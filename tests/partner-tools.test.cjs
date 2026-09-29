const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(handler){
 const nodes=new Map(),calls=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:false,disabled:false,textContent:'',innerHTML:'',open:false,children:[],classList:{add(){},remove(){},toggle(){}},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}});return nodes.get(id);};
 const context={$,...{console,Date,Number,Math,JSON,Set,Error},esc:v=>String(v??'').replace(/</g,'&lt;'),formatCost:v=>Number(v).toFixed(2),labelTables(){},view(){},window:{scrollTo(){}},document:{querySelectorAll:()=>[],querySelector:()=>({})},fetch:async()=>({ok:true,json:async()=>({products:[],stores:[],books:[]})}),api:async(path,body)=>{calls.push({path,body});return handler(path,body);}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('static/partner-tools.js','utf8'),context);
 return {$,calls,context,run:code=>vm.runInContext(code,context)};
}
test('customer form previews the payload without sending a live change',async()=>{
 const h=harness(()=>({token:'preview-token',method:'POST',path:'/customers',notes:['Review customer'],payload:{firstName:'Ana',lastName:'Test'}}));
 h.run("partnerChange={operation:'customer.create',fields:[['firstName','First name','text',true],['lastName','Last name','text',true]],record:{}};");
 h.$('partner-write-firstName').value='Ana';h.$('partner-write-lastName').value='Test';
 await h.$('partner-edit-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/api/storehub/partner/preview');assert.equal(h.calls[0].body.payload.firstName,'Ana');
 assert.equal(h.$('partner-review-dialog').open,true);assert.equal(h.$('partner-send').disabled,true);assert.equal(h.$('partner-confirm-check').checked,false);
});
test('sending needs explicit confirmation and repeated taps submit the reviewed token once',async()=>{
 let resolve;const pending=new Promise(r=>resolve=r),h=harness(()=>pending);
 h.run("partnerDraft={token:'reviewed-token'};");
 await h.$('partner-send').onclick();assert.equal(h.calls.length,0);
 h.$('partner-confirm-check').checked=true;
 const first=h.$('partner-send').onclick();await h.$('partner-send').onclick();
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/api/storehub/partner/commit');assert.equal(h.calls[0].body.token,'reviewed-token');assert.equal(h.calls[0].body.confirmed,true);
 h.$('partner-review-dialog').open=true;h.$('partner-review-close').onclick();assert.equal(h.$('partner-review-dialog').open,true);
 resolve({result:{refId:'saved'}});await first;assert.equal(h.run('partnerDraft'),null);assert.equal(h.$('partner-send').disabled,true);
});
test('a failed or uncertain write cannot be blindly resent from the review',async()=>{
 const h=harness(()=>{throw Error('Unknown outcome');});h.run("partnerDraft={token:'reviewed-token'};");h.$('partner-confirm-check').checked=true;
 await h.$('partner-send').onclick();await h.$('partner-send').onclick();
 assert.equal(h.calls.length,1);assert.equal(h.run('partnerDraft'),null);assert.match(h.$('partner-review-feedback').textContent,/Recent API changes/);
});

test('rule search matches product name, SKU and barcode while preserving original records',()=>{
 const h=harness(()=>{});
 h.run("partnerResource='priceitems';partnerContext.products=[{id:'p1',name:'Coffee',sku:'COF-01',barcode:'001234'}];partnerRecords=[{id:'r1',productId:'p1'},{id:'r2',productId:'p2'}];");
 for(const query of ['coffee','cof-01','001234']){h.run(`partnerRuleSearch='${query}';`);assert.equal(h.run('partnerFilteredRecords()[0].id'),'r1');assert.equal(h.run('partnerFilteredRecords().length'),1);}
 assert.equal(h.run('partnerRecords.length'),2);
 h.run("partnerRuleSearch='missing';");assert.equal(h.run('partnerFilteredRecords().length'),0);
});

