/* Every detail view of a built replay page renders without gaps.

     node ui/replay/check-details.cjs [replay.html]

   With no argument it builds the page from bench/tests/fixtures/results-r770-16101.json. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const model=require('./simulation-model.cjs');
const {embedded}=require('./check-panel-a.cjs');

function check(html){
  const RESULTS=embedded(html,'RESULTS'),SIM_PROFILE=embedded(html,'SIM_PROFILE'),PAGE=embedded(html,'PAGE'),ASSETS=embedded(html,'ASSETS');
  const simulator=model.create(SIM_PROFILE,RESULTS),mix=model.mix(RESULTS,simulator.times).snapshot();
  const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{classList:{add(){},toggle(){}},innerHTML:'',textContent:'',hidden:false});return nodes.get(id);};
  const context=vm.createContext({RESULTS,SIM_PROFILE,PAGE,simulator,simResult:simulator.calculate(mix.counts),simCounts:mix.counts,appliedMix:mix,C:RESULTS.capacity,S:RESULTS.serving,ASSETS,motion:true,reduced:{matches:false},$:node,decimal:n=>n.toFixed(1),compact:n=>n.toFixed(0)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'agent-steps.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'detail-views.js'),'utf8'),context);
  const pages=[];
  for(const key of ['agent:0','agent:1','agent:2','agent:3','agent:4','worker:3:1','stage:4:0:2','cpu-server','gpu-server','cpu','cpu-share','cpu-reference','serving','llm','memory','capacity','residency','architecture','methodology']){
    vm.runInContext(`renderDetailView(${JSON.stringify(key)})`,context);
    const content=node('topic-body').innerHTML.replace(/data:[^"]+/g,'data:'),title=node('topic-title').textContent;
    pages.push(title+' '+content);
    assert.ok(content.length>150,key);assert.ok(!/undefined(?!-behavior)|NaN|Infinity/.test(content+title),key);
    if(key.startsWith('agent:'))assert.ok(content.includes('dv-flow')&&content.includes((PAGE.unmeasured||[]).includes(RESULTS.archetypes[+key.split(':')[1]].name)?'reference figure':'measured in the mix'));
    if(key==='cpu-server'){assert.ok(content.includes('dv-product'));assert.equal(content.includes('Intel Xeon'),!!PAGE.xeon);assert.ok(content.includes(`>${RESULTS.platform.cores}<`),'cores per server');assert.ok(content.includes(RESULTS.platform.memoryGB.toLocaleString('en-US')+' GB'),'memory per server, not doubled');}
    if(key==='gpu-server'){assert.ok(content.includes('dv-product'));assert.ok(content.includes(`>${RESULTS.serving.gpusPerServer}<`),'GPU positions per server');}
    if(key==='cpu'){assert.ok(content.includes('dv-sweep')&&content.includes('Core pools, this deployment'));assert.ok(content.includes(`>${context.simResult.cpuSystems*RESULTS.platform.cores}<`),'cores across the deployment');}
    if(key==='memory')assert.ok(content.includes(`${(context.simResult.cpuSystems*RESULTS.platform.memoryGB).toFixed(0)} GB</strong><span>configured deployment capacity`),'configured memory is servers × memory per server');
    if(key==='methodology'){assert.ok(content.includes('Model boundaries and provenance'));assert.ok(content.includes("This server's measurements and reference constants"));assert.ok(content.includes(String(RESULTS.capacity.resident)));}
  }
  context.simResult=simulator.calculate([0,0,0,0,0]);
  for(const key of ['cpu','memory','serving','capacity','cpu-server','gpu-server']){vm.runInContext(`renderDetailView('${key}')`,context);const body=node('topic-body').innerHTML.replace(/data:[^"]+/g,'data:');assert.ok(!/NaN|Infinity|undefined(?!-behavior)/.test(body),key);pages.push(body);}
  assert.ok(!html.includes('/*__DETAIL_'));assert.ok(html.includes('data-topic="cpu-server"'));assert.ok(html.includes('data-topic="gpu-server"'));
  return pages.join('\n');
}

module.exports={check};
if(require.main===module){
  let html;
  if(process.argv[2])html=fs.readFileSync(process.argv[2],'utf8');
  else{const root=path.resolve(__dirname,'../..');html=require('./build.cjs').build(JSON.parse(fs.readFileSync(path.join(root,'bench/tests/fixtures/results-r770-16101.json'),'utf8')),JSON.parse(fs.readFileSync(path.join(root,'calibration/reference-r770.json'),'utf8')));}
  check(html);
  console.log('PASS: all agent, server, CPU, memory, model-serving, capacity and methodology views; per-server cores and memory not doubled; zero-load handling; server routing.');
}
