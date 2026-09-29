let inventoryData=null,inventoryPage=1,inventoryEpoch=0,inventoryStep=0,inventoryProduct=null,inventoryEditing=null,inventoryDeleting=null,inventoryBusy=false,inventoryToken='',inventorySearchTimer;
let inventoryEmployees=[],inventoryEmployeeOptions=[],inventoryEmployeeActive=-1,inventoryEmployeesLoading=false;
function inventoryEmployeeMatches(query){const q=query.trim().toLowerCase();return inventoryEmployees.filter(name=>name.toLowerCase().includes(q));}
function inventoryEmployeeClose(){$('inventory-people').hidden=true;$('inventory-person').setAttribute('aria-expanded','false');$('inventory-person-toggle').setAttribute('aria-expanded','false');$('inventory-person').removeAttribute('aria-activedescendant');inventoryEmployeeActive=-1;}
function inventoryEmployeeRender(showAll=false){
 inventoryEmployeeOptions=inventoryEmployeeMatches(showAll?'':$('inventory-person').value);inventoryEmployeeActive=-1;
 $('inventory-people').innerHTML=inventoryEmployeeOptions.map((name,index)=>`<button id="inventory-employee-${index}" type="button" role="option" tabindex="-1" aria-selected="false" data-employee-index="${index}">${esc(name)}</button>`).join('')||`<p class="note">${inventoryEmployeesLoading?'Loading employees...':inventoryEmployees.length?'No matching employees. You can keep the name you typed.':'Employee list unavailable. You can type a name.'}</p>`;
 $('inventory-people').hidden=false;$('inventory-person').setAttribute('aria-expanded','true');$('inventory-person-toggle').setAttribute('aria-expanded','true');$('inventory-person').removeAttribute('aria-activedescendant');
}
function inventoryEmployeeSelect(name){$('inventory-person').value=name;inventoryEmployeeClose();$('inventory-person').focus();inventoryEmployeeClose();}
async function inventoryEmployeeLoad(){
 inventoryEmployeesLoading=true;$('inventory-person-note').textContent='Loading employees. You can also type a name.';
 try{await window.StoreHubPeople?.load();inventoryEmployees=[...new Set((window.StoreHubPeople?.options('employees')||[]).map(p=>p.name).filter(n=>typeof n==='string'&&n.trim()))].sort((a,b)=>a.localeCompare(b));}
 finally{inventoryEmployeesLoading=false;$('inventory-person-note').textContent=inventoryEmployees.length?'Choose an employee or enter another name.':'Employee list unavailable. You can still type a name.';if(!$('inventory-people').hidden)inventoryEmployeeRender();}
}
function inventoryFeedback(text,error=false,form=false){const node=$(form?'inventory-form-feedback':'inventory-feedback');node.textContent=text;node.className=error?'error':'note';}
async function loadManualInventory(){
 const epoch=++inventoryEpoch,query=new URLSearchParams({page:inventoryPage,branch:$('inventory-filter-branch').value,search:$('inventory-filter-search').value.trim(),status:$('inventory-filter-status').value,deleted:$('inventory-filter-deleted').value});
 try{
  inventoryFeedback('Loading batch records...');
  const result=await window.WarehouseUI.json('/api/manual-inventory?'+query,{},$('inventory-table'));
  if(epoch!==inventoryEpoch)return;
  inventoryData=result;inventoryPage=result.page;
  if($('inventory-filter-branch').options.length===1){const options=result.branches.map(b=>`<option value="${esc(b)}">${esc(b)}</option>`).join('');$('inventory-filter-branch').insertAdjacentHTML('beforeend',options);$('inventory-branch').insertAdjacentHTML('beforeend',options);}
  $('inventory-summary').innerHTML=[['Active batches',result.summary.total,''],['Due within 30 days',result.summary.soon,'expiry-warning'],['Expired batches',result.summary.expired,'expiry-danger']].map(([label,total,css])=>`<div class="${css}"><span>${label}</span><b>${total.toLocaleString()}</b></div>`).join('');
  $('inventory-total').textContent=`${result.total.toLocaleString()} matching batch records`;
  $('inventory-records').innerHTML=result.records.map(r=>`<tr><td><b>${esc(r.product_name)}</b><small>${esc(r.sku)||'No SKU'}${r.batch?' · Batch '+esc(r.batch):' · No lot number'}</small>${r.notes?`<small>${esc(r.notes)}</small>`:''}</td><td>${esc(r.branch)}</td><td>${esc(r.quantity)}</td><td>${esc(r.expiry_date)||'No expiry date'}<small><span class="inventory-status ${esc(r.expiry_status.toLowerCase().replaceAll(' ','-'))}">${esc(r.expiry_status)}</span></small></td><td>${esc(r.count_date)}<small>${esc(r.counted_by)}</small></td><td><div class="inventory-row-actions">${r.deleted_at?`<button type="button" data-inventory-action="restore" data-record="${esc(r.id)}">Restore</button>`:`<button type="button" data-inventory-action="edit" data-record="${esc(r.id)}">Edit</button><button type="button" data-inventory-action="delete" data-record="${esc(r.id)}">Delete</button>`}</div></td></tr>`).join('')||'<tr><td colspan="6">No batch records found. Add a count or change the filters.</td></tr>';
  $('inventory-page').textContent=`Page ${result.page} of ${result.pages} · ${result.total} records`;$('inventory-prev').disabled=result.page<=1;$('inventory-next').disabled=result.page>=result.pages;labelTables();inventoryFeedback('');return result;
 }catch(e){if(epoch===inventoryEpoch)inventoryFeedback(e.message,true);return null;}
}
function inventoryProducts(query){const q=query.trim().toLowerCase();return q&&data?.products?data.products.filter(p=>['Product Name','SKU','Barcode'].some(k=>String(p[k]||'').toLowerCase().includes(q))):[];}
function inventorySearch(){
 const q=$('inventory-product-search').value,matches=inventoryProducts(q);
 $('inventory-product-results').innerHTML=matches.slice(0,20).map(p=>`<button type="button" data-inventory-product="${esc(p.id)}"><b>${esc(p['Product Name'])}</b><small>${esc(p.SKU)||'No SKU'}${p.Barcode?' · '+esc(p.Barcode):''}</small></button>`).join('')+(matches.length>20?'<p class="note">Showing the first 20 matches. Refine your search to find more.</p>':'')||`<p class="note">${q.trim()?'No matching products.':'Search the full catalog by name, SKU or barcode.'}</p>`;
}
function inventoryChoose(product){inventoryProduct=product;$('inventory-selected').textContent=product?product['Product Name']+' · '+(product.SKU||'No SKU'):'No product selected.';$('inventory-product-summary').textContent=$('inventory-selected').textContent;}
function inventoryApplyBarcode(raw){
 const code=String(raw).trim();if(!code)throw Error('Scan or enter a barcode first.');
 const found=data.products.filter(p=>String(p.Barcode||'').split(',').some(b=>b.trim()===code));
 $('inventory-product-search').value=code;inventorySearch();inventoryChoose(null);
 if(found.length!==1){inventoryShowStep(1);inventoryFeedback(found.length?'More than one product has this barcode. Choose the correct product by name.':'Barcode not found in the catalog. Search by name or add the product to the catalog first.',true,true);return;}
 inventoryChoose(found[0]);inventoryShowStep(2);
}
function inventoryScanDraft(){
 return {token:inventoryToken,record:inventoryEditing,productId:inventoryProduct?.id||'',values:Object.fromEntries(['date','branch','person','quantity','batch','expiry','notes','product-search'].map(k=>[k,$('inventory-'+k).value]))};
}
async function inventoryRestoreScan(draft){
 if(!draft?.values||typeof draft.token!=='string'||!draft.token)throw Error('The inventory draft is unavailable. Reopen Add count and scan again.');
 view('manual-inventory');
 if(!await loadManualInventory())throw Error('Could not load inventory. Try again.');
 if(!inventoryData.branches.includes(draft.values.branch))throw Error('The saved branch is unavailable. Reopen Add count.');
 await inventoryOpen(draft.record||null);
 for(const key of ['date','branch','person','quantity','batch','expiry','notes','product-search'])$('inventory-'+key).value=typeof draft.values[key]==='string'?draft.values[key]:'';
 inventoryToken=draft.token;inventoryChoose(data.products.find(p=>p.id===draft.productId)||null);inventorySearch();inventoryShowStep(1);
}
function inventoryShowStep(step){inventoryEmployeeClose();inventoryStep=step;document.querySelectorAll('[data-inventory-step]').forEach(e=>e.hidden=Number(e.dataset.inventoryStep)!==step);$('inventory-step-label').textContent=`Step ${step+1} of 3`;$('inventory-back').hidden=step===0;$('inventory-next-step').hidden=step===2;$('inventory-save').hidden=step!==2;inventoryFeedback('',false,true);$('inventory-dialog').scrollTop=0;}
function inventoryValidate(step){
 if(step===0){for(const id of ['inventory-date','inventory-branch','inventory-person']){if(!$(id).checkValidity()){$(id).reportValidity();return false;}}if(!$('inventory-person').value.trim()){inventoryFeedback('Enter the name of the person who counted the stock.',true,true);return false;}}
 if(step===1&&!inventoryProduct){inventoryFeedback('Choose a product from the search results.',true,true);return false;}
 if(step===2){for(const id of ['inventory-quantity','inventory-batch','inventory-expiry','inventory-notes'])if(!$(id).checkValidity()){$(id).reportValidity();return false;}}
 return true;
}
async function inventoryOpen(record=null){
 if(inventoryBusy)return;
 if(!record||!inventoryData){if(!await loadManualInventory())return;}
 if(!inventoryData||!data?.products){inventoryFeedback('The catalog is still loading. Try again in a moment.',true);return;}
 inventoryEditing=record;inventoryToken=inventoryData.submission_id;
 $('inventory-form').reset();$('inventory-date').max=inventoryData.today;$('inventory-date').value=record?.count_date||inventoryData.today;$('inventory-branch').value=record?.branch||$('inventory-filter-branch').value;$('inventory-person').value=record?.counted_by||'';
 $('inventory-quantity').value=record?.quantity??'';$('inventory-batch').value=record?.batch||'';$('inventory-expiry').value=record?.expiry_date||'';$('inventory-notes').value=record?.notes||'';
 inventoryChoose(record?data.products.find(p=>p.id===record.product_id):null);inventorySearch();inventoryShowStep(0);
 $('inventory-dialog-title').textContent=record?'Edit inventory count':'Add inventory count';$('inventory-save').textContent=record?'Save changes':'Save count';$('inventory-dialog').showModal();
 inventoryEmployeeLoad().catch(()=>{});
}
function inventorySetBusy(value){inventoryBusy=value;for(const id of ['inventory-close','inventory-back','inventory-next-step','inventory-save','inventory-delete-confirm','inventory-delete-cancel','inventory-person-toggle'])$(id).disabled=value;$('inventory-dialog').querySelectorAll('input,select,textarea').forEach(e=>e.disabled=value);}
$('inventory-person').onfocus=()=>{if(!inventoryBusy)inventoryEmployeeRender();};$('inventory-person').oninput=()=>inventoryEmployeeRender();
$('inventory-person-toggle').onclick=()=>{if(inventoryBusy)return;if(!$('inventory-people').hidden){inventoryEmployeeClose();return;}$('inventory-person').focus();inventoryEmployeeRender(true);};
$('inventory-people').onclick=event=>{const option=event.target.closest('[data-employee-index]');if(option)inventoryEmployeeSelect(inventoryEmployeeOptions[Number(option.dataset.employeeIndex)]);};
$('inventory-person').addEventListener('keydown',event=>{
 if(event.key==='Escape'&&!$('inventory-people').hidden){event.preventDefault();event.stopPropagation();inventoryEmployeeClose();return;}
 if(event.key==='Tab'){inventoryEmployeeClose();return;}
 if(event.key==='Enter'&&!$('inventory-people').hidden){event.preventDefault();if(inventoryEmployeeActive>=0)inventoryEmployeeSelect(inventoryEmployeeOptions[inventoryEmployeeActive]);else inventoryEmployeeClose();return;}
 if(!['ArrowDown','ArrowUp'].includes(event.key))return;event.preventDefault();if($('inventory-people').hidden)inventoryEmployeeRender();if(!inventoryEmployeeOptions.length)return;
 inventoryEmployeeActive=inventoryEmployeeActive<0?(event.key==='ArrowDown'?0:inventoryEmployeeOptions.length-1):(inventoryEmployeeActive+(event.key==='ArrowDown'?1:-1)+inventoryEmployeeOptions.length)%inventoryEmployeeOptions.length;
 $('inventory-person').setAttribute('aria-activedescendant','inventory-employee-'+inventoryEmployeeActive);
 $('inventory-people').querySelectorAll('[role="option"]').forEach((option,index)=>{option.setAttribute('aria-selected',String(index===inventoryEmployeeActive));if(index===inventoryEmployeeActive)option.scrollIntoView({block:'nearest'});});
});
document.addEventListener('click',event=>{if(!$('inventory-person-field').contains(event.target))inventoryEmployeeClose();});
$('inventory-form').noValidate=true;
$('inventory-form').onsubmit=async event=>{
 event.preventDefault();if(inventoryBusy)return;
 // Enter in the search field advances the step; it never saves an incomplete count.
 if(inventoryStep<2){if(inventoryValidate(inventoryStep))inventoryShowStep(inventoryStep+1);return;}
 for(let step=0;step<3;step++){if(!inventoryValidate(step)){inventoryShowStep(step);inventoryValidate(step);return;}}
 const payload={submission_id:inventoryToken,branch:$('inventory-branch').value,product_id:inventoryProduct.id,count_date:$('inventory-date').value,counted_by:$('inventory-person').value.trim(),quantity:$('inventory-quantity').value,batch:$('inventory-batch').value.trim(),expiry_date:$('inventory-expiry').value,notes:$('inventory-notes').value.trim()};
 if(inventoryEditing)payload.revision=inventoryEditing.revision;
 inventorySetBusy(true);inventoryFeedback('Saving count...',false,true);
 try{await api(inventoryEditing?`/api/manual-inventory/${encodeURIComponent(inventoryEditing.id)}/edit`:'/api/manual-inventory',payload);$('inventory-dialog').close();await loadManualInventory();}
 catch(e){inventoryFeedback(e.message,true,true);}finally{inventorySetBusy(false);}
};
$('inventory-next-step').onclick=()=>{if(!inventoryBusy&&inventoryValidate(inventoryStep))inventoryShowStep(inventoryStep+1);};$('inventory-back').onclick=()=>{if(!inventoryBusy)inventoryShowStep(inventoryStep-1);};
$('inventory-close').onclick=()=>{if(!inventoryBusy)$('inventory-dialog').close();};$('inventory-dialog').addEventListener('cancel',e=>{if(inventoryBusy)e.preventDefault();});
$('inventory-add').onclick=()=>inventoryOpen();document.querySelectorAll('[data-inventory-open]').forEach(b=>b.onclick=()=>view('manual-inventory'));
$('inventory-product-search').oninput=inventorySearch;
$('inventory-product-search').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();const code=event.target.value.trim();const found=data.products.some(p=>String(p.Barcode||'').split(',').some(b=>b.trim()===code));if(found)inventoryApplyBarcode(code);else inventoryFeedback('Choose a matching product, or scan a barcode from the catalog.',true,true);}});
$('inventory-product-results').onclick=event=>{const b=event.target.closest('[data-inventory-product]');if(b)inventoryChoose(data.products.find(p=>p.id===b.dataset.inventoryProduct));};
$('inventory-records').onclick=async event=>{
 const button=event.target.closest('[data-inventory-action]');if(!button||inventoryBusy)return;const record=inventoryData?.records.find(r=>r.id===button.dataset.record);if(!record)return;
 const action=button.dataset.inventoryAction;
 if(action==='edit')return inventoryOpen(record);
 if(action==='delete'){inventoryDeleting=record;$('inventory-delete-summary').textContent=`${record.product_name} · ${record.branch} · ${record.quantity} units`;$('inventory-delete-feedback').textContent='';$('inventory-delete-dialog').showModal();return;}
 if(action==='restore'){inventorySetBusy(true);button.disabled=true;try{await api(`/api/manual-inventory/${encodeURIComponent(record.id)}/restore`,{revision:record.revision});await loadManualInventory();}catch(e){inventoryFeedback(e.message,true);}finally{inventorySetBusy(false);button.disabled=false;}}
};
$('inventory-delete-cancel').onclick=()=>$('inventory-delete-dialog').close();$('inventory-delete-dialog').addEventListener('cancel',e=>{if(inventoryBusy)e.preventDefault();});
$('inventory-delete-confirm').onclick=async()=>{if(inventoryBusy||!inventoryDeleting)return;inventorySetBusy(true);try{await api(`/api/manual-inventory/${encodeURIComponent(inventoryDeleting.id)}/delete`,{revision:inventoryDeleting.revision});$('inventory-delete-dialog').close();await loadManualInventory();}catch(e){$('inventory-delete-feedback').textContent=e.message;$('inventory-delete-feedback').className='error';}finally{inventorySetBusy(false);}};
for(const id of ['inventory-filter-branch','inventory-filter-status','inventory-filter-deleted'])$(id).onchange=()=>{inventoryPage=1;loadManualInventory();};
$('inventory-filter-search').oninput=()=>{clearTimeout(inventorySearchTimer);inventorySearchTimer=setTimeout(()=>{inventoryPage=1;loadManualInventory();},250);};
$('inventory-prev').onclick=()=>{inventoryPage--;loadManualInventory();};$('inventory-next').onclick=()=>{inventoryPage++;loadManualInventory();};