test('editing and deleting rules stays tied to the opened book after the lookup input changes',async()=>{
 const h=harness(()=>({token:'rule-preview',method:'DELETE',path:'/priceBooks/opened/items/r1?productId=p1',notes:[]}));
 h.run("partnerLoadedBookId='opened';");h.$('partner-filter-bookId').value='different';
 await h.run("partnerOpenChange('priceitem.remove',{id:'r1',productId:'p1'})");
 assert.equal(h.run('partnerChange.bookId'),'opened');assert.match(h.$('partner-fields').innerHTML,/readonly/);
 h.$('partner-write-productId').value='p1';
 await h.$('partner-edit-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
 assert.equal(h.calls[0].body.bookId,'opened');assert.equal(h.calls[0].body.itemId,'r1');
 assert.equal(h.$('partner-review-dialog').open,true);assert.equal(h.$('partner-send').disabled,true);
});

test('a new pricing rule requires a successfully opened book',async()=>{
 const h=harness(()=>{});h.$('partner-filter-bookId').value='unopened';
 await assert.rejects(h.run("partnerOpenChange('priceitem.create')"),/Open a price book/);
 assert.equal(h.calls.length,0);
});

test('a context refresh failure cannot attach displayed rules to another book',async()=>{
 const h=harness(()=>({records:[{id:'new-rule',productId:'p1'}],count:1}));
 h.run("partnerResource='priceitems';partnerLoadedBookId='old-book';partnerRecords=[{id:'old-rule',productId:'p1'}];");
 h.$('partner-filter-bookId').value='new-book';
 h.context.fetch=async()=>{throw Error('Connection interrupted');};
 await h.run('partnerReadRecords()');
 assert.equal(h.run('partnerLoadedBookId'),'old-book');assert.equal(h.run('partnerRecords[0].id'),'old-rule');
 assert.match(h.$('partner-feedback').textContent,/Connection interrupted/);
 assert.equal(h.$('partner-read-button').disabled,false);
});

test('Default prices show all 1371 products with pagination and no custom-rule actions',()=>{
 const h=harness(()=>{});
 h.run("partnerResource='priceitems';partnerLoadedBookId='default';partnerRecords=Array.from({length:1371},(_,i)=>({id:'p'+i,name:'Product '+i,unitPrice:8.928571,priceType:'Fixed',sku:'SKU'+i,barcode:'00'+i}));partnerRender();");
 assert.equal(h.$('partner-count').textContent,'1,371 of 1,371 products');
 assert.equal(h.$('partner-page').textContent,'Page 1 of 69');
 assert.equal((h.$('partner-records').innerHTML.match(/data-partner-detail=/g)||[]).length,20);
 assert.match(h.$('partner-records').innerHTML,/8\.93/);
 assert.doesNotMatch(h.$('partner-records').innerHTML,/data-partner-rule-(edit|delete)/);
 h.$('partner-rule-search').oninput({target:{value:'SKU1370'}});
 assert.equal(h.$('partner-count').textContent,'1 of 1,371 products');
 assert.match(h.$('partner-records').innerHTML,/Product 1370/);
 h.run("partnerDetail(partnerRecords[1370]);");assert.equal(h.$('partner-detail-actions').innerHTML,'');
});

test('Default price records cannot open custom-rule write forms',async()=>{
 const h=harness(()=>{});h.run("partnerResource='priceitems';partnerLoadedBookId='default';");
 await assert.rejects(h.run("partnerOpenChange('priceitem.create')"),/Default prices are product prices/);
 assert.equal(h.calls.length,0);
});

test('transaction items resolve product names and show readable amounts without raw item JSON',()=>{
 const h=harness(()=>{});
 h.run("partnerContext.products=[{id:'p1',name:'Coffee <script>',sku:'COF-01',barcode:'001234'}];");
 const html=h.run("partnerRecordMarkup({invoiceNumber:'INV1',items:[{productId:'p1',quantity:1,unitPrice:13.39,subTotal:13.39,discount:0,tax:1.61,total:15,taxCode:'VAT'}]})");
 assert.match(html,/Coffee &lt;script>/);assert.doesNotMatch(html,/<script>/);
 assert.match(html,/SKU: COF-01/);assert.match(html,/Barcode: 001234/);
 assert.match(html,/Unit price \(tax-exclusive\)/);assert.match(html,/>13\.39</);assert.match(html,/>15\.00</);assert.match(html,/>0\.00</);
 assert.doesNotMatch(html,/<pre>/);assert.doesNotMatch(html,/"productId"/);
 assert.equal(h.calls.length,0);
});

test('transaction items retain historical names and identify products missing from the catalog',()=>{
 const h=harness(()=>{});
 const html=h.run("partnerTransactionItems([{productId:'deleted',productName:'Old product name',quantity:0,total:0},{productId:'unknown',quantity:2}])");
 assert.match(html,/Old product name/);assert.match(html,/Product no longer in catalog/);assert.match(html,/Product ID: unknown/);
 assert.match(html,/>0\.00</);assert.match(html,/>0</);
});

test('people selectors display names but previews keep the employee and customer IDs',async()=>{
 const h=harness(()=>({token:'name-preview',method:'POST',path:'/transactions',notes:[]}));
 h.context.window.StoreHubPeople={fields:{employeeId:'employees',customerRefId:'customers'},display:(key,id)=>key==='employeeId'?'Ana Staff':'Ben Customer',options:kind=>[{id:kind==='employees'?'e1':'c1',name:kind==='employees'?'Ana Staff':'Ben Customer'}]};
 const html=h.run("partnerField(['employeeId','Employee','employee'],'e1',true)");
 assert.match(html,/Ana Staff/);assert.match(html,/value="e1" selected/);
 assert.equal(h.run("partnerDisplay('employeeId','e1')"),'Ana Staff');
 h.run("partnerChange={operation:'test',fields:[['employeeId','Employee','employee'],['customerRefId','Customer','customer']],record:{}};");
 h.$('partner-write-employeeId').value='e1';h.$('partner-write-customerRefId').value='c1';
 await h.$('partner-edit-form').onsubmit({preventDefault(){},submitter:{disabled:false}});
 assert.equal(h.calls[0].body.payload.employeeId,'e1');assert.equal(h.calls[0].body.payload.customerRefId,'c1');
});
