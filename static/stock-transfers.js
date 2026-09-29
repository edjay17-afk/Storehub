let stState=null,stPage=1,stSequence=0,stPreview=null,stUploadSequence=0,stBusy=false,stDetailSequence=0,stSearchTimer;
function stFeedback(text,error=false){$('st-feedback').textContent=text;$('st-feedback').className=error?'error':'success';}
function stParams(){return new URLSearchParams({page:stPage,size:$('st-size').value,q:$('st-search').value.trim(),status:$('st-status').value,location:$('st-location').value,from:$('st-from').value,to:$('st-to').value});}
async function loadImportedTransfers(){
 const finish=window.WarehouseUI?.loading($('st-list'))||(()=>{});try{
 const sequence=++stSequence;
 const response=await fetch('/api/imported-transfers?'+stParams(),{cache:'no-store'}),result=await response.json();
 if(sequence!==stSequence)return;
 if(!response.ok)throw Error(result.error||'Could not load imported transfers.');
 stState=result;stPage=result.page;
 const chosenStatus=$('st-status').value,chosenLocation=$('st-location').value;
 $('st-status').innerHTML='<option value="">All statuses</option>'+[...new Set([...result.statuses,...(chosenStatus?[chosenStatus]:[])])].map(s=>`<option value="${esc(s)}" ${s===chosenStatus?'selected':''}>${esc(s)}</option>`).join('');
 $('st-location').innerHTML='<option value="">All active locations</option>'+result.locations.map(s=>`<option value="${esc(s)}" ${s===chosenLocation?'selected':''}>${esc(s)}</option>`).join('');
 $('st-summary').textContent=`${result.total} matching transfer${result.total===1?'':'s'} · ${result.allTotal} imported reports`;
 $('st-list').innerHTML=result.orders.map(o=>`<article class="card po-order"><div class="po-order-top"><h2>${esc(o.id)}</h2><span class="badge">${esc(o.status)}</span></div><p class="po-supplier">${esc(o.source)}<br><span aria-hidden="true">→ </span>${esc(o.target)}</p><p class="po-dates">Created ${esc(o.created.replace('T',' '))} · Imported ${esc(poDate(o.imported))}</p><div class="sh-values"><span>Product lines<b>${o.lines}</b></span></div><div class="po-order-actions"><button data-st-detail="${esc(o.id)}">View transfer</button><a class="po-download" href="/api/imported-transfers/${encodeURIComponent(o.id)}.csv" aria-label="Download ${esc(o.id)} CSV" title="Download stock transfer CSV"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 15v6h16v-6M12 3v12m-5-5 5 5 5-5"/></svg><span>CSV</span></a></div></article>`).join('')||'<div class="card"><h2>No matching StoreHub transfers</h2><p>Import a Stock Transfer CSV above, or choose other filters.</p></div>';
 $('st-page').textContent=`Page ${result.page} of ${result.pages}`;
 $('st-prev').disabled=result.page===1;$('st-next').disabled=result.page>=result.pages;
 document.querySelectorAll('[data-st-detail]').forEach(b=>b.onclick=()=>showImportedTransfer(b.dataset.stDetail));
 }finally{finish();}
}
function stClearPreview(){stUploadSequence++;stPreview=null;$('st-preview').classList.add('hidden');$('st-preview-list').innerHTML='';$('st-confirm').disabled=true;}
function stControls(busy){stBusy=busy;['st-file','st-preview-button','st-confirm','st-discard'].forEach(id=>$(id).disabled=busy);if(!busy)$('st-confirm').disabled=!stPreview?.orders.length;}
$('st-file').onchange=()=>{stClearPreview();stFeedback('');};
$('st-discard').onclick=()=>{stClearPreview();stFeedback('Preview cancelled. No transfer reports were imported.');};
$('st-upload-form').onsubmit=async event=>{
 event.preventDefault();if(stBusy)return;
 stClearPreview();const sequence=stUploadSequence,file=$('st-file').files[0];if(!file)return stFeedback('Choose a StoreHub Stock Transfer CSV.',true);
 if(file.size>5*1024*1024)return stFeedback('Upload a CSV file no larger than 5 MB.',true);
 stControls(true);stFeedback('Checking the StoreHub export...');
 try{
  const body=new FormData();body.append('file',file);
  const response=await fetch('/api/imported-transfers/preview',{method:'POST',body}),result=await response.json();
  if(sequence!==stUploadSequence)return;
  if(!response.ok)throw Error(result.error||'Could not preview this CSV.');
  stPreview=result;
  $('st-preview-summary').textContent=`${result.filename}: ${result.counts.new} new, ${result.counts.updated} updated, ${result.counts.unchanged} unchanged. ${result.skipped.length} transfers involving store 688 excluded. Stock balances will stay unchanged.`;
  $('st-preview-list').innerHTML=result.orders.map((o,i)=>`<tr><td>${esc(o.id)}</td><td>${esc(o.source)}<br>→ ${esc(o.target)}</td><td>${o.previousStatus&&o.previousStatus!==o.status?`${esc(o.previousStatus)} → `:''}${esc(o.status)}</td><td>${o.rows.length-1}</td><td>${esc(o.change)}</td><td><button type="button" data-st-preview-detail="${i}" aria-label="Preview ${esc(o.id)} details">View</button></td></tr>`).join('')||'<tr><td colspan="6">No active transfers in this file.</td></tr>';
  document.querySelectorAll('[data-st-preview-detail]').forEach(b=>b.onclick=()=>showImportedTransfer('',stPreview.orders[Number(b.dataset.stPreviewDetail)]));
  $('st-preview').classList.remove('hidden');labelTables();stFeedback('Review the transfers below, then select Import report.');
 }catch(e){stFeedback(e.message,true);}finally{stControls(false);}
};
$('st-confirm').onclick=async()=>{
 if(stBusy||!stPreview)return;
 const token=stPreview.token;stControls(true);stFeedback('Importing transfer reports...');
 try{const result=await api('/api/imported-transfers/import',{token});stClearPreview();$('st-file').value='';stPage=1;await loadImportedTransfers();stFeedback(`Imported: ${result.counts.new} new, ${result.counts.updated} updated, ${result.counts.unchanged} unchanged. ${result.skipped} excluded. Stock balances were preserved.`);}
 catch(e){stFeedback(e.message,true);}finally{stControls(false);}
};
$('st-reload').onclick=()=>loadImportedTransfers().catch(e=>stFeedback(e.message,true));
$('st-filters').onsubmit=e=>{e.preventDefault();stPage=1;loadImportedTransfers().catch(e=>stFeedback(e.message,true));};
['st-status','st-location','st-size'].forEach(id=>$(id).onchange=()=>{stPage=1;loadImportedTransfers().catch(e=>stFeedback(e.message,true));});
$('st-search').oninput=()=>{clearTimeout(stSearchTimer);stSearchTimer=setTimeout(()=>{stPage=1;loadImportedTransfers().catch(e=>stFeedback(e.message,true));},250);};
function stChangePage(step){stPage+=step;loadImportedTransfers().then(()=>$('st-list').scrollIntoView({block:'start',behavior:'instant'})).catch(e=>stFeedback(e.message,true));}
$('st-prev').onclick=()=>stChangePage(-1);$('st-next').onclick=()=>stChangePage(1);
document.querySelectorAll('[data-st-open]').forEach(b=>b.onclick=()=>{view('imported-transfers');window.scrollTo({top:0,behavior:'instant'});});
function stRenderDetails(order,preview){
 const summary=order.rows[0];$('st-detail-title').textContent=order.id+(preview?' · Preview':'');
 const meta=[['Status',order.status],['Source store',order.source],['Target store',order.target],...['Created Date','Shipped Date','Received Date','Sent By','Received By','Cancelled By','Cancelled Date'].map(h=>[h,['Sent By','Received By','Cancelled By'].includes(h)?window.StoreHubPeople?.resolve('employees',summary[h])||summary[h]||'—':summary[h]||'—'])];
 if(!preview)meta.push(['Imported',poDate(order.imported)],['Source file',order.filename]);
 $('st-detail-body').innerHTML=`<div class="po-detail-grid">${meta.map(([label,value])=>`<div><small>${esc(label)}</small><p>${esc(value)}</p></div>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>Product / SKU</th><th>Quantity</th><th>Unit</th><th>Cost (PHP)</th><th>Subtotal (PHP)</th></tr></thead><tbody>${order.rows.slice(1).map(row=>`<tr><td>${esc(row['Product Name'])}<small>${esc(row.SKU)}${row.Category?' · '+esc(row.Category):''}${row['Serial No.']?'<br>Serial: '+esc(row['Serial No.']):''}</small></td><td>${esc(row['Ordered Qty'])}</td><td>${esc(row.Unit||'—')}</td><td>${formatCost(row['Cost (RM)'])}</td><td>${formatCost(row['SubTotal (RM)'])}</td></tr>`).join('')||'<tr><td colspan="5">This exported transfer has no product lines.</td></tr>'}</tbody></table></div><p class="note">Dates and units follow the StoreHub export. Amounts are displayed in PHP without changing the numeric values. This report does not record shipping or receiving in the local stock ledger.</p>`;
 $('st-detail-export').hidden=preview;
 if(!preview)$('st-detail-export').href='/api/imported-transfers/'+encodeURIComponent(order.id)+'.csv';
 labelTables();
}
async function showImportedTransfer(id,preview=null){
 const sequence=++stDetailSequence;$('st-detail-title').textContent=id||'Import preview';$('st-detail-body').textContent='Loading transfer...';$('st-detail-export').hidden=true;
 if(!$('st-detail-dialog').open)$('st-detail-dialog').showModal();
 try{await window.StoreHubPeople?.load();let order=preview;if(!order){const response=await fetch('/api/imported-transfers/'+encodeURIComponent(id),{cache:'no-store'});order=await response.json();if(!response.ok)throw Error(order.error||'Could not load the transfer.');}if(sequence===stDetailSequence)stRenderDetails(order,!!preview);}
 catch(e){if(sequence===stDetailSequence)$('st-detail-body').textContent=e.message;}
}
$('st-close').onclick=()=>{$('st-detail-dialog').close();stDetailSequence++;};
$('st-detail-dialog').addEventListener('cancel',()=>stDetailSequence++);
