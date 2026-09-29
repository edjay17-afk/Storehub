const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

function setup(responses){
 const elements=new Map(),requests=[];
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,hidden:false,files:[],classList:{add(){},remove(){}},addEventListener(){}});return elements.get(id);};
 $('st-file').files=[{size:100,name:'transfers.csv'}];$('st-size').value='20';
 const context=vm.createContext({$,esc:String,poDate:String,poNumber:String,formatCost:v=>'PHP '+Number(v).toFixed(2),labelTables(){},view(){},window:{scrollTo(){}},document:{querySelectorAll(){return [];}},URLSearchParams,setTimeout,clearTimeout,
  FormData:class{append(name,value){this[name]=value;}},
  async fetch(url,options){requests.push({url,options});const result=responses.shift();return {ok:result.ok??true,json:async()=>result.body};},
  async api(url,body){requests.push({url,body});return responses.shift().body;}
 });
 vm.runInContext(fs.readFileSync('static/stock-transfers.js','utf8'),context);
 return {$,requests,context};
}
const preview={token:'test-preview-token',filename:'transfers.csv',counts:{new:1,updated:0,unchanged:0},orders:[{id:'ST1',source:'Warehouse',target:'Branch',status:'Created',total:'1',change:'new',rows:[{},{}]}],skipped:['ST688']};
const listing={orders:[],total:1,allTotal:1,page:1,pages:1,statuses:['Created'],locations:['Warehouse','Branch']};

test('preview uploads the file and import submits only its server token',async()=>{
 const {$,requests}=setup([{body:preview},{body:{counts:preview.counts,skipped:1}},{body:listing}]);
 await $('st-upload-form').onsubmit({preventDefault(){}});
 assert.equal(requests[0].url,'/api/imported-transfers/preview');
 assert.equal(requests[0].options.body.file.name,'transfers.csv');
 assert.equal($('st-confirm').disabled,false);
 assert.match($('st-preview-summary').textContent,/1 new.*1 transfers involving store 688/);
 await $('st-confirm').onclick();
 assert.equal(requests[1].url,'/api/imported-transfers/import');
 assert.equal(JSON.stringify(requests[1].body),JSON.stringify({token:preview.token}));
 assert.match($('st-feedback').textContent,/Stock balances were preserved/);
 assert.equal($('st-confirm').disabled,true);
});

test('transfer list and details hide totals while line amounts show PHP',async()=>{
 const {$,context}=setup([{body:{...listing,orders:[{id:'ST1',status:'Created',source:'Warehouse',target:'Branch',created:'2026-09-29T00:00:00',imported:'2026-09-29',lines:1,total:'25.5'}]}}]);
 await vm.runInContext('loadImportedTransfers()',context);
 assert.ok(!$('st-list').innerHTML.includes('Total ('));
 const order={...preview.orders[0],filename:'test.csv',imported:'2026-09-29',rows:[{}, {'Product Name':'Rice','Ordered Qty':'1.5','Cost (RM)':'17','SubTotal (RM)':'25.50'}]};
 vm.runInContext('stRenderDetails('+JSON.stringify(order)+',false)',context);
 const html=$('st-detail-body').innerHTML;
 assert.match(html,/Cost \(PHP\)/);assert.match(html,/PHP 17\.00/);assert.match(html,/PHP 25\.50/);
 assert.ok(!html.includes('Total (RM)'));assert.ok(!html.includes('po-totals'));
});

test('an invalid file leaves confirmation disabled and displays the server error',async()=>{
 const {$,requests}=setup([{ok:false,body:{error:'Wrong Stock Transfer columns'}}]);
 await $('st-upload-form').onsubmit({preventDefault(){}});
 assert.equal($('st-confirm').disabled,true);
 assert.match($('st-feedback').textContent,/Wrong Stock Transfer columns/);
 await $('st-confirm').onclick();
 assert.equal(requests.length,1);
});

test('selecting another file invalidates the previous preview',async()=>{
 const {$,requests}=setup([{body:preview}]);
 await $('st-upload-form').onsubmit({preventDefault(){}});
 $('st-file').onchange();
 assert.equal($('st-confirm').disabled,true);
 await $('st-confirm').onclick();
 assert.equal(requests.length,1);
});
