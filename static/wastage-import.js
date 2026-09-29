let wiPreview=null,wiBusy=false;
function wiFeedback(text,error=false){$('wi-feedback').textContent=text;$('wi-feedback').className=error?'error':'success';}
function wiControls(busy){wiBusy=busy;['wi-file','wi-preview-button','wi-confirm','wi-close'].forEach(id=>$(id).disabled=busy);if(!busy)$('wi-confirm').disabled=!wiPreview?.records.some(r=>r.change==='new');}
$('wi-file').onchange=()=>{wiPreview=null;wiFeedback('');};
$('wi-close').onclick=()=>{if(!wiBusy){wiPreview=null;$('wi-dialog').close();}};
$('wi-dialog').addEventListener('cancel',e=>{if(wiBusy)e.preventDefault();else wiPreview=null;});
$('wi-form').onsubmit=async e=>{
 e.preventDefault();if(wiBusy)return;wiPreview=null;const file=$('wi-file').files[0];if(!file)return;
 if(file.size>5*1024*1024)return wiFeedback('Choose a CSV file no larger than 5 MB.',true);
 wiControls(true);wiFeedback('Checking StoreHub wastage records…');
 try{const body=new FormData();body.append('file',file);const response=await fetch('/api/wastage/import-preview',{method:'POST',body}),result=await response.json();if(!response.ok)throw Error(result.error||'Could not preview wastage CSV.');wiPreview=result;
 $('wi-summary').textContent=`${result.filename}: ${result.records.filter(r=>r.change==='new').length} new StoreHub records, ${result.records.filter(r=>r.change==='unchanged').length} already imported, ${result.skipped.length} records outside available branches excluded.`;
 $('wi-records').innerHTML=result.records.map(r=>`<article class="rp-row"><h3>${esc(r.id)} · ${esc(r.branch)}</h3><p>${esc(r.created.replace('T',' '))} · ${esc(r.change)} · Original total ${formatCost(r.total)}</p><div class="table-wrap"><table><thead><tr><th>Product</th><th>Quantity</th><th>Reason</th><th>Unit cost (PHP)</th><th>Subtotal (PHP)</th></tr></thead><tbody>${r.items.map(i=>`<tr><td>${esc(i.product_name)}</td><td>${esc(i.quantity)}</td><td>${esc(i.reason)}</td><td>${formatCost(i.cost)}</td><td>${formatCost(i.subtotal)}</td></tr>`).join('')}</tbody></table></div></article>`).join('')||'<p>No records for available branches.</p>';
 $('wi-review-feedback').textContent='Review the exported date, branch and product quantities. No photos are included in this CSV.';labelTables();$('wi-dialog').showModal();wiFeedback('CSV checked. Review the import before saving.');
 }catch(error){wiFeedback(error.message,true);}finally{wiControls(false);}
};
$('wi-confirm').onclick=async()=>{
 if(wiBusy||!wiPreview)return;const token=wiPreview.token;wiControls(true);$('wi-review-feedback').textContent='Importing…';
 try{const result=await api('/api/wastage/import',{token});wiPreview=null;$('wi-dialog').close();$('wi-file').value='';wasteDeleted=false;wastePage=1;$('waste-filter-form').reset();$('waste-trash-toggle').textContent='Deleted records';await loadWastage();wiFeedback(`${result.imported_lines} product lines imported; ${result.unchanged_records} existing StoreHub records kept. Stock balances unchanged.`);}
 catch(error){$('wi-review-feedback').textContent=error.message;}finally{wiControls(false);}
};
