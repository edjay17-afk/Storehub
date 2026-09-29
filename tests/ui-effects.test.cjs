const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(){
 const nodes=[];
 function node(){const attrs=new Map(),classes=new Set();return {innerHTML:'',textContent:'',children:[],removed:false,attrs,classes,
  classList:{add:c=>classes.add(c),remove:c=>classes.delete(c)},setAttribute:(k,v)=>attrs.set(k,v),getAttribute:k=>attrs.has(k)?attrs.get(k):null,removeAttribute:k=>attrs.delete(k),
  append(child){this.children.push(child);},remove(){this.removed=true;},querySelector(){return {textContent:''};},getAnimations(){return [];},animate(){this.animated=true;}};}
 const context={window:{matchMedia:()=>({matches:false})},WeakMap,Map,Promise,Error,document:{createElement(){const n=node();nodes.push(n);return n;},head:node()},fetch:async()=>({ok:true,json:async()=>({records:[]})})};
 vm.createContext(context);vm.runInContext(fs.readFileSync('static/ui-effects.js','utf8'),context);
 return {context,node,nodes,ui:context.window.WarehouseUI};
}
test('overlapping loads keep the busy indicator until all requests finish',()=>{
 const s=setup(),element=s.node();const a=s.ui.loading(element),b=s.ui.loading(element);
 assert.equal(s.nodes.length,1);assert.equal(element.getAttribute('aria-busy'),'true');
 a();a();assert.equal(element.getAttribute('aria-busy'),'true');b();
 assert.equal(element.getAttribute('aria-busy'),null);assert.ok(s.nodes[0].removed);assert.equal(element.classes.size,0);
});
test('failed JSON requests clear placeholders and preserve the previous busy attribute',async()=>{
 const s=setup(),element=s.node();element.setAttribute('aria-busy','false');
 s.context.fetch=async()=>({ok:false,json:async()=>({error:'Offline'})});
 await assert.rejects(s.ui.json('/records',{},element),/Offline/);
 assert.equal(element.getAttribute('aria-busy'),'false');assert.ok(s.nodes[0].removed);
});
test('lazy scripts share one request and retry after a failed load',async()=>{
 const s=setup(),first=s.ui.script('/static/decoder.js');assert.equal(s.ui.script('/static/decoder.js'),first);
 s.nodes[0].onerror();await assert.rejects(first,/Could not load/);
 const retry=s.ui.script('/static/decoder.js');assert.equal(s.nodes.length,2);s.nodes[1].onload();await retry;
});
test('reduced-motion preference skips animated page transitions',()=>{
 const s=setup(),element=s.node();s.context.window.matchMedia=()=>({matches:true});s.ui.enter(element);assert.equal(element.animated,undefined);
});
