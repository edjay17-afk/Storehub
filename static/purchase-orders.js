let poState=null, poPage=1, poSequence=0, poBusy=false, poDetailId='', poDetailSequence=0, poSearchTimer;
const poNumber=v=>v===null||v===undefined||v===''?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:6});
const poDate=v=>v?new Date(v).toLocaleString():'—';
function poFeedback(text,error=false){$('po-feedback').textContent=text;$('po-feedback').className=error?'error':'success';}
function poRange(){return {from:$('po-from').value,to:$('po-to').value};}
function poParams(){return new URLSearchParams({...poRange(),q:$('po-search').value.trim(),status:$('po-status').value,store:$('po-store').value,page:poPage,size:$('po-size').value});}
function poStatusName(value){return String(value||'Unknown').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/[_-]/g,' ').replace(/^./,s=>s.toUpperCase());}
function poReference(id){return /^\d+$/.test(id)?'PO'+id.padStart(4,'0'):id;}
async function loadPurchaseOrders(){
 const finish=window.WarehouseUI?.loading($('po-list'))||(()=>{});try{
 const sequence=++poSequence;
 await window.StoreHubPeople?.load();
 $('po-summary').textContent='Loading purchase orders...';
 const response=await fetch('/api/storehub/purchase-orders?'+poParams(),{cache:'no-store'});
 const result=await response.json();
 if(sequence!==poSequence)return;
 if(!response.ok)throw Error(result.error||'Could not load purchase orders.');
 poState=result;poPage=result.page;
 const chosenStatus=$('po-status').value,chosenStore=$('po-store').value;
 const statuses=[...new Set([...result.statuses,...(chosenStatus?[chosenStatus]:[])])];
 $('po-status').innerHTML='<option value="">All statuses</option>'+statuses.map(s=>`<option value="${esc(s)}" ${s===chosenStatus?'selected':''}>${esc(poStatusName(s))}</option>`).join('');
 $('po-store').innerHTML='<option value="">All active locations</option>'+result.locations.map(s=>`<option value="${esc(s.id)}" ${s.id===chosenStore?'selected':''}>${esc(s.name)}</option>`).join('');
 $('po-summary').textContent=`${result.total.toLocaleString()} purchase order${result.total===1?'':'s'} · ${result.refreshed?'Last refreshed '+poDate(result.refreshed):'This date range has not been refreshed. Tap Refresh orders to read it from StoreHub.'}`;
 $('po-list').innerHTML=result.orders.map(o=>`<article class="card po-order"><div class="po-order-top"><h2>${esc(poReference(o.id))}</h2><span class="badge">${esc(poStatusName(o.status))}</span></div><p class="po-supplier">${esc(o.supplierName||'Supplier reference '+(o.supplierId||'unavailable'))}<br>${esc(o.storeName)}</p><p class="po-dates">Created ${esc(poDate(o.createdTime))}${o.expectedArrivalDate?' · Expected '+esc(new Date(o.expectedArrivalDate).toLocaleDateString()):''}</p><div class="sh-values"><span>Product lines<b>${o.orderedItems.length}</b></span><span>Outstanding units<b>${poNumber(o.outstanding)}</b></span><span>Total (PHP)<b>${formatCost(o.total)}</b></span></div><div class="po-order-actions"><button data-po-detail="${esc(o.id)}">View order</button><a class="po-download" href="/api/storehub/purchase-orders/${encodeURIComponent(o.id)}.csv" aria-label="Download ${esc(poReference(o.id))} CSV" title="Download purchase order CSV"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 15v6h16v-6M12 3v12m-5-5 5 5 5-5"/></svg><span>CSV</span></a></div></article>`).join('')||'<div class="card"><h2>No matching purchase orders</h2><p>Choose other filters or refresh the selected dates from StoreHub.</p></div>';
 $('po-page').textContent=`Page ${result.page} of ${result.pages}`;
 $('po-prev').disabled=poBusy||result.page===1;$('po-next').disabled=poBusy||result.page>=result.pages;
 document.querySelectorAll('[data-po-detail]').forEach(b=>b.onclick=()=>showPurchaseOrder(b.dataset.poDetail));
 if(result.error)poFeedback(result.error,true);
 }finally{finish();}
}
function poOpen(){view('purchase-orders');window.scrollTo({top:0,behavior:'instant'});}
document.querySelectorAll('[data-po-open]').forEach(b=>b.onclick=poOpen);
document.querySelectorAll('[data-po-local]').forEach(b=>b.onclick=()=>{view('documents');window.scrollTo({top:0,behavior:'instant'});});
$('po-new').onclick=()=>{if(!data)return message('Wait for the product catalog to load.',true);if(lines.length&&$('kind').value!=='purchase')return poFeedback('Save or clear the current transfer draft first.',true);$('kind').value='purchase';$('kind').onchange();view('requests');window.scrollTo({top:0,behavior:'instant'});$('request-form').elements.supplier.focus();message('Create a supplier purchase for the warehouse. Save the request to download its purchase-order CSV.');};
async function refreshPurchaseOrders(){
 if(poBusy)return;poBusy=true;$('po-refresh').disabled=true;
 try{poFeedback('Reading StoreHub purchase orders for the selected dates...');const result=await api('/api/storehub/purchase-orders/refresh',poRange());poPage=1;await loadPurchaseOrders();poFeedback(`${result.count} orders refreshed. Stock quantities were preserved.`);}
 catch(e){poFeedback(e.message,true);}
 finally{poBusy=false;$('po-refresh').disabled=false;if(poState){$('po-prev').disabled=poState.page===1;$('po-next').disabled=poState.page>=poState.pages;}}
}
$('po-refresh').onclick=refreshPurchaseOrders;
$('po-filters').onsubmit=e=>{e.preventDefault();poPage=1;loadPurchaseOrders().then(()=>{if(poState?.needsRefresh)return refreshPurchaseOrders();}).catch(e=>poFeedback(e.message,true));};
['po-status','po-store','po-size'].forEach(id=>$(id).onchange=()=>{poPage=1;loadPurchaseOrders().catch(e=>poFeedback(e.message,true));});
$('po-search').oninput=()=>{clearTimeout(poSearchTimer);poSearchTimer=setTimeout(()=>{poPage=1;loadPurchaseOrders().catch(e=>poFeedback(e.message,true));},250);};
function poChangePage(step){poPage+=step;loadPurchaseOrders().then(()=>$('po-list').scrollIntoView({block:'start',behavior:'instant'})).catch(e=>poFeedback(e.message,true));}
$('po-prev').onclick=()=>poChangePage(-1);$('po-next').onclick=()=>poChangePage(1);
function renderPurchaseOrderDetails(o){
 $('po-detail-title').textContent=poReference(o.id);
 const person=v=>window.StoreHubPeople?.resolve('employees',v)||v||'—';
 const meta=[['Status',poStatusName(o.status)],['Supplier',o.supplierName||'Supplier name unavailable'],['Target location',o.storeName],['Created',poDate(o.createdTime)],['Last modified',poDate(o.modifiedTime)],['Expected arrival',poDate(o.expectedArrivalDate)],['Requested by',person(o.requestedBy)],['Completed by',o.completedByName||window.StoreHubPeople?.display('completedBy',o.completedBy)||o.completedBy||'—']];
 $('po-detail-body').innerHTML='<div class="po-detail-grid">'+meta.map(([l,v])=>`<div><small>${l}</small><p>${esc(v)}</p></div>`).join('')+'</div>'+`<div class="table-wrap"><table><thead><tr><th>Product / SKU</th><th>Ordered</th><th>Received</th><th>Outstanding</th><th>Supplier price (PHP)</th><th>Subtotal (PHP)</th></tr></thead><tbody>${o.orderedItems.map(l=>`<tr><td>${esc(l.name)}<small>${esc(l.sku)}${l.unit?' · '+esc(l.unit):''}</small>${Number(l.overReceived)>0?`<small class="po-over-received">Over received: ${poNumber(l.overReceived)}</small>`:''}${l.componentsUsages.length?`<details><summary>Components</summary>${l.componentsUsages.map(c=>`<p>${esc(c.name)} · ${poNumber(c.quantity??c.Quantity)}</p>`).join('')}</details>`:''}</td><td>${poNumber(l.orderedQuantity)}</td><td>${poNumber(l.receivedQuantity??0)}</td><td>${poNumber(l.outstanding)}</td><td>${formatCost(l.supplierPrice)}</td><td>${formatCost(l.subTotal)}</td></tr>`).join('')||'<tr><td colspan="6">No product lines.</td></tr>'}</tbody></table></div><div class="po-totals">${[['Subtotal',o.subTotal],['Discount',o.discount],['Tax',o.tax],['Total',o.total]].map(([l,v])=>`<span>${l}<b>${formatCost(v)}</b></span>`).join('')}</div>${o.notes?'<h3>Notes</h3><p class="po-note">'+esc(o.notes)+'</p>':''}<p class="note">Supplier and requested-by names are matched from your supplied purchase-order CSV where available. API amounts are shown as returned by StoreHub.</p>`;
 $('po-detail-export').href='/api/storehub/purchase-orders/'+encodeURIComponent(o.id)+'.csv';
 labelTables();
}
async function showPurchaseOrder(id){
 const sequence=++poDetailSequence;poDetailId=id;
 $('po-detail-title').textContent=poReference(id);$('po-detail-body').textContent='Loading order details...';$('po-detail-feedback').textContent='';$('po-detail-export').removeAttribute('href');$('po-detail-refresh').disabled=true;
 if(!$('po-detail-dialog').open)$('po-detail-dialog').showModal();
 try{await window.StoreHubPeople?.load();const r=await fetch('/api/storehub/purchase-orders/'+encodeURIComponent(id),{cache:'no-store'});const o=await r.json();if(sequence!==poDetailSequence)return;if(!r.ok)throw Error(o.error||'Could not load purchase order.');renderPurchaseOrderDetails(o);$('po-detail-refresh').disabled=false;}
 catch(e){if(sequence===poDetailSequence)$('po-detail-feedback').textContent=e.message;}
}
$('po-close').onclick=()=>$('po-detail-dialog').close();
$('po-detail-dialog').addEventListener('close',()=>{poDetailSequence++;});
$('po-detail-refresh').onclick=async()=>{
 const id=poDetailId;$('po-detail-refresh').disabled=true;$('po-detail-feedback').textContent='Reading the latest order details from StoreHub...';
 try{const o=await api('/api/storehub/purchase-orders/'+encodeURIComponent(id)+'/refresh',{});if(poDetailId===id&&$('po-detail-dialog').open){renderPurchaseOrderDetails(o);$('po-detail-feedback').textContent='Details refreshed. No receipt or stock adjustment was recorded.';}await loadPurchaseOrders();}
 catch(e){if(poDetailId===id)$('po-detail-feedback').textContent=e.message;}
 finally{$('po-detail-refresh').disabled=false;}
};
const poToday=new Date(),poStart=new Date(poToday);poStart.setUTCDate(poStart.getUTCDate()-30);$('po-from').value=poStart.toISOString().slice(0,10);$('po-to').value=poToday.toISOString().slice(0,10);
// Keep this read-only page current after scheduled sync, without changing filters or form drafts.
setInterval(()=>{if(document.body.dataset.section==='purchase-orders'&&!document.hidden&&!poBusy&&!$('po-detail-dialog').open)loadPurchaseOrders().catch(()=>{});},60000);
