const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function setup(){
 const elements=new Map(),requests=[],steps=[0,1,2].map(n=>({dataset:{inventoryStep:String(n)},hidden:false}));
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,options:[{value:''}],setAttribute(){},removeAttribute(){},focus(){},contains(){return false;},addEventListener(){},insertAdjacentHTML(_,s){this.innerHTML+=s;this.options.push({value:'Branch A'});},querySelectorAll:()=>[],checkValidity(){return this.valid!==false;},reportValidity(){},showModal(){this.open=true;},close(){this.open=false;},reset(){}});return elements.get(id);};
 const state={records:[],page:1,pages:1,total:0,summary:{total:0,soon:0,expired:0},branches:['Branch A'],submission_id:'server-token',today:'2026-09-29'};
 const products=[{id:'one','Product Name':'Milk <script>',SKU:'MILK-1',Barcode:'000123'}, {id:'two','Product Name':'Rice',SKU:'RICE',Barcode:'000456'}];
 const context=vm.createContext({$,esc:v=>String(v??'').replaceAll('<','&lt;'),data:{products},view(){},window:{WarehouseUI:{json:async url=>{requests.push({url});return state;}}},document:{addEventListener(){},querySelectorAll:s=>s==='[data-inventory-step]'?steps:[]},labelTables(){},URLSearchParams,setTimeout,clearTimeout,api:async(url,body)=>{requests.push({url,body});return {id:'saved'};}});
 vm.runInContext(fs.readFileSync('static/manual-inventory.js','utf8'),context);
 return {$,context,requests,state,steps};
}
test('product search includes full catalog names, SKU and barcode',()=>{
 const h=setup();assert.equal(vm.runInContext("inventoryProducts('000456')[0].id",h.context),'two');assert.equal(vm.runInContext("inventoryProducts('milk-1')[0].id",h.context),'one');assert.equal(vm.runInContext("inventoryProducts('rice')[0].id",h.context),'two');assert.equal(vm.runInContext("inventoryProducts(' ').length",h.context),0);
});
test('Enter advances the step and cannot save an incomplete count',async()=>{
 const h=setup();await h.$('inventory-form').onsubmit({preventDefault(){}});assert.equal(vm.runInContext('inventoryStep',h.context),0);assert.equal(h.requests.length,0);
 h.$('inventory-person').value='Maria';h.$('inventory-form').onsubmit({preventDefault(){}});assert.equal(vm.runInContext('inventoryStep',h.context),1);
 await h.$('inventory-form').onsubmit({preventDefault(){}});assert.equal(vm.runInContext('inventoryStep',h.context),1);assert.match(h.$('inventory-form-feedback').textContent,/Choose a product/);assert.equal(h.requests.length,0);
});
test('loading is read-only, escapes product names and renders pagination',async()=>{
 const h=setup();h.state.records=[{id:'r',product_name:'Milk <script>',sku:'M',quantity:'10',branch:'Branch A',expiry_date:'2026-10-01',expiry_status:'Expiring soon',count_date:'2026-09-29',counted_by:'Maria',batch:'lot'}];h.state.total=21;h.state.pages=2;
 await vm.runInContext('loadManualInventory()',h.context);assert.ok(h.requests.every(r=>!r.body));assert.match(h.$('inventory-records').innerHTML,/&lt;script>/);assert.equal(h.$('inventory-prev').disabled,true);assert.equal(h.$('inventory-next').disabled,false);assert.match(h.$('inventory-page').textContent,/Page 1 of 2/);
});
test('save sends batch count with the server token and never a stock-write route',async()=>{
 const h=setup();await vm.runInContext('inventoryOpen()',h.context);h.$('inventory-person').value='Maria';h.$('inventory-branch').value='Branch A';h.$('inventory-date').value='2026-09-29';h.$('inventory-quantity').value='4';h.$('inventory-batch').value='Lot 4';h.$('inventory-expiry').value='2026-10-10';
 vm.runInContext("inventoryChoose(data.products[1]);inventoryShowStep(2)",h.context);await h.$('inventory-form').onsubmit({preventDefault(){}});
 const writes=h.requests.filter(r=>r.body);assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/manual-inventory');assert.equal(writes[0].body.submission_id,'server-token');assert.equal(writes[0].body.product_id,'two');assert.equal(writes[0].body.quantity,'4');assert.equal(writes[0].body.expiry_date,'2026-10-10');assert.equal(h.$('inventory-dialog').open,false);
});
test('edit preserves record revision and uses edit endpoint',async()=>{
 const h=setup();await vm.runInContext('loadManualInventory()',h.context);h.context.record={id:'record',product_id:'two',count_date:'2026-09-29',branch:'Branch A',counted_by:'Maria',quantity:'6',expiry_date:'',batch:'B',notes:'',revision:3};
 await vm.runInContext('inventoryOpen(record);',h.context);vm.runInContext('inventoryShowStep(2)',h.context);await h.$('inventory-form').onsubmit({preventDefault(){}});
 const write=h.requests.find(r=>r.body);assert.equal(write.url,'/api/manual-inventory/record/edit');assert.equal(write.body.revision,3);assert.equal(write.body.quantity,'6');
});
test('stale network responses cannot replace newer filter results',async()=>{
 const h=setup(),resolve=[];h.context.window.WarehouseUI.json=()=>new Promise(r=>resolve.push(r));
 const first=vm.runInContext('loadManualInventory()',h.context),second=vm.runInContext('loadManualInventory()',h.context);
 resolve[1]({...h.state,total:2});await second;resolve[0]({...h.state,total:9});await first;assert.match(h.$('inventory-total').textContent,/2 matching/);
});
test('barcode selects exact match with leading zeros and advances without saving',()=>{
 const h=setup();vm.runInContext("inventoryApplyBarcode(' 000123 ')",h.context);assert.equal(vm.runInContext('inventoryProduct.id',h.context),'one');assert.equal(vm.runInContext('inventoryStep',h.context),2);assert.equal(h.requests.length,0);
});
test('unknown and shared barcodes clear stale selection and require a choice',()=>{
 const h=setup();vm.runInContext("inventoryApplyBarcode('000123');inventoryApplyBarcode('999')",h.context);assert.equal(vm.runInContext('inventoryProduct',h.context),null);assert.equal(vm.runInContext('inventoryStep',h.context),1);assert.match(h.$('inventory-form-feedback').textContent,/not found/);
 h.context.data.products[1].Barcode='000123, 000456';vm.runInContext("inventoryApplyBarcode('000123')",h.context);assert.equal(vm.runInContext('inventoryProduct',h.context),null);assert.match(h.$('inventory-form-feedback').textContent,/More than one/);
});
test('secure scan draft restores branch, expiry, count, original token and edit revision',async()=>{
 const h=setup();await vm.runInContext('loadManualInventory()',h.context);
 h.context.record={id:'record',product_id:'two',count_date:'2026-09-29',branch:'Branch A',counted_by:'Maria',quantity:'6',expiry_date:'2026-10-10',batch:'B',notes:'Keep this note',revision:3};
 await vm.runInContext('inventoryOpen(record)',h.context);h.$('inventory-quantity').value='9';vm.runInContext("inventoryToken='original-token';savedDraft=inventoryScanDraft()",h.context);
 h.$('inventory-quantity').value='';h.$('inventory-expiry').value='';await vm.runInContext('inventoryRestoreScan(savedDraft)',h.context);
 assert.equal(h.$('inventory-quantity').value,'9');assert.equal(h.$('inventory-expiry').value,'2026-10-10');assert.equal(h.$('inventory-branch').value,'Branch A');assert.equal(h.$('inventory-person').value,'Maria');assert.equal(h.$('inventory-notes').value,'Keep this note');assert.equal(vm.runInContext('inventoryEditing.revision',h.context),3);assert.equal(vm.runInContext('inventoryToken',h.context),'original-token');assert.equal(vm.runInContext('inventoryStep',h.context),1);assert.ok(h.requests.every(r=>!r.body));
});
