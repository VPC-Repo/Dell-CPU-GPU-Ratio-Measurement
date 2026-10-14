/* Panel A behaviour on a built replay page, in a source-level DOM harness (not a visual browser test).

     node ui/replay/check-panel-a.cjs [replay.html]

   With no argument it builds the page from bench/tests/fixtures/results-r770-16101.json.
   Expectations come from the page's own embedded results, so any server's page can be checked. */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const model=require('./simulation-model.cjs');

function embedded(html,name){return JSON.parse(html.match(new RegExp('const '+name+' = (.*);'))[1]);}

function check(html){
  const RESULTS=embedded(html,'RESULTS'),SIM_PROFILE=embedded(html,'SIM_PROFILE');
  const sim=model.create(SIM_PROFILE,RESULTS),draft=model.mix(RESULTS,sim.times);
  const expect=n=>{draft.setTarget(n);return sim.calculate(draft.snapshot().counts);};
  const short=v=>String(v||'').replace(/^Dell(?: Inc\.?)? (?=PowerEdge)/,'').trim();
  const cpuName=short(RESULTS.platform.system)||'This server',gpuName=short(RESULTS.serving.gpuServer)||'GPU server';
  const nodes=new Map(),listeners=new Map(),timers=[];let now=0;
  class Element{
    constructor(id){this.id=id;this.style={setProperty(){}};this.dataset={};this.value='';this.hidden=false;this.open=false;this.textContent='';this.classList={add(){},toggle(){}};this.listeners={};}
    set innerHTML(value){this.html=value;for(const match of value.matchAll(/\bid="([^"]+)"/g))if(!nodes.has(match[1]))nodes.set(match[1],new Element(match[1]));}
    get innerHTML(){return this.html||'';}
    setAttribute(name,value){this.attributes??={};this.attributes[name]=value;} querySelectorAll(){return [];} addEventListener(name,fn){this.listeners[name]=fn;} setCustomValidity(){} focus(){} replaceChildren(){this.innerHTML='';} showModal(){this.open=true;} close(){this.open=false;}
  }
  for(const m of html.matchAll(/\bid="([^"]+)"/g))nodes.set(m[1],new Element(m[1]));
  const exchange=new Element('exchange');
  const document={documentElement:new Element('html'),hidden:false,activeElement:null,getElementById:id=>nodes.get(id)||null,querySelectorAll:()=>[],querySelector:s=>s==='.exchange'?exchange:null,addEventListener:(n,f)=>{const prior=listeners.get(n);listeners.set(n,prior?e=>{prior(e);f(e);}:f);}};
  const context=vm.createContext({document,window:{},innerWidth:1440,innerHeight:900,addEventListener(){},matchMedia:()=>({matches:false,addEventListener(){}}),performance:{now:()=>now},setInterval:fn=>timers.push(fn),console});
  vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],context);
  const api=context.window.__panelA,advance=()=>{for(let i=0;i<120;i++){now+=100;timers.forEach(f=>f());}};
  const visible=()=>[...nodes.values()].map(n=>n.textContent+' '+n.innerHTML).join(' ').replace(/data:[^"]+/g,'data:');
  assert.equal(api.snapshot().result.total,1000);assert.equal(api.snapshot().result.cpuSystems,expect(1000).cpuSystems);
  assert.equal(api.snapshot().result.sockets,expect(1000).cpuSystems*RESULTS.platform.sockets,'sockets follow platform.sockets');
  assert.equal(nodes.get('cpu-server-name').textContent,cpuName);assert.equal(nodes.get('gpu-server-name').textContent,gpuName);
  const initial=api.snapshot().result,openingPreset=api.snapshot().preset;
  api.preset('Development team');assert(api.snapshot().transition);advance();assert.equal(api.snapshot().result.total,1000);assert.notEqual(api.snapshot().result.counts[4],initial.counts[4]);
  function step(i,delta){listeners.get('click')({target:{closest:s=>s==='[data-step]'?{dataset:{step:String(i),delta:String(delta)}}:null}});}
  step(0,5);assert.equal(api.snapshot().draft.valid,false);advance();assert.equal(api.snapshot().result.total,1000);
  step(4,-5);advance();const custom=Array.from(api.snapshot().draft.weights);assert.equal(api.snapshot().preset,'Custom');
  api.preset('Enterprise');advance();api.preset('Custom');advance();assert.deepEqual(Array.from(api.snapshot().draft.weights),custom);
  const target=nodes.get('target');target.value='0';target.listeners.input({target});advance();assert.equal(api.snapshot().result.cpuSystems,0);assert.equal(api.snapshot().result.gpuSystems,0);assert.equal(api.snapshot().cpu,0);assert(nodes.get('cpu-photos').innerHTML.includes('No systems allocated'));
  target.value='100000';target.listeners.input({target});advance();assert.equal(api.snapshot().result.total,100000);assert(Number.isFinite(api.snapshot().memory));
  assert.equal(api.snapshot().result.gpuSystems,Math.ceil((api.snapshot().result.gpuEquivalent-1e-9)/RESULTS.serving.gpusPerServer),'GPU servers follow serving.gpusPerServer');
  assert(nodes.get('installed-capacity').textContent.endsWith(` and ${api.snapshot().result.gpuSystems*RESULTS.serving.gpusPerServer} GPUs installed`));
  nodes.get('reset').onclick();advance();assert.equal(api.snapshot().result.total,1000);
  // The bottom box shows the selected agent's stages as chips and the current stage's steps, and keeps animating.
  listeners.get('click')({target:{closest:s=>s==='[data-agent]'?{dataset:{agent:'1'}}:null}});
  let chips=nodes.get('worker-sequence').innerHTML;assert(chips.includes('>Research<')&&chips.includes('>Writing<')&&chips.includes('>Finalize<'));
  advance();advance();assert.notEqual(nodes.get('worker-sequence').innerHTML,chips,'Stages advance with the tour stopped');
  api.preset('Development team');assert(nodes.get('flow-title').textContent.startsWith('Code'),'Development team shows Code');
  api.preset('Document operations');assert(nodes.get('flow-title').textContent.startsWith('Ingestion'));
  api.preset('Service desk');assert(nodes.get('flow-title').textContent.startsWith('Task'));assert(!nodes.get('worker-sequence').innerHTML.includes('Finalize'));
  const beforeTheme=Array.from(api.snapshot().draft.weights);
  nodes.get('theme-dark').onclick();assert.equal(document.documentElement.attributes['data-theme'],'dark');
  nodes.get('theme-light').onclick();assert.equal(document.documentElement.attributes['data-theme'],'light');
  assert.deepEqual(Array.from(api.snapshot().draft.weights),beforeTheme);
  nodes.get('reset').onclick();advance();
  function choose(i,value){nodes.get('rows').listeners.change({target:{dataset:{workflow:String(i)},value:String(value),matches:s=>s==='[data-workflow]'}});}
  const w0=Array.from(api.snapshot().draft.weights);choose(0,1);advance();assert.equal(api.snapshot().draft.weights[0],w0[1]);assert.equal(api.snapshot().draft.weights[1],w0[0]);assert.equal(api.snapshot().draft.valid,true);
  assert(nodes.get('rows').innerHTML.indexOf('id="row-1"')<nodes.get('rows').innerHTML.indexOf('id="row-0"'));
  const customRows=nodes.get('rows').innerHTML.match(/id="row-\d"/g);
  api.preset('Enterprise');advance();api.preset('Custom');advance();assert.deepEqual(nodes.get('rows').innerHTML.match(/id="row-\d"/g),customRows);
  nodes.get('reset').onclick();advance();choose(4,'remove');assert.equal(api.snapshot().draft.included[4],false);assert.equal(api.snapshot().draft.valid,false);
  choose(0,4);assert.equal(api.snapshot().draft.included[0],false);assert.equal(api.snapshot().draft.included[4],true);assert.equal(api.snapshot().draft.weights[4],w0[0]);advance();assert.equal(api.snapshot().result.total,1000);
  nodes.get('reset').onclick();advance();
  // The sub-head names this server and the GPU server, sized from this server's measurements; one server is singular.
  target.value='100';target.listeners.input({target});advance();
  const small=expect(100),plural=(n,w)=>`${n} ${w}${n===1?'':'s'}`;
  assert.equal(nodes.get('result-sentence').textContent,`100 working agents need ${plural(small.cpuSystems,cpuName+' server')} and ${plural(small.gpuSystems,gpuName)}.`);
  nodes.get('reset').onclick();advance();
  const big=expect(1000);assert(nodes.get('result-sentence').textContent.endsWith(`${plural(big.cpuSystems,cpuName+' server')} and ${plural(big.gpuSystems,gpuName)}.`));
  assert(!/NaN|undefined(?!-behavior)|Infinity/.test(visible()),'no NaN, undefined or Infinity in rendered text');
  assert(!/\/\*__[A-Z_]+__\*\//.test(html));assert(html.includes('workers per agent'));assert(html.includes('CPU sockets to GPUs'));
  return {cpuSystems:big.cpuSystems,gpuSystems:big.gpuSystems,openingPreset};
}

module.exports={check,embedded};
if(require.main===module){
  let html;
  if(process.argv[2])html=fs.readFileSync(process.argv[2],'utf8');
  else{const root=path.resolve(__dirname,'../..');html=require('./build.cjs').build(JSON.parse(fs.readFileSync(path.join(root,'bench/tests/fixtures/results-r770-16101.json'),'utf8')),JSON.parse(fs.readFileSync(path.join(root,'calibration/reference-r770.json'),'utf8')));}
  const r=check(html);
  console.log(`PASS: Panel A (1,000 agents: ${r.cpuSystems} CPU and ${r.gpuSystems} GPU servers); presets, saved Custom, incomplete drafts, transitions, zero and large fleets, reset, stage chips, light/dark, workflow swaps and removal, data-driven names and counts. Source-level DOM harness, not a visual browser test.`);
}
