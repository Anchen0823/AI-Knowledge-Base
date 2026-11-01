// Exercise graph visibility against the actual frontend functions without a browser.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=fs.readFileSync('snakekb/static/app.js','utf8');
const enabled=source.slice(source.indexOf('  function edgeEnabled('),source.indexOf("  const stage =",source.indexOf('  function edgeEnabled(')));
const draw=source.slice(source.indexOf('  function draw()'),source.indexOf('  async function load(',source.indexOf('  function draw()')));
function element(){return {style:{},setAttribute(){},classList:{toggle(){}},querySelector(){return this.text;},text:{style:{},textContent:'label'}};}
const nodes=['a','b','c','s'].map((id,i)=>({id,x:i*100,y:100,kind:id==='s'?'source':'concept',group:element()}));
const edges=[{source:'a',target:'b',basis:'原文关系'},{source:'a',target:'c',kind:'语义相近'},{source:'s',target:'a',kind:'来源'}].map(e=>({...e,line:element()}));
const context={nodes,edges,world:element(),zoom:1,tx:0,ty:0,hover:null,selected:null,display:{sources:false,semantic:false,local:false,labels:false}};
vm.createContext(context);vm.runInContext(enabled+draw+'draw();',context);
assert.equal(edges[0].line.style.display,'');
assert.equal(edges[1].line.style.display,'none');
assert.equal(edges[2].line.style.display,'none');
context.selected='a';context.display.local=true;vm.runInContext('draw()',context);
assert.equal(edges[2].line.style.display,'');
assert.equal(nodes[2].group.style.display,'none');
assert.equal(nodes[1].group.style.display,'');
context.display.semantic=true;vm.runInContext('draw()',context);
assert.equal(nodes[2].group.style.display,'');
context.selected=null;vm.runInContext('draw()',context);
assert.ok(nodes.every(n=>n.group.style.display===''));
console.log('graph visibility: overview, selected neighbours, semantic toggle, deselection passed');
