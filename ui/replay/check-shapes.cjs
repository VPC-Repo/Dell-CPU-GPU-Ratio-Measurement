/* The replay page carries no reference-server assumption: build it for server shapes other than the
   reference and check that names, counts and pictures follow the results.

     node ui/replay/check-shapes.cjs

   Shapes, all derived from bench/tests/fixtures/results-r770-16101.json:
   - the fixture itself (two sockets, 172 cores, the reference server's own picture)
   - one socket, 64 cores, 512 GB, a non-Intel processor, the fixture's GPU server
   - the same server with a different GPU server (4 GPUs per server), memory-bound, every step kept up */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {build}=require('./build.cjs');
const panel=require('./check-panel-a.cjs'),details=require('./check-details.cjs');
const root=path.resolve(__dirname,'../..');
const fixture=JSON.parse(fs.readFileSync(path.join(root,'bench/tests/fixtures/results-r770-16101.json'),'utf8'));
const reference=JSON.parse(fs.readFileSync(path.join(root,'calibration/reference-r770.json'),'utf8'));
const refName=reference.system.replace(/^PowerEdge /,'');   // the reference model's short name

function oneSocket(){
  const r=structuredClone(fixture),f=59/154;                  // application cores, this shape over the fixture's
  r.kit.run='synthetic-1s-64c';r.kit.instances=3;
  r.platform={system:'Example Systems S1-64',processors:'1 x AMD EPYC 9535 64-Core Processor',sockets:1,cores:64,threads:128,memoryGB:512,
    allocation:{reranking:3,queryEmbedding:1,ingestEmbedding:1,application:59}};
  const c=r.capacity;
  Object.assign(c,{workflowsPerSecond:+(c.workflowsPerSecond*f).toFixed(3),busyCores:+(c.busyCores*f).toFixed(1),resident:Math.round(c.resident*f),
    memoryGB:Math.round(c.memoryGB*f),memoryPeakGB:Math.round(c.memoryPeakGB*f),callsPerSecond:+(c.callsPerSecond*f).toFixed(1),
    outputTokensPerSecond:Math.round(c.outputTokensPerSecond*f/10)*10});
  c.cpuPercent=Math.round(100*c.busyCores/64);c.coreMsPerSecond=Math.round(c.busyCores*1000);c.busyThreads=Math.round(c.busyThreads*f);
  return r;
}
function textOf(html){return html.replace(/data:[^"')]+/g,'data:');}

// 1. The fixture: the reference server's own picture and specification link.
{
  const html=build(fixture,reference);
  const page=panel.embedded(html,'PAGE'),assets=panel.embedded(html,'ASSETS');
  assert.ok(assets.cpuProduct&&assets.gpuProduct&&assets.intel&&assets.xeon,'reference server pictures and Intel marks');
  assert.ok(page.cpuSpecs.includes(refName.toLowerCase()));
  panel.check(html);details.check(html);
}
// 2. One socket, 64 cores, no reference file: no reference-server name anywhere in the page.
const shape=oneSocket();
for(const ref of [null,reference]){
  const html=build(shape,ref),text=textOf(html);
  const results=panel.embedded(html,'RESULTS'),page=panel.embedded(html,'PAGE'),assets=panel.embedded(html,'ASSETS');
  assert.equal(results.platform.sockets,1);assert.equal(results.platform.cores,64);
  assert.ok(!assets.cpuProduct&&!assets.intel,'generic server picture, no Intel logo for a non-Intel processor');
  assert.ok(!page.intel&&!page.xeon&&page.cpuSpecs==='');
  const r=panel.check(html),views=details.check(html);
  assert.ok(!/NaN|undefined(?!-behavior)|Infinity/.test(views));
  assert.ok(views.includes('Example Systems S1-64'),'the server is named by its own system name');
  assert.ok(views.includes('1 x AMD EPYC 9535'),'processor from the results');
  assert.ok(views.includes('>64<')&&views.includes('512 GB'),'cores and memory per server from the results');
  if(!ref){
    assert.ok(!html.includes(refName),`no "${refName}" anywhere in the page (including scripts and embedded data)`);
    assert.ok(!/r770/i.test(text),'no reference-server name in any case');
  }else{
    // With the reference file the method view names it once, as the source of the reference constants.
    assert.equal((html.match(new RegExp(refName,'g'))||[]).length,1,'reference named only as the source of the reference constants');
    assert.ok(views.includes(`from the published ${reference.system} results`));
  }
  console.log(`  one socket, 64 cores${ref?', with reference':''}: 1,000 agents need ${r.cpuSystems} servers and ${r.gpuSystems} GPU server${r.gpuSystems===1?"":"s"}`);
}
// 3. Another GPU server (4 GPUs each), memory-bound, every step kept up.
{
  const r=oneSocket();
  Object.assign(r.serving,{gpuServer:'Example GPU Server G4',gpusPerServer:4,gpu:'Example GPU'});
  r.kit.memory_bound=true;r.kit.bracketed=false;
  const html=build(r,null),assets=panel.embedded(html,'ASSETS');
  assert.ok(!assets.gpuProduct&&!assets.xeon,'generic GPU server picture, no Xeon mark');
  const counts=panel.check(html),views=details.check(html);
  assert.ok(views.includes('Example GPU Server G4')&&views.includes('>4<'),'GPU server name and positions from serving');
  assert.ok(views.includes('memory-bound')&&views.includes('capacity is at least this rate'));
  assert.ok(!textOf(html).match(/XE7740/),'no fixture GPU server name when serving names another');
  console.log(`  another GPU server, 4 GPUs each: ${counts.gpuSystems} GPU servers for 1,000 agents`);
}
// 4. A mix that leaves an agent type out (no analysts): it opens on the measured mix and sizes analysts from reference figures.
{
  const r=oneSocket();
  r.archetypes=r.archetypes.filter(a=>a.name!=='Analyst');r.definitions={tile:{Task:6,Research:1,Ingestion:1,Code:1},mix:'Smoke (no analyst)'};
  r.replay.timings=r.replay.timings.filter(t=>t.name!=='Analyst');
  r.capacity.cpuBreakdown=r.capacity.cpuBreakdown.filter(b=>b.group!=='Data analysis');
  const html=build(r,null),results=panel.embedded(html,'RESULTS'),page=panel.embedded(html,'PAGE');
  assert.deepEqual(page.unmeasured,['Analyst']);assert.equal(results.definitions.tile.Analyst,0);
  const counts=panel.check(html),views=details.check(html);
  assert.ok(views.includes('Analyst was not in the measured mix'));
  assert.equal(counts.openingPreset,'Smoke (no analyst) (measured)','the measured mix opens the page');
  console.log(`  mix without analysts: ${counts.cpuSystems} servers for 1,000 agents of the measured mix`);
}
console.log('PASS: replay pages for the fixture, a one-socket 64-core server (with and without the reference file), another GPU server and a mix without analysts; names, sockets, cores, memory, GPUs per server and pictures follow the results; no NaN or undefined.');
