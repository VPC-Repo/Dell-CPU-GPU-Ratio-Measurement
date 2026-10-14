#!/usr/bin/env node
/* Build the replay page for one server from its results.

     node ui/replay/build.cjs <results.json> <out.html> [reference.json]

   results.json is the kit's results (bench/runner/results.py). The page is one
   self-contained file: fonts, logos and product pictures are embedded, and it
   needs no network. Served by the kit, it also reads /calibration.json at load
   and resizes the GPUs from it, so editing calibration.yaml and reloading
   changes the GPU numbers without a new build.

   From this server's results: platform (system, processors, sockets, cores,
   memory, core pools), the capacity point (resident agents, CPU %, memory,
   output tokens/s, calls/s, busy cores, CPU shares), per-agent model calls,
   output tokens and CPU, the sandbox CPU per workflow of each agent type (its
   CPU breakdown over its arrival rate), workflow lifetimes and the activity
   trace. Reference constants (simulation-profile.json): service cost laws,
   the analyst job size and memory share, planning targets. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const here = __dirname;
const read = f => fs.readFileSync(path.join(here, f), 'utf8');
const json = o => JSON.stringify(o).replace(/</g, '\\u003c');

function steps() {
  // agent-steps.js declares `const defs=[...]`: evaluate it once to read names and workers.
  return new Function(read('agent-steps.js') + '\nreturn defs;')();
}

const COLORS = {Task: '#0672CB', Research: '#0B7C84', Ingestion: '#00468B', Analyst: '#40586D', Code: '#66278F'};
const LABELS = {Task: 'retrieve, record, and reply', Research: 'retrieve and synthesize', Ingestion: 'OCR, embed, and index',
  Analyst: 'analyze and explain', Code: 'set up, CI, and verify'};
// Sandboxed job steps in the CPU breakdown, by the agent type that runs them (service pieces excluded).
const JOB_GROUPS = {'Data analysis': 'Analyst', 'Builds and tests': 'Code', 'Document intake': 'Ingestion', Research: 'Research'};
const SPECS = {
  cpu: [/\bR770\b/, 'https://www.delltechnologies.com/asset/en-in/products/servers/technical-support/poweredge-r770-spec-sheet.pdf', 'r770'],
  gpu: [/\bXE7740\b/, 'https://www.dell.com/en-us/shop/ipovw/poweredge-xe7740', 'xe7740'],
};

function num(v, what) {
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`results.json: ${what} is missing or not a number`);
  return n;
}

function build(kit, reference) {
  const defs = steps();
  const profile = JSON.parse(read('simulation-profile.json'));
  const allAssets = JSON.parse(read('assets.json'));
  const P = kit.platform || {}, C = kit.capacity || {}, S = kit.serving || {};
  const tile = (kit.definitions || {}).tile || {};
  const byName = Object.fromEntries((kit.archetypes || []).map(a => [a.name, a]));
  // An agent type the measured mix left out (tile 0) stands in with the published reference figures.
  const unmeasured = defs.map(d => d.name).filter(n => !byName[n] || !(tile[n] > 0));
  if (unmeasured.length === defs.length) throw new Error('results.json: no agent type in the measured mix');
  const refAgent = Object.fromEntries(profile.referenceAgents.map(a => [a.name, a]));

  const sockets = Math.max(1, Math.round(num(P.sockets, 'platform.sockets')));
  const busyCores = Number.isFinite(C.busyCores) ? C.busyCores : num(C.coreMsPerSecond, 'capacity.coreMsPerSecond') / 1000;
  const wfs = num(C.workflowsPerSecond, 'capacity.workflowsPerSecond');
  const share = n => unmeasured.includes(n) ? 0 : tile[n];
  const tileTotal = defs.reduce((a, d) => a + share(d.name), 0);

  const platform = {system: P.system || 'This server', processors: P.processors || '', sockets,
    cores: num(P.cores, 'platform.cores'), threads: P.threads, memoryGB: num(P.memoryGB, 'platform.memoryGB'),
    allocation: P.allocation || {}};
  const capacity = {workflowsPerSecond: wfs, busyCores, resident: num(C.resident, 'capacity.resident'),
    cpuPercent: num(C.cpuPercent, 'capacity.cpuPercent'), memoryGB: num(C.memoryGB, 'capacity.memoryGB'),
    memoryPeakGB: C.memoryPeakGB, callsPerSecond: num(C.callsPerSecond, 'capacity.callsPerSecond'),
    outputTokensPerSecond: num(C.outputTokensPerSecond, 'capacity.outputTokensPerSecond'),
    coreMsPerSecond: Number.isFinite(C.coreMsPerSecond) ? C.coreMsPerSecond : Math.round(busyCores * 1000),
    cpuShares: {sandbox: 0, retrieval: 0, execution: 0, support: 0, ...(C.cpuShares || {})}};
  if (!(capacity.cpuShares.sandbox + capacity.cpuShares.execution > 0)) throw new Error('results.json: capacity.cpuShares has no sandbox or execution share');
  const archetypes = defs.map(d => {
    const a = unmeasured.includes(d.name) ? refAgent[d.name] : byName[d.name];
    return {name: d.name, coreMs: num(a.coreMs, `${d.name} coreMs`), modelCalls: num(a.modelCalls, `${d.name} modelCalls`),
      outputTokens: num(a.outputTokens, `${d.name} outputTokens`), label: LABELS[d.name], color: COLORS[d.name]};
  });
  const serving = {model: S.model || '', gpu: S.gpu || 'GPU', gpuServer: S.gpuServer || '',
    gpusPerServer: Math.max(1, Math.round(num(S.gpusPerServer, 'serving.gpusPerServer'))),
    outputTokensPerSecondPerGpu: num(S.outputTokensPerSecondPerGpu, 'serving.outputTokensPerSecondPerGpu')};

  // Sandbox CPU per workflow, by agent type: this server's job share of its busy cores over the type's arrival rate.
  const jobShare = Object.fromEntries(defs.map(d => [d.name, 0]));
  for (const b of C.cpuBreakdown || []) {
    const who = JOB_GROUPS[b.group];
    if (who && !/service/i.test(b.piece || '')) jobShare[who] += Number(b.share) || 0;
  }
  const sandboxFromServer = Object.values(jobShare).some(v => v > 0);
  if (sandboxFromServer) {
    profile.costs.forEach(c => {
      if (unmeasured.includes(c.name)) return;       // keeps the reference constant
      const rate = wfs * share(c.name) / tileTotal;
      c.sandboxCoreSeconds = Number((jobShare[c.name] / 100 * busyCores / rate).toFixed(2));
    });
  }

  // Workflow lifetimes at the capacity step (completed workflows submitted 300 s or more into it).
  const timings = ((kit.replay || {}).timings || []);
  profile.timings = defs.map(d => {
    if (unmeasured.includes(d.name)) return {name: d.name, samples: 0, meanSeconds: refAgent[d.name].meanSeconds, reference: true};
    const t = timings.find(x => x.name === d.name);
    // No completed workflow of this type in the window (only in a short test hold): use the reference lifetime, labelled.
    if (!t || !(t.meanSeconds > 0)) return {name: d.name, samples: 0, meanSeconds: refAgent[d.name].meanSeconds, reference: true};
    return {name: d.name, samples: t.samples, meanSeconds: t.meanSeconds};
  });

  // Host CPU and memory over the judged window as factors of their means, [seconds, cpu, memory].
  let trace = ((kit.replay || {}).trace || []).filter(r => Array.isArray(r) && r.length >= 3 && r.every(Number.isFinite));
  if (trace.length < 2) trace = [[0, 1, 1], [10, 1, 1]];

  const cpuProduct = SPECS.cpu[0].test(platform.system);
  const gpuProduct = SPECS.gpu[0].test(serving.gpuServer);
  const intel = /intel/i.test(platform.processors), xeon = /xeon/i.test(platform.processors);
  const assets = {dell: allAssets.dell, server: allAssets.server, gpuServer: allAssets.gpuServer};
  if (intel) assets.intel = allAssets.intel;
  if (xeon || gpuProduct) assets.xeon = allAssets.xeon;
  if (cpuProduct) assets.cpuProduct = allAssets[SPECS.cpu[2]];
  if (gpuProduct) assets.gpuProduct = allAssets[SPECS.gpu[2]];

  const results = {definitionVersion: profile.version, platform, capacity, archetypes, serving,
    definitions: {tile: Object.fromEntries(defs.map(d => [d.name, share(d.name)])), mix: (kit.definitions || {}).mix || '',
      workers: Object.fromEntries(defs.map(d => [d.name, d.workers.length]))},
    kit: {run: (kit.kit || {}).run, step: (kit.kit || {}).step, calibration_hash: (kit.kit || {}).calibration_hash,
      memory_bound: !!(kit.kit || {}).memory_bound, bracketed: (kit.kit || {}).bracketed}};
  const page = {intel, xeon, cpuSpecs: cpuProduct ? SPECS.cpu[1] : '', gpuSpecs: gpuProduct ? SPECS.gpu[1] : '',
    gpuProductName: gpuProduct ? serving.gpuServer : '', referenceSystem: reference && reference.system || '', sandboxFromServer, unmeasured};
  if (unmeasured.includes('Analyst')) profile.analystMemoryShare = 0;   // no analysts measured: the whole footprint is the per-server base

  const name = platform.system.replace(/^Dell(?: Inc\.?)? (?=PowerEdge)/, '');
  let html = read('panel-a.src.html');
  const fill = (key, value) => {
    if (!html.includes(key)) throw new Error(`Placeholder ${key} missing from panel-a.src.html`);
    html = html.replace(key, () => value);
  };
  fill('/*__TITLE__*/', `${name} agent capacity replay`.replace(/[<&]/g, ''));
  fill('/*__FONTS__*/', read('fonts.css'));
  fill('/*__DETAIL_CSS__*/', read('detail-views.css'));
  fill('/*__PANEL_CSS__*/', read('panel-a.css'));
  fill('/*__RESULTS__*/null', json(results));
  fill('/*__ASSETS__*/null', json(assets));
  fill('/*__SIM_PROFILE__*/null', json(profile));
  fill('/*__TRACE__*/null', json(trace));
  fill('/*__PAGE__*/null', json(page));
  fill('/*__SIM_MODEL__*/', read('simulation-model.cjs'));
  fill('/*__DEFS__*/', read('agent-steps.js'));
  fill('/*__DETAIL_UI__*/', read('detail-views.js'));
  fill('/*__PANEL_UI__*/', read('panel-a.js'));
  if (/\/\*__[A-Z_]+__\*\//.test(html)) throw new Error('Unfilled placeholder in the replay page');
  // The page's own model must accept these results before the page ships.
  require('./simulation-model.cjs').create(profile, results).calculate([1, 1, 1, 1, 1]);
  return html;
}

if (require.main === module) {
  const [resultsPath, outPath, referencePath] = process.argv.slice(2);
  if (!resultsPath || !outPath) {
    console.error('usage: node ui/replay/build.cjs <results.json> <out.html> [reference.json]');
    process.exit(2);
  }
  const kit = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
  const reference = referencePath && fs.existsSync(referencePath) ? JSON.parse(fs.readFileSync(referencePath, 'utf8')) : null;
  const html = build(kit, reference);
  fs.mkdirSync(path.dirname(path.resolve(outPath)), {recursive: true});
  const tmp = outPath + '.tmp';
  fs.writeFileSync(tmp, html);
  fs.renameSync(tmp, outPath);
  console.log(`Built ${outPath} for ${kit.platform.system} (${(html.length / 1024).toFixed(0)} KB).`);
}

module.exports = {build};
