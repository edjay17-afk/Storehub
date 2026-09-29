let wastePage=1,wastePages=1,wasteRecords=[],wasteProduct=null,wastePhoto=null,wastePhotoUrl='',wasteToken='',wasteReady=false,wasteSaving=false,wasteEditing=null,wastePhotoAction='keep',wasteDeleted=false,wasteWizardStep=0;
function wasteFeedback(text,error=false){$('waste-feedback').className=error?'error':'success';$('waste-feedback').textContent=text;}
function wastePageFeedback(text,error=false){$('waste-page-feedback').className=error?'error':'success';$('waste-page-feedback').textContent=text;}
async function loadWastage(){
 const finish=window.WarehouseUI?.loading($('waste-records').closest('.table-wrap'))||(()=>{});
 const params=new URLSearchParams({page:wastePage});if(wasteDeleted)params.set('deleted','1');
 for(const [key,id] of [['waste_date','waste-filter-date'],['branch','waste-filter-branch'],['search','waste-filter-search']])if($(id).value)params.set(key,$(id).value);
 try{
  const response=await fetch('/api/wastage?'+params);const result=await response.json();if(!response.ok)throw Error(result.error||'Could not load wastage records.');
  if(!wasteReady){
   $('waste-date').value=result.today;$('waste-date').max=result.today;
   const options=result.branches.map(b=>`<option value="${esc(b)}">${esc(b)}</option>`).join('');$('waste-branch').innerHTML='<option value="">Choose a branch</option>'+options;$('waste-filter-branch').innerHTML='<option value="">All branches</option>'+options;wasteReady=true;
  }
  if(!wasteToken)wasteToken=result.submission_id;
  wastePage=result.page;wastePages=result.pages;wasteRecords=result.records;
  $('waste-count').textContent=`${result.total.toLocaleString()} ${result.total===1?'record':'records'}`;
  $('waste-records').innerHTML=wasteRecords.map(r=>`<tr><td>${esc(r.waste_date)}</td><td>${esc(r.branch)}</td><td>${esc(r.product_name)}<small>${esc(r.sku)}</small>${r.source?.record_id?`<small>StoreHub ${esc(r.source.record_id)} | ${esc(r.source.reason)}</small>`:''}</td><td class="num">${esc(r.quantity)}</td><td>${r.has_photo?'Attached':'\u2014'}</td><td>${wasteDeleted?`<button type="button" data-waste-restore="${esc(r.id)}">Restore</button>`:`<div class="waste-record-actions"><button type="button" data-waste-detail="${esc(r.id)}">View</button><button type="button" data-waste-edit="${esc(r.id)}">Edit</button><button type="button" data-waste-delete="${esc(r.id)}" class="waste-delete">Delete</button></div>`}</td></tr>`).join('')||'<tr><td colspan="6">No wastage records found.</td></tr>';
  $('waste-page').textContent=`Page ${wastePage} of ${wastePages}`;$('waste-prev').disabled=wastePage<=1;$('waste-next').disabled=wastePage>=wastePages;
  $('waste-list-feedback').textContent='';labelTables();
  document.querySelectorAll('[data-waste-detail]').forEach(b=>b.onclick=()=>showWasteRecord(b.dataset.wasteDetail));
  bindWasteActions();
 }catch(e){$('waste-list-feedback').className='error';$('waste-list-feedback').textContent=e.message;}finally{finish();}
 wasteSteps();
}
function wasteSteps(){
 const ready=wasteReady&&$('waste-date').value&&$('waste-branch').value;
 $('waste-product-step').disabled=!ready||wasteSaving;$('waste-detail-step').disabled=!ready||!wasteProduct||wasteSaving;
 $('waste-save').disabled=!ready||!wasteProduct||!wasteToken||wasteSaving||wasteWizardStep!==3;
 $('waste-next-step').disabled=!wasteReady||wasteSaving;$('waste-back').disabled=wasteSaving;$('waste-modal-close').disabled=wasteSaving;
 $('waste-new').disabled=!wasteReady||wasteSaving;
 $('waste-date').disabled=wasteSaving;$('waste-branch').disabled=wasteSaving;
 $('waste-search-note').textContent=ready?(wasteProduct?'Quantity uses the product\'s base unit.':'Search and click a product below.'):'Select a date and branch to begin.';
}
function wasteSearch(){
 const query=$('waste-search').value.trim().toLowerCase();$('waste-options').innerHTML='';
 if(!query)return;
 if(!data){$('waste-options').innerHTML='<p>Products are loading. Try again shortly.</p>';return;}
 const found=data.products.filter(p=>matches(p,query));
 $('waste-options').innerHTML=found.slice(0,30).map(p=>`<button type="button" data-waste-product="${esc(p.id)}">${esc(p['Product Name'])}<small>${esc(p.SKU)} ${p.Barcode?'\u00b7 '+esc(p.Barcode):''}</small></button>`).join('')||'<p>No matching products.</p>';
 if(found.length>30)$('waste-options').insertAdjacentHTML('beforeend','<p class="note">Showing 30 matches. Refine your search for more.</p>');
 $('waste-options').querySelectorAll('[data-waste-product]').forEach(b=>b.onclick=()=>{
  wasteProduct=data.products.find(p=>p.id===b.dataset.wasteProduct);$('waste-options').innerHTML='';$('waste-search').value='';renderWasteProduct();wasteSteps();$('waste-next-step').focus();
 });
}
function renderWasteProduct(){
 $('waste-selected').classList.toggle('hidden',!wasteProduct);
 $('waste-selected').innerHTML=wasteProduct?`<div><strong>${esc(wasteProduct['Product Name'])}</strong><small>SKU ${esc(wasteProduct.SKU)||'\u2014'}${wasteProduct.Unit?' \u00b7 '+esc(wasteProduct.Unit):''}</small></div><button type="button" id="waste-change-product">Change</button>`:'';
 $('waste-search').disabled=!!wasteProduct;
 if(wasteProduct)$('waste-change-product').onclick=()=>{wasteProduct=null;$('waste-quantity').value='';clearWastePhoto();renderWasteProduct();wasteSteps();$('waste-search').focus();};
}
function clearWastePhoto(){wastePhotoAction=wasteEditing?'remove':'keep';if(wastePhotoUrl)URL.revokeObjectURL(wastePhotoUrl);wastePhotoUrl='';wastePhoto=null;$('waste-photo-preview').hidden=true;$('waste-photo-preview').removeAttribute('src');$('waste-remove-photo').hidden=true;$('waste-file').value='';$('waste-camera-file').value='';}
function setWastePhoto(file){if(!file)return;if(file.size>10*1024*1024){wasteFeedback('Photo must be 10 MB or smaller.',true);return;}if(!['image/jpeg','image/png','image/webp'].includes(file.type)){wasteFeedback('Use a JPEG, PNG or WebP photo.',true);return;}clearWastePhoto();wastePhoto=file;wastePhotoAction='replace';wastePhotoUrl=URL.createObjectURL(file);$('waste-photo-preview').src=wastePhotoUrl;$('waste-photo-preview').hidden=false;$('waste-remove-photo').hidden=false;wasteFeedback('Photo attached. Save the record when ready.');}
function showWasteRecord(id){const r=wasteRecords.find(r=>r.id===id);if(!r)return;$('waste-detail-body').innerHTML=`<div class="po-detail-grid"><p><strong>Date</strong><br>${esc(r.waste_date)}</p><p><strong>Branch</strong><br>${esc(r.branch)}</p><p><strong>Product</strong><br>${esc(r.product_name)}<br><small>${esc(r.sku)}</small></p><p><strong>Wasted quantity</strong><br>${esc(r.quantity)}</p></div>${r.source?.record_id?`<div class="notice"><strong>StoreHub ${esc(r.source.record_id)}</strong><p>Reason: ${esc(r.source.reason)} | Created by ${esc(r.source.created_by)}</p><p>Original unit cost ${formatCost(r.source.unit_cost)} | Original subtotal ${formatCost(r.source.subtotal)}</p><p>Source date: ${esc(r.source.original_created.replace('T',' '))} | ${esc(r.source.filename)}</p><p class="note">Original export values are retained when you edit this local record.</p></div>`:''}<p><strong>Notes</strong><br>${esc(r.notes)||'No notes'}</p><p class="note">Recorded ${esc(new Date(r.created).toLocaleString())}</p>${r.updated_at?`<p class="note">Last changed ${esc(new Date(r.updated_at).toLocaleString())}</p>`:''}<div class="waste-record-actions"><button type="button" data-waste-edit="${esc(r.id)}">Edit record</button><button type="button" class="waste-delete" data-waste-delete="${esc(r.id)}">Delete record</button></div>${r.has_photo?`<img loading="lazy" decoding="async" class="waste-detail-photo" src="/api/wastage/${encodeURIComponent(r.id)}/photo" alt="Photo of ${esc(r.product_name)} wastage">`:'<p class="note">No photo attached.</p>'}`;$('waste-detail-dialog').showModal();bindWasteActions();}
document.querySelectorAll('[data-waste-open]').forEach(b=>b.onclick=()=>{view('wastage');window.scrollTo({top:0,behavior:'instant'});});
$('waste-date').onchange=wasteSteps;$('waste-branch').onchange=wasteSteps;$('waste-search').oninput=wasteSearch;
$('waste-upload').onclick=()=>$('waste-file').click();$('waste-capture').onclick=()=>$('waste-camera-file').click();
$('waste-file').onchange=e=>setWastePhoto(e.target.files[0]);$('waste-camera-file').onchange=e=>setWastePhoto(e.target.files[0]);$('waste-remove-photo').onclick=clearWastePhoto;
$('waste-form').onsubmit=async e=>{
 e.preventDefault();if(wasteWizardStep!==3){advanceWasteStep();return;}if(wasteSaving||!wasteProduct||!wasteToken||!validateWasteStep(0)||!validateWasteStep(1)||!validateWasteStep(2))return;
 const form=new FormData(e.target);form.set('submission_id',wasteToken);form.set('product_id',wasteProduct.id);if(wasteEditing){form.set('revision',wasteEditing.revision);form.set('photo_action',wastePhotoAction);}if(wastePhoto)form.set('photo',wastePhoto);
 wasteSaving=true;wasteSteps();$('waste-save').textContent='Saving...';wasteFeedback('Saving wastage record...');
 try{const url=wasteEditing?`/api/wastage/${encodeURIComponent(wasteEditing.id)}/edit`:'/api/wastage';const response=await fetch(url,{method:'POST',body:form});const result=await response.json();if(!response.ok)throw Error(result.error||'Could not save wastage.');try{window.WarehouseNotifications?.success(url,result);}catch{}
  const edited=!!wasteEditing;wasteEditing=null;updateWasteEditMode();wasteProduct=null;wasteToken='';$('waste-quantity').value='';$('waste-notes').value='';$('waste-search').value='';clearWastePhoto();renderWasteProduct();
  $('waste-filter-form').reset();wasteDeleted=false;wastePage=1;await loadWastage();$('waste-trash-toggle').textContent='Deleted records';$('waste-entry-dialog').close();wastePageFeedback(edited?'Wastage record updated.':'Wastage record saved.');
 }catch(error){wasteFeedback(error.message+' Your entries have been kept; retry when ready.',true);}
 finally{wasteSaving=false;updateWasteEditMode();wasteSteps();}
};
$('waste-filter-form').onsubmit=e=>{e.preventDefault();wastePage=1;loadWastage();};$('waste-filter-clear').onclick=()=>{$('waste-filter-form').reset();wastePage=1;loadWastage();};
$('waste-prev').onclick=()=>{wastePage--;loadWastage();};$('waste-next').onclick=()=>{wastePage++;loadWastage();};$('waste-detail-close').onclick=()=>$('waste-detail-dialog').close();
function updateWasteEditMode(){
 $('waste-form-title').textContent=wasteEditing?'Edit wastage record':'Record wastage';
 $('waste-save').textContent=wasteEditing?'Save changes':'Save wastage record';
 $('waste-edit-cancel').disabled=wasteSaving;
 $('waste-edit-note').hidden=!wasteEditing;$('waste-edit-note').textContent=wasteEditing?'Changes update this existing record. Your attached photo is kept unless you replace or remove it.':'';
}
function cancelWasteEdit(){
 if(wasteSaving)return;wasteEditing=null;wasteProduct=null;clearWastePhoto();$('waste-search').value='';$('waste-options').innerHTML='';$('waste-quantity').value='';$('waste-notes').value='';renderWasteProduct();updateWasteEditMode();wasteSteps();wasteFeedback('');
}
function editWasteRecord(id){
 if(wasteSaving)return;const record=wasteRecords.find(r=>r.id===id);if(!record||record.deleted_at)return;
 cancelWasteEdit();wasteEditing={...record};wasteProduct=data?.products.find(p=>p.id===record.product_id)||{id:record.product_id,'Product Name':record.product_name,SKU:record.sku};
 $('waste-date').value=record.waste_date;$('waste-branch').value=record.branch;$('waste-quantity').value=record.quantity;$('waste-notes').value=record.notes;
 wastePhotoAction='keep';if(record.has_photo){$('waste-photo-preview').src=`/api/wastage/${encodeURIComponent(record.id)}/photo?v=${record.revision}`;$('waste-photo-preview').hidden=false;$('waste-remove-photo').hidden=false;}
 renderWasteProduct();updateWasteEditMode();wasteSteps();$('waste-detail-dialog').close();openWasteWizard();
}
async function changeWasteDeleted(id,deleted,confirmed=false){
 if(wasteSaving)return;const record=wasteRecords.find(r=>r.id===id);if(!record)return;
 if(deleted&&!confirmed){
  $('waste-delete-summary').textContent=`${record.product_name} · ${record.quantity} units · ${record.branch} · ${record.waste_date}`;
  $('waste-delete-confirm').onclick=()=>{$('waste-delete-dialog').close();changeWasteDeleted(id,true,true);};
  $('waste-detail-dialog').close();$('waste-delete-dialog').showModal();return;
 }
 wasteSaving=true;wasteSteps();updateWasteEditMode();
 try{
  await api(`/api/wastage/${encodeURIComponent(id)}/${deleted?'delete':'restore'}`,{revision:record.revision});
  if(wasteEditing?.id===id){wasteSaving=false;cancelWasteEdit();wasteSaving=true;}
  $('waste-detail-dialog').close();await loadWastage();wastePageFeedback(deleted?'Wastage record deleted. It can be restored from Deleted records.':'Wastage record restored.');
 }catch(error){wastePageFeedback(error.message,true);}
 finally{wasteSaving=false;wasteSteps();updateWasteEditMode();}
}
function bindWasteActions(){
 document.querySelectorAll('[data-waste-edit]').forEach(b=>b.onclick=()=>editWasteRecord(b.dataset.wasteEdit));
 document.querySelectorAll('[data-waste-delete]').forEach(b=>b.onclick=()=>changeWasteDeleted(b.dataset.wasteDelete,true));
 document.querySelectorAll('[data-waste-restore]').forEach(b=>b.onclick=()=>changeWasteDeleted(b.dataset.wasteRestore,false));
}
$('waste-edit-cancel').onclick=closeWasteWizard;
$('waste-trash-toggle').onclick=()=>{wasteDeleted=!wasteDeleted;wastePage=1;$('waste-trash-toggle').textContent=wasteDeleted?'Active records':'Deleted records';loadWastage();};
$('waste-delete-cancel').onclick=()=>$('waste-delete-dialog').close();
function showWasteStep(step){
 wasteWizardStep=step;
 document.querySelectorAll('[data-waste-step]').forEach(panel=>panel.hidden=Number(panel.dataset.wasteStep)!==step);
 document.querySelectorAll('#waste-progress li').forEach((item,index)=>{item.classList.toggle('current',index===step);item.classList.toggle('complete',index<step);if(index===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});
 $('waste-step-caption').textContent=`Step ${step+1} of 4 · ${['Date and branch','Select a product','Quantity and photo','Review and save'][step]}`;
 $('waste-back').hidden=step===0;$('waste-next-step').hidden=step===3;$('waste-save').hidden=step!==3;
 wasteFeedback('');if(step===3)renderWasteReview();wasteSteps();
 document.querySelector('.waste-wizard-body').scrollTop=0;
 if($('waste-entry-dialog').open){const focusId=['waste-date',wasteProduct?'waste-next-step':'waste-search','waste-quantity','waste-save'][step];$(focusId).focus({preventScroll:true});}
}
function validateWasteStep(step){
 if(step===0){for(const id of ['waste-date','waste-branch'])if(!$(id).checkValidity()){showWasteStep(0);$(id).reportValidity();return false;}}
 if(step===1&&!wasteProduct){showWasteStep(1);wasteFeedback('Search and select a product before continuing.',true);return false;}
 if(step===2){const input=$('waste-quantity'),quantity=Number(input.value),fraction=String(input.value).split('.')[1];if(!input.checkValidity()||!Number.isFinite(quantity)||quantity<=0||(fraction&&fraction.length>6)){showWasteStep(2);wasteFeedback('Enter a positive wasted quantity with up to 6 decimal places.',true);input.reportValidity();input.focus();return false;}}
 return true;
}
function advanceWasteStep(){if(!wasteSaving&&wasteWizardStep<3&&validateWasteStep(wasteWizardStep))showWasteStep(wasteWizardStep+1);}
function renderWasteReview(){
 const photoSrc=wastePhotoUrl||(wasteEditing?.has_photo&&wastePhotoAction==='keep'?`/api/wastage/${encodeURIComponent(wasteEditing.id)}/photo?v=${wasteEditing.revision}`:'');
 $('waste-review-summary').innerHTML=`<dl class="waste-review-grid"><div><dt>Date</dt><dd>${esc($('waste-date').value)}</dd></div><div><dt>Branch</dt><dd>${esc($('waste-branch').value)}</dd></div><div class="waste-review-product"><dt>Product</dt><dd>${esc(wasteProduct?.['Product Name'])}<small>${esc(wasteProduct?.SKU)}</small></dd></div><div><dt>Wasted quantity</dt><dd>${esc($('waste-quantity').value)}</dd></div><div><dt>Photo</dt><dd>${photoSrc?'Attached':'No photo'}</dd></div><div class="waste-review-product"><dt>Notes</dt><dd>${esc($('waste-notes').value)||'No notes'}</dd></div></dl>${photoSrc?`<img loading="lazy" decoding="async" class="waste-review-photo" src="${esc(photoSrc)}" alt="Wastage photo for review">`:''}`;
}
function openWasteWizard(){showWasteStep(0);$('waste-entry-dialog').showModal();$('waste-date').focus();}
function closeWasteWizard(){if(wasteSaving)return;$('waste-entry-dialog').close();cancelWasteEdit();showWasteStep(0);}
$('waste-new').onclick=()=>{cancelWasteEdit();openWasteWizard();};
$('waste-next-step').onclick=advanceWasteStep;
$('waste-back').onclick=()=>{if(!wasteSaving)showWasteStep(Math.max(0,wasteWizardStep-1));};
$('waste-modal-close').onclick=closeWasteWizard;
$('waste-entry-dialog').addEventListener('cancel',event=>{event.preventDefault();closeWasteWizard();});
