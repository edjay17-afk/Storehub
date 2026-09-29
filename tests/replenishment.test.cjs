const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(suggestions=[]){
 const elements=new Map(),requests=[];let fail=false,viewed='';
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,options:[{value:''},{value:'Branch A'},{value:'Branch B'}]});return elements.get(id);};
 $('kind').onchange=()=>{};$('rp-branch').value='Branch A';
 const products=suggestions.map(r=>({id:r.productId,Cost:'2.00',SKU:'SKU-'+r.productId,Barcode:'012345'}));
 const state={suggestions,locations:['Warehouse','Branch A','Branch B'],interval:15,configured:true,lastSync:'2026-09-29T00:00:00Z',warnings:[],baselinePending:0};
 const context=vm.createContext({$,esc:String,data:{products,warehouse:'Warehouse'},lines:[],document:{querySelectorAll:()=>[],body:{dataset:{section:'catalog'}}},window:{scrollTo(){}},setInterval(){},fetch:async(url,options)=>{requests.push({url,options});return {ok:!fail,json:async()=>fail?{error:'Offline'}:state};},load:async()=>{},renderLines(){},view(id){viewed=id;},message(){}});
 vm.runInContext(fs.readFileSync('static/replenishment.js','utf8'),context);
 return {$,context,requests,state,viewed:()=>viewed,fail:()=>fail=true};
}
const row=(id,location='Branch A',suggested='3')=>({productId:id,name:'Rice '+id,location,onHand:'0',target:'5',pending:'2',needed:'3',suggested,sold30Days:'10',basis:'Ideal stock'});
test('filters support branch, barcode and transfer availability',()=>{
 const h=setup([row('a'),row('b','Branch B','0')]);
 const results=vm.runInContext("rpFilter("+JSON.stringify(h.state.suggestions)+",'Branch A','012345','ready',rpProducts())",h.context);
 assert.equal(results.length,1);assert.equal(results[0].productId,'a');
});
test('draft refreshes, includes all available branch lines across pages, and performs no POST',async()=>{
 const h=setup([...Array.from({length:25},(_,i)=>row(String(i))),row('blocked','Branch A','0'),row('other','Branch B')]);
 await vm.runInContext('draftReplenishment()',h.context);
 assert.equal(h.context.lines.length,25);assert.equal(h.$('kind').value,'transfer');assert.equal(h.$('target').value,'Branch A');assert.equal(h.viewed(),'requests');
 assert.ok(h.requests.every(r=>!r.options.method));assert.equal(h.context.lines[0].cost,'2.00');
});
test('existing request is preserved and a failed refresh never drafts stale suggestions',async()=>{
 const h=setup([row('a')]);h.context.lines=[{product_id:'existing',quantity:'1'}];
 await assert.rejects(vm.runInContext('draftReplenishment()',h.context),/current product request/);assert.equal(h.context.lines[0].product_id,'existing');
 h.context.lines=[];await vm.runInContext('loadReplenishment()',h.context);h.fail();
 await assert.rejects(vm.runInContext('draftReplenishment()',h.context),/Offline/);assert.equal(h.context.lines.length,0);
});
test('render paginates at 20 and disables branch drafting with no available stock',async()=>{
 const h=setup(Array.from({length:25},(_,i)=>row(String(i),'Branch A','0')));await vm.runInContext('loadReplenishment()',h.context);
 assert.equal((h.$('rp-list').innerHTML.match(/<article/g)||[]).length,20);assert.match(h.$('rp-page').textContent,/Page 1 of 2/);assert.equal(h.$('rp-draft').disabled,true);
});
