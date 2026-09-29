let partnerResource='products', partnerContext={products:[],stores:[],books:[]}, partnerRecords=[], partnerPage=0;
let partnerReadSequence=0, partnerEditSequence=0, partnerChange=null, partnerDraft=null, partnerSending=false;
let partnerLoadedBookId='', partnerRuleSearch='', partnerRefreshBookId='';
const partnerResources={
 products:{title:'Products',help:'Get all StoreHub products or look up one product by its StoreHub ID. Add new products in the warehouse, then publish from StoreHub connection.',fields:[['id','StoreHub product ID','text']],columns:[['name','Product'],['sku','SKU'],['barcode','Barcode'],['cost','Cost'],['unitPrice','Unit price']]},
 inventory:{title:'Inventory',help:'Read current balances, warning stock and ideal stock for an active store. This does not replace local warehouse quantities.',fields:[['storeId','Store','store',true]],columns:[['productId','Product'],['quantityOnHand','On hand'],['warningStock','Warning stock'],['idealStock','Ideal stock']]},
 customers:{title:'Customers',help:'Choose a customer or search by name, email and phone. Search filters use AND; StoreHub returns at most 100 search matches.',fields:[['refId','Customer','customer'],['firstName','First name starts with','text'],['lastName','Last name starts with','text'],['email','Email contains','text'],['phone','Phone contains','text']],columns:[['firstName','First name'],['lastName','Last name'],['email','Email'],['phone','Phone'],['memberId','Member ID']]},
 transactions:{title:'Transactions',help:'Read sales, returns and online orders. Dates below use UTC creation time. A sale or return sent here creates a StoreHub record; it does not collect a payment.',fields:[['from','Created from (UTC)','date'],['to','Created to (UTC)','date'],['storeId','Store','store'],['includeOnline','Include online orders','checkbox'],['onlineOnly','Online orders only','checkbox']],columns:[['invoiceNumber','Invoice'],['transactionTime','Transaction time'],['storeId','Store'],['transactionType','Type'],['total','Total'],['isCancelled','Cancelled']]},
 employees:{title:'Employees',help:'Read employee records. Optionally load employees modified on or after a date.',fields:[['modifiedSince','Modified since','date']],columns:[['firstName','First name'],['lastName','Last name'],['email','Email'],['phone','Phone']]},
 stores:{title:'Stores',help:'Read contact and address details for the four Daily Centro stores.',fields:[],columns:[['name','Store'],['city','City'],['phone','Phone'],['email','Email']]},
 timesheets:{title:'Timesheets',help:'Search attendance by date, store and employee. Open a record for clock-in and clock-out details.',fields:[['from','Clock-in from','date'],['to','Clock-in to','date'],['storeId','Store','store'],['employeeId','Employee','employee']],columns:[['employeeId','Employee'],['storeId','Store'],['clockInTime','Clock in'],['clockOutTime','Clock out']]},
 priceitems:{title:'Price books',help:'Create a book, open a saved book, or enter an existing StoreHub book ID. Add, view, edit and delete its pricing rules. StoreHub’s API does not provide a list of all books or an action to rename or delete a book itself.',fields:[['bookId','Existing price-book ID','book',true]],columns:[['productId','Product'],['unitPrice','Unit price'],['taxCode','Tax code'],['minQuantity','Minimum quantity'],['maxQuantity','Maximum quantity']]}
};
const partnerMoneyFields=new Set(['cost','unitPrice','total','subTotal','tax','discount','roundedAmount','supplierPrice','storeCreditsBalance','cashbackBalance','loyalty','amount','serviceCharge','deductedTax','discountAmount']);
const partnerDefaultColumns=[['name','Product'],['sku','SKU'],['barcode','Barcode'],['unitPrice','Price (tax-exclusive)'],['priceType','Price type']];
function partnerIsDefaultPrices(){return partnerResource==='priceitems'&&partnerLoadedBookId==='default';}
function partnerLabel(key){return ({employeeId:'Employee',employeeRefId:'Employee',customerRefId:'Customer',customerId:'Customer',storeId:'Store',productId:'Product'})[key]||key.replace(/([a-z])([A-Z])/g,'$1 $2').replace(/^./,v=>v.toUpperCase());}
function partnerFeedback(id,text,error=false){$(id).textContent=text;$(id).className=error?'error':'success';}
function partnerProductOptions(selected='',search=''){const query=search.trim().toLowerCase();return '<option value="">Choose product</option>'+partnerContext.products.filter(p=>!p.isParentProduct&&(p.id===selected||!query||[p.name,p.sku,p.barcode,p.id].some(v=>String(v||'').toLowerCase().includes(query)))).map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)}${p.sku?' · '+esc(p.sku):''}</option>`).join('');}
function partnerField(spec,value,editing=false){
 const [key,label,type='text',required=false]=spec,id=(editing?'partner-write-':'partner-filter-')+key;
 let control='';
 if(type==='employee'||type==='customer')control=`<select id="${id}" name="${key}" ${required?'required':''}><option value="">${editing?'Not specified':type==='employee'?'All employees':'All customers'}</option>${(window.StoreHubPeople?.options(type==='employee'?'employees':'customers',value)||[]).map(p=>`<option value="${esc(p.id)}" ${p.id===value?'selected':''}>${esc(p.name)}</option>`).join('')}</select>`;
 else if(type==='store'||type==='stores')control=`<select id="${id}" name="${key}" ${type==='stores'?'multiple size="4"':''} ${required?'required':''}>${type==='store'?'<option value="">'+(required?'Choose store':'All active stores')+'</option>':''}${partnerContext.stores.map(s=>`<option value="${esc(s.id)}" ${(Array.isArray(value)?value.includes(s.id):value===s.id)?'selected':''}>${esc(s.name)}</option>`).join('')}</select>`;
 else if(type==='enum')control=`<select id="${id}" name="${key}">${spec[4].map(v=>`<option ${v===value?'selected':''}>${esc(v)}</option>`).join('')}</select>`;
 else if(type==='product')control=`<select id="${id}" name="${key}" required>${partnerProductOptions(value)}</select>`;
 else if(type==='checkbox')control=`<input id="${id}" name="${key}" type="checkbox" ${value?'checked':''}>`;
 else{
  const actual=type==='tags'||type==='book'||type==='readonly'?'text':type==='number'?'number':type;
  const shown=Array.isArray(value)?value.join(', '):value??'';
  control=`<input id="${id}" name="${key}" type="${actual}" value="${esc(shown)}" ${required?'required':''} ${type==='number'?'step="any" min="0"':''} ${type==='readonly'?'readonly':''} ${type==='book'?'list="partner-books"':''}>`;
  if(type==='book')control+='<datalist id="partner-books">'+partnerContext.books.map(b=>`<option value="${esc(b.id)}">${esc(b.name||b.id)}</option>`).join('')+'</datalist>';
 }
 return `<label class="${type==='checkbox'?'partner-confirm':''}" for="${id}">${esc(label+(partnerMoneyFields.has(key)?' (PHP)':''))}${control}</label>`;
}
function partnerFormValues(specs,editing=false){
 const result={};
 for(const spec of specs){const [key,,type='text']=spec,el=$((editing?'partner-write-':'partner-filter-')+key);
  if(type==='checkbox')result[key]=el.checked;
  else if(type==='stores')result[key]=[...el.selectedOptions].map(o=>o.value);
  else if(type==='tags')result[key]=el.value.split(',').map(v=>v.trim()).filter(Boolean);
  else if(type==='number'){if(el.value!=='')result[key]=Number(el.value);}
  else if(type==='datetime-local'){if(el.value)result[key]=new Date(el.value).toISOString();}
  else if(el.value!==''||editing&&partnerChange?.operation==='customer.update')result[key]=el.value.trim();
 }
 return result;
}
async function partnerLoadContext(){const results=await Promise.all([fetch('/api/storehub/partner/context',{cache:'no-store'}),window.StoreHubPeople?.load()]);const r=results[0];if(!r.ok)throw Error('Could not load StoreHub tool options.');partnerContext=await r.json();}
function partnerChoose(resource){
 partnerResource=resource;partnerRecords=[];partnerPage=0;partnerReadSequence++;partnerLoadedBookId='';partnerRuleSearch='';$('partner-rule-search').value='';
 $('partner-read-button').disabled=false;
 const config=partnerResources[resource];$('partner-resource-title').textContent=config.title;$('partner-help').textContent=config.help;
 const today=new Date().toISOString().slice(0,10),before=new Date(Date.now()-30*86400000).toISOString().slice(0,10);
 $('partner-filters').innerHTML=config.fields.map(f=>partnerField(f,f[0]==='from'?before:f[0]==='to'?today:f[0]==='includeOnline'?true:'')).join('');
 $('partner-tabs').innerHTML=Object.entries(partnerResources).map(([key,c])=>`<button type="button" data-partner-resource="${key}" ${key===resource?'class="active" aria-pressed="true"':'aria-pressed="false"'}>${esc(c.title)}</button>`).join('');
 document.querySelectorAll('[data-partner-resource]').forEach(b=>b.onclick=()=>partnerChoose(b.dataset.partnerResource));
 const actions=resource==='customers'?[['customer.create','New customer']]:resource==='transactions'?[['transaction.create','Record sale / return']]:resource==='priceitems'?[['pricebook.create','New price book'],['priceitem.create','Add pricing rule']]:[];
 $('partner-actions').innerHTML=actions.map(([operation,label])=>`<button type="button" class="primary" data-partner-operation="${operation}">${label}</button>`).join('')+(resource==='products'?'<button type="button" id="partner-local-product">New warehouse product</button>':'');
 document.querySelectorAll('[data-partner-operation]').forEach(b=>b.onclick=()=>partnerOpenChange(b.dataset.partnerOperation).catch(e=>partnerFeedback('partner-feedback',e.message,true)));
 if($('partner-local-product'))$('partner-local-product').onclick=()=>$('new-product').click();
 $('partner-results-card').classList.add('hidden');$('partner-feedback').textContent='';
 $('partner-read-button').textContent=resource==='priceitems'?'Open price book':'Load from StoreHub';
 $('partner-rule-search-label').classList.toggle('hidden',resource!=='priceitems');
 $('partner-book-library').classList.toggle('hidden',resource!=='priceitems');
 if(resource==='priceitems'){partnerRenderBooks();$('partner-filter-bookId').value='default';partnerReadRecords();}
}
function partnerRenderBooks(){
 $('partner-saved-books').innerHTML='<button type="button" class="partner-book" data-partner-book="default"><strong>Default product prices</strong><span>All StoreHub products</span><small>Current selling prices · Tax-exclusive</small></button>'+partnerContext.books.filter(b=>b.id!=='default').map(b=>`<button type="button" class="partner-book" data-partner-book="${esc(b.id)}"><strong>${esc(b.name||'Existing price book')}</strong><span>${esc(b.id)}</span><small>${Array.isArray(b.appliedStores)?esc(b.appliedStores.length?b.appliedStores.map(id=>partnerContext.stores.find(s=>s.id===id)?.name||id).join(' · '):'Applies to all StoreHub stores'):'Store assignment managed in StoreHub'}</small></button>`).join('');
 document.querySelectorAll('[data-partner-book]').forEach(b=>b.onclick=()=>{$('partner-filter-bookId').value=b.dataset.partnerBook;partnerReadRecords();});
}
async function partnerOpen(){view('partner-tools');window.scrollTo({top:0,behavior:'instant'});try{await partnerLoadContext();partnerChoose(partnerResource);}catch(e){partnerFeedback('partner-feedback',e.message,true);}}
document.querySelectorAll('[data-partner-open]').forEach(b=>b.onclick=partnerOpen);
document.querySelector('nav [data-view="partner-tools"]').onclick=partnerOpen;
function partnerDisplay(key,value){
 if(value===null||value===undefined||value==='')return '—';
 if(partnerMoneyFields.has(key))return formatCost(value);
 if(window.StoreHubPeople?.fields[key])return window.StoreHubPeople.display(key,value);
 if(key==='storeId')return partnerContext.stores.find(s=>s.id===value)?.name||value;
 if(key==='productId')return partnerContext.products.find(p=>p.id===value)?.name||value;
 if(typeof value==='boolean')return value?'Yes':'No';
 if(/Time$/.test(key)){const d=new Date(value);return Number.isNaN(d.valueOf())?value:d.toLocaleString();}
 return Array.isArray(value)?value.join(', '):String(value);
}
function partnerRender(){
 const defaults=partnerIsDefaultPrices(),config=defaults?{columns:partnerDefaultColumns}:partnerResources[partnerResource],filtered=partnerFilteredRecords(),pages=Math.max(1,Math.ceil(filtered.length/20));partnerPage=Math.min(partnerPage,pages-1);
 const book=partnerContext.books.find(b=>b.id===partnerLoadedBookId);
 $('partner-results-title').textContent=defaults?'Default product prices':partnerResource==='priceitems'?(book?.name||'Pricing rules')+' · '+partnerLoadedBookId:'Results';
 document.querySelectorAll('[data-partner-operation="priceitem.create"]').forEach(b=>b.classList.toggle('hidden',defaults));
 $('partner-head').innerHTML='<tr>'+config.columns.map(([,label])=>`<th>${esc(label)}</th>`).join('')+'<th></th></tr>';
 $('partner-records').innerHTML=filtered.slice(partnerPage*20,partnerPage*20+20).map(r=>{const index=partnerRecords.indexOf(r);return '<tr>'+config.columns.map(([key])=>`<td>${esc(partnerDisplay(key,defaults&&key==='unitPrice'&&r.priceType==='Variable'?null:r[key]))}</td>`).join('')+`<td><div class="partner-rule-actions"><button data-partner-detail="${index}">View</button>${partnerResource==='priceitems'&&!defaults?`<button data-partner-rule-edit="${index}">Edit</button><button class="danger" data-partner-rule-delete="${index}">Delete</button>`:''}</div></td></tr>`;}).join('')||`<tr><td colspan="${config.columns.length+1}">${defaults?'No products match your search.':partnerResource==='priceitems'?'No pricing rules match. Add a pricing rule to this book.':'No matching StoreHub records.'}</td></tr>`;
 $('partner-count').textContent=filtered.length.toLocaleString()+(defaults?' of '+partnerRecords.length.toLocaleString()+' products':partnerResource==='priceitems'?' pricing rules':' records');$('partner-page').textContent=`Page ${partnerPage+1} of ${pages}`;
 $('partner-prev').disabled=partnerPage===0;$('partner-next').disabled=partnerPage+1>=pages;
 document.querySelectorAll('[data-partner-detail]').forEach(b=>b.onclick=()=>partnerDetail(partnerRecords[Number(b.dataset.partnerDetail)]));
 for(const [attr,op] of [['edit','priceitem.update'],['delete','priceitem.remove']])document.querySelectorAll('[data-partner-rule-'+attr+']').forEach(b=>b.onclick=()=>partnerOpenChange(op,partnerRecords[Number(b.dataset[attr==='edit'?'partnerRuleEdit':'partnerRuleDelete'])]).catch(e=>partnerFeedback('partner-feedback',e.message,true)));
 $('partner-results-card').classList.remove('hidden');labelTables();
}
function partnerFilteredRecords(){return partnerResource!=='priceitems'||!partnerRuleSearch?partnerRecords:partnerRecords.filter(r=>{const p=partnerIsDefaultPrices()?r:partnerContext.products.find(p=>p.id===r.productId);return [p?.name,p?.sku,p?.barcode,r.productId,r.id].some(v=>String(v||'').toLowerCase().includes(partnerRuleSearch));});}
$('partner-rule-search').oninput=e=>{partnerRuleSearch=e.target.value.trim().toLowerCase();partnerPage=0;partnerRender();};
$('partner-prev').onclick=()=>{partnerPage--;partnerRender();};$('partner-next').onclick=()=>{partnerPage++;partnerRender();};
async function partnerReadRecords(){
 const sequence=++partnerReadSequence,resource=partnerResource,filters=partnerFormValues(partnerResources[resource].fields);
 if(resource==='priceitems'&&!filters.bookId){partnerFeedback('partner-feedback','Choose a saved book or enter its StoreHub ID.',true);return;}
 let actual=resource;if(resource==='customers'&&filters.refId)actual='customer';if(resource==='products'&&filters.id)actual='product';if(resource==='priceitems'&&filters.itemId)actual='priceitem';
 const finish=window.WarehouseUI?.loading($('partner-filters').closest('.card'))||(()=>{});
 $('partner-read-button').disabled=true;partnerFeedback('partner-feedback','Loading StoreHub records...');
 try{const result=await api('/api/storehub/partner/read',{resource:actual,filters});if(sequence!==partnerReadSequence)return;if(resource==='priceitems'){await partnerLoadContext();if(sequence!==partnerReadSequence)return;if(partnerLoadedBookId!==filters.bookId){partnerRuleSearch='';$('partner-rule-search').value='';}partnerLoadedBookId=filters.bookId;partnerRenderBooks();}partnerRecords=resource==='priceitems'&&filters.bookId==='default'?[...result.records].sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),undefined,{numeric:true})):result.records;partnerPage=0;partnerRender();partnerFeedback('partner-feedback',result.note||`${result.count.toLocaleString()} records loaded from StoreHub.`);}
 catch(error){if(sequence===partnerReadSequence)partnerFeedback('partner-feedback',error.message,true);}
 finally{finish();if(sequence===partnerReadSequence)$('partner-read-button').disabled=false;}
}
$('partner-read-form').onsubmit=e=>{e.preventDefault();return partnerReadRecords();};
function partnerNamedAmounts(value){if(Array.isArray(value))return value.map(partnerNamedAmounts);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,v])=>[key,partnerMoneyFields.has(key)&&v!==null&&v!==''&&typeof v!=='object'?formatCost(v):partnerNamedAmounts(v)]));return value;}
function partnerRecordMarkup(record){
 const person=[record.firstName,record.lastName].filter(Boolean).join(' '),identityKey=partnerResource==='employees'?'id':partnerResource==='customers'?'refId':'';
 return '<dl class="partner-record">'+Object.entries(record).filter(([,v])=>!v||typeof v!=='object').map(([k,v])=>`<dt>${esc(k===identityKey&&person?partnerResource==='employees'?'Employee':'Customer':partnerLabel(k))}</dt><dd>${esc(k===identityKey&&person?person:partnerDisplay(k,v))}</dd>`).join('')+'</dl>'+Object.entries(record).filter(([,v])=>v&&typeof v==='object').map(([k,v])=>k==='items'&&Array.isArray(v)?partnerTransactionItems(v):`<details><summary>${esc(partnerLabel(k))}${Array.isArray(v)?' ('+v.length+')':''}</summary><pre>${esc(JSON.stringify(partnerNamedAmounts(window.StoreHubPeople?.named(v)||v),null,2))}</pre></details>`).join('');
}
function partnerTransactionItems(items){
 const amounts=[['quantity','Quantity'],['unitPrice','Unit price (tax-exclusive)'],['subTotal','Subtotal'],['discount','Discount'],['tax','Tax'],['total','Total']];
 return `<details open class="partner-transaction-items"><summary>Items (${items.length})</summary><div class="partner-item-list">${items.map((item,index)=>{
  const line=item&&typeof item==='object'?item:{},product=partnerContext.products.find(p=>p.id===line.productId),name=line.productName||line.name||product?.name||(line.productId?'Product no longer in catalog':line.itemType||'Item '+(index+1));
  const codes=[product?.sku?'SKU: '+product.sku:'',product?.barcode?'Barcode: '+product.barcode:''].filter(Boolean);
  return `<article class="partner-item"><h3>${esc(name)}</h3>${codes.length?`<p class="note">${esc(codes.join(' · '))}</p>`:''}${!product&&line.productId?`<p class="note">Product ID: ${esc(line.productId)}</p>`:''}<dl>${amounts.map(([key,label])=>`<div><dt>${label}</dt><dd>${esc(partnerDisplay(key,line[key]))}</dd></div>`).join('')}</dl>${line.taxCode?`<p class="note">Tax code: ${esc(line.taxCode)}</p>`:''}${line.notes?`<p>${esc(line.notes)}</p>`:''}</article>`;
 }).join('')||'<p class="note">No transaction items.</p>'}</div></details>`;
}
function partnerDetail(record){
 $('partner-detail-title').textContent=record.name||record.invoiceNumber||[record.firstName,record.lastName].filter(Boolean).join(' ')||record.id||record.refId||'StoreHub record';
 $('partner-detail-body').innerHTML=partnerRecordMarkup(record);
 const actions=[];
 if(partnerResource==='customers')actions.push(['customer.update','Edit customer']);
 if(partnerResource==='transactions'&&record.transactionType==='Sale'&&!record.isCancelled)actions.push(['transaction.cancel','Cancel sale']);
 if(partnerResource==='priceitems'&&!partnerIsDefaultPrices())actions.push(['priceitem.update','Edit pricing rule'],['priceitem.remove','Remove pricing rule']);
 $('partner-detail-actions').innerHTML=actions.map(([op,label])=>`<button type="button" data-partner-record-operation="${op}">${label}</button>`).join('');
 document.querySelectorAll('[data-partner-record-operation]').forEach(b=>b.onclick=()=>partnerOpenChange(b.dataset.partnerRecordOperation,record).catch(e=>partnerFeedback('partner-feedback',e.message,true)));
 $('partner-detail-dialog').showModal();
}
$('partner-detail-close').onclick=()=>$('partner-detail-dialog').close();
function partnerLocalDate(){const d=new Date();return new Date(d.valueOf()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
async function partnerOpenChange(operation,record={}){
 if(operation.startsWith('priceitem.')&&partnerIsDefaultPrices())throw Error('Open a custom price book to manage pricing rules. Default prices are product prices.');
 const sequence=++partnerEditSequence;await partnerLoadContext();if(sequence!==partnerEditSequence)return;
 if($('partner-detail-dialog').open)$('partner-detail-dialog').close();
 const titles={'customer.create':'New StoreHub customer','customer.update':'Edit StoreHub customer','transaction.create':'Record StoreHub sale / return','transaction.cancel':'Cancel StoreHub sale','pricebook.create':'Create StoreHub price book','priceitem.create':'Add price-book rule','priceitem.update':'Edit price-book rule','priceitem.remove':'Remove price-book rule'};
 let fields=[],values={...record},note='Review the request before any change is sent to StoreHub.';
 if(operation.startsWith('customer.')){
  fields=[['firstName','First name','text',true],['lastName','Last name','text',true],['email','Email','email'],['phone','Phone','text'],['address1','Address line 1','text'],['address2','Address line 2','text'],['city','City','text'],['state','State / province','text'],['postalCode','Postal code','text'],['tags','Tags (comma-separated)','tags']];
  if(operation==='customer.create'){fields.push(['refId','Customer reference','readonly',true],['birthday','Birthday','date'],['memberId','Member ID','text']);values.refId=partnerContext.customerRef;}
  else note='StoreHub replaces customer data during an update. This form includes the existing supported fields; clearing a field removes its value. The review shows the current record and the complete request.';
 }else if(operation==='transaction.create'){
  fields=[['refId','Transaction reference','readonly',true],['invoiceNumber','Invoice number','text',true],['storeId','Store','store',true],['transactionType','Type','enum',true,['Sale','Return']],['transactionTime','Transaction time','datetime-local',true],['paymentMethod','Recorded payment method','enum',true,['Cash','CreditCard']],['employeeId','Employee (optional)','employee'],['customerRefId','Customer (optional)','customer'],['comment','Comment','text'],['returnReason','Return reason','text'],['saleInvoiceNumber','Original sale invoice for return','text'],['tableId','Table ID (optional)','text']];
  values={refId:partnerContext.transactionRef,transactionTime:partnerLocalDate(),transactionType:'Sale',paymentMethod:'Cash'};
  note='Amounts below are tax-exclusive. Enter absolute tax and discount amounts per line. This records a real sale or return and can change StoreHub stock; it does not charge a card. Returns must be checked against the original sale.';
 }else if(operation==='transaction.cancel'){
  fields=[['cancelledTime','Cancellation time','datetime-local',true],['cancelledBy','Cancelled by (optional)','employee']];values={cancelledTime:partnerLocalDate()};note='Cancel invoice '+(record.invoiceNumber||record.refId)+'. The sale is rechecked in StoreHub during review and again before sending.';
 }else if(operation==='pricebook.create'){
  fields=[['name','Price-book name','text',true],['appliedStores','Apply to stores (select one or more)','stores',true]];values={appliedStores:partnerContext.stores.map(s=>s.id)};note='Select the Daily Centro stores that will use this book. The new book is saved here automatically after StoreHub confirms creation.';
 }else{
  fields=operation==='priceitem.remove'?[['productId','Product ID','readonly',true]]:[['productId','Product','product',true],['unitPrice','Tax-exclusive unit price','number'],['taxCode','Tax code (blank inherits product tax)','text'],['minQuantity','Minimum quantity','number'],['maxQuantity','Maximum quantity (optional)','number']];
  note=operation==='priceitem.remove'?'This removes a real StoreHub pricing rule. Review its current values before confirming.':'This sets a real StoreHub selling-price rule. Unit prices are rounded to two decimals. Leave optional quantity bounds blank if not specified.';
 }
 partnerChange={operation,fields,record,bookId:partnerLoadedBookId};partnerDraft=null;
 if(operation.startsWith('priceitem.')&&!partnerChange.bookId)throw Error('Open a price book before preparing a pricing rule.');
 $('partner-edit-title').textContent=titles[operation];$('partner-edit-note').textContent=note;$('partner-fields').innerHTML=fields.map(f=>partnerField(f,values[f[0]],true)).join('');
 if(operation.startsWith('priceitem.')){
  $('partner-edit-note').textContent=note+' Book: '+partnerChange.bookId;
  if(operation!=='priceitem.remove'){
   const label=document.createElement('label');label.textContent='Find product';const input=document.createElement('input');input.type='search';input.placeholder='Name, SKU or barcode';input.id='partner-product-search';label.append(input);$('partner-fields').prepend(label);
   input.oninput=()=>{const selected=$('partner-write-productId').value;$('partner-write-productId').innerHTML=partnerProductOptions(selected,input.value);};
  }
 }
 $('partner-transaction-lines').classList.toggle('hidden',operation!=='transaction.create');$('partner-sale-lines').innerHTML='';if(operation==='transaction.create')partnerAddSaleLine();
 $('partner-edit-feedback').textContent='';$('partner-edit-dialog').showModal();
}
function partnerAddSaleLine(){
 const row=document.createElement('tr');row.innerHTML=`<td><select data-sale-product required aria-label="Sale product">${partnerProductOptions()}</select></td><td><input data-sale-quantity type="number" min="0.000001" step="any" value="1" required aria-label="Sale quantity"></td><td><input data-sale-price type="number" min="0" step="0.01" required aria-label="Sale unit price (PHP)"></td><td><input data-sale-tax type="number" min="0" step="0.01" value="0" required aria-label="Sale tax amount (PHP)"></td><td><input data-sale-discount type="number" min="0" step="0.01" value="0" required aria-label="Sale discount amount (PHP)"></td><td><button type="button" data-sale-remove aria-label="Remove sale product">Remove</button></td>`;
 $('partner-sale-lines').append(row);row.querySelector('[data-sale-product]').onchange=e=>{const p=partnerContext.products.find(p=>p.id===e.target.value);row.querySelector('[data-sale-price]').value=p&&p.priceType!=='Variable'?Number(p.unitPrice||0).toFixed(2):'';partnerSaleTotal();};
 row.querySelectorAll('input').forEach(el=>el.oninput=partnerSaleTotal);row.querySelector('[data-sale-remove]').onclick=()=>{row.remove();partnerSaleTotal();};labelTables();partnerSaleTotal();
}
function partnerSaleItems(){return [...$('partner-sale-lines').children].map(row=>{const n=key=>Number(row.querySelector('[data-sale-'+key+']').value),round=v=>Math.round((v+Number.EPSILON)*100)/100,quantity=n('quantity'),unitPrice=n('price'),tax=n('tax'),discount=n('discount'),subTotal=round(quantity*unitPrice);return {productId:row.querySelector('[data-sale-product]').value,quantity,unitPrice,tax,discount,subTotal,total:round(subTotal+tax-discount)};});}
function partnerSaleTotal(){const items=partnerSaleItems();$('partner-sale-total').textContent=items.length+' lines · Total '+formatCost(items.reduce((sum,i)=>sum+i.total,0));}
$('partner-sale-add').onclick=partnerAddSaleLine;
$('partner-edit-close').onclick=()=>$('partner-edit-dialog').close();
$('partner-edit-form').onsubmit=async e=>{
 e.preventDefault();const submit=e.submitter;submit.disabled=true;partnerFeedback('partner-edit-feedback','Checking this change...');
 try{
  const values=partnerFormValues(partnerChange.fields,true),body={operation:partnerChange.operation,payload:values};
  if(partnerChange.operation==='customer.update'||partnerChange.operation==='transaction.cancel')body.refId=partnerChange.record.refId;
  if(partnerChange.operation.startsWith('priceitem.')){body.bookId=partnerChange.bookId;if(partnerChange.record.id)body.itemId=partnerChange.record.id;}
  if(partnerChange.operation==='transaction.create'){body.payload.items=partnerSaleItems();for(const key of ['subTotal','tax','discount','total'])body.payload[key]=Math.round(body.payload.items.reduce((sum,i)=>sum+i[key],0)*100)/100;}
  partnerDraft=await api('/api/storehub/partner/preview',body);
  $('partner-review-notes').innerHTML=partnerDraft.notes.map(n=>`<p class="notice">${esc(n)}</p>`).join('');
  $('partner-review-body').innerHTML=(partnerDraft.before?'<h3>Current StoreHub record</h3>'+partnerRecordMarkup(partnerDraft.before):'')+'<h3>Change to send</h3><p class="note">'+esc(partnerDraft.method+' '+partnerDraft.path)+'</p>'+(partnerDraft.payload?partnerRecordMarkup(partnerDraft.payload):'<p>This pricing rule will be removed.</p>');
  $('partner-confirm-check').checked=false;$('partner-send').disabled=true;$('partner-confirm-check').disabled=false;$('partner-review-close').disabled=false;$('partner-review-feedback').textContent='';
  $('partner-edit-dialog').close();$('partner-review-dialog').showModal();
 }catch(error){partnerFeedback('partner-edit-feedback',error.message,true);}finally{submit.disabled=false;}
};
$('partner-confirm-check').onchange=()=>$('partner-send').disabled=!$('partner-confirm-check').checked||partnerSending||!partnerDraft;
$('partner-review-close').onclick=()=>{if(!partnerSending)$('partner-review-dialog').close();};
$('partner-review-dialog').addEventListener('close',()=>{const bookId=partnerRefreshBookId;partnerRefreshBookId='';if(bookId&&partnerResource==='priceitems'){$('partner-filter-bookId').value=bookId;partnerReadRecords();}});
$('partner-review-dialog').addEventListener('cancel',e=>{if(partnerSending)e.preventDefault();});
$('partner-send').onclick=async()=>{
 if(partnerSending||!partnerDraft||!$('partner-confirm-check').checked)return;
 partnerSending=true;$('partner-send').disabled=true;$('partner-confirm-check').disabled=true;$('partner-review-close').disabled=true;
 partnerFeedback('partner-review-feedback','Sending this reviewed change to StoreHub...');
 try{const draft=partnerDraft,result=await api('/api/storehub/partner/commit',{token:draft.token,confirmed:true});partnerFeedback('partner-review-feedback','StoreHub confirmed the change. Local warehouse balances were preserved.');$('partner-review-body').innerHTML='<h3>StoreHub response</h3>'+(result.result?partnerRecordMarkup(result.result):'<p>Pricing rule removed successfully.</p>');if(draft.operation==='pricebook.create')partnerRefreshBookId=result.result.id;else if(draft.operation?.startsWith('priceitem.'))partnerRefreshBookId=draft.bookId;partnerDraft=null;}
 catch(error){partnerFeedback('partner-review-feedback',error.message+' Check Recent API changes before trying again.',true);partnerDraft=null;}
 finally{partnerSending=false;$('partner-review-close').disabled=false;}
};
$('partner-history-open').onclick=async()=>{try{const r=await fetch('/api/storehub/partner/history',{cache:'no-store'});if(!r.ok)throw Error('Could not read recent API changes.');const d=await r.json();$('partner-detail-title').textContent='Recent API changes';$('partner-detail-body').innerHTML=d.changes.length?'<div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Created</th><th>Details</th></tr></thead><tbody>'+d.changes.map(r=>`<tr><td>${esc(r.operation)}</td><td>${esc(r.status)}</td><td>${esc(new Date(r.created).toLocaleString())}</td><td>${esc(r.error||r.token)}</td></tr>`).join('')+'</tbody></table></div>':'<p>No StoreHub changes have been sent from these tools.</p>';$('partner-detail-actions').innerHTML='';$('partner-detail-dialog').showModal();labelTables();}catch(error){partnerFeedback('partner-feedback',error.message,true);}};
