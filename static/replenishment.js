let rpData=null,rpPage=0,rpBusy=false,rpSequence=0;
function rpFeedback(text,error=false){$('rp-feedback').textContent=text;$('rp-feedback').className=error?'error':'success';}
function rpProducts(){return new Map((data?.products||[]).map(p=>[p.id,p]));}
function rpFilter(source,branch,q,availability,products){return source.filter(r=>{const p=products.get(r.productId)||{};return (!branch||r.location===branch)&&[r.name,p.SKU,p.Barcode].some(v=>String(v||'').toLowerCase().includes(q))&&(!availability||(availability==='ready'?Number(r.suggested)>0:Number(r.suggested)<=0));}).sort((a,b)=>a.location.localeCompare(b.location)||a.name.localeCompare(b.name));}
async function loadReplenishment(){
 const sequence=++rpSequence,finish=window.WarehouseUI?.loading($('rp-list'))||(()=>{});
 try{if(!data)await load();const response=await fetch('/api/storehub',{cache:'no-store'}),result=await response.json();if(!response.ok)throw Error(result.error||'Could not load replenishment.');if(sequence!==rpSequence)return;rpData=result;const branch=$('rp-branch').value;$('rp-branch').innerHTML='<option value="">All branches</option>'+result.locations.filter(l=>l!==data.warehouse).map(l=>`<option value="${esc(l)}">${esc(l)}</option>`).join('');if([...$('rp-branch').options].some(o=>o.value===branch))$('rp-branch').value=branch;$('rp-interval').value=String(result.interval);renderReplenishment();}
 catch(e){if(sequence===rpSequence)rpFeedback(e.message,true);throw e;}finally{finish();}
}
function renderReplenishment(){
 if(!rpData)return;const d=rpData,products=rpProducts(),source=d.suggestions;
 $('rp-sync-status').textContent=(d.busy?'Sync in progress. ':d.interval?`Automatic sync: every ${d.interval} minutes. `:'Automatic sync is off. ')+(d.lastSync?'Stock synced: '+new Date(d.lastSync).toLocaleString()+(d.salesSync?' | Sales synced: '+new Date(d.salesSync).toLocaleString():' | Sales history has not synced yet.'):d.configured?'Sync to load branch stock.':'StoreHub connection needs setup.');
 $('rp-warnings').innerHTML=[...(d.error?[d.error]:[]),...(d.warnings||[]),...(!d.salesSync?['Sales history is unavailable. Sync StoreHub before relying on this plan.']:[]),...(d.baselinePending?[`${d.baselinePending} products need opening balances. Open StoreHub connection to initialize them.`]:[])].map(w=>`<div class="notice">${esc(w)}</div>`).join('');
 const ready=source.filter(r=>Number(r.suggested)>0).length,blocked=source.length-ready;
 $('rp-stats').innerHTML=[['Branch shortages',source.length],['Can transfer',ready],['Warehouse stock needed',blocked]].map(([label,n])=>`<div class="card"><small>${label}</small><b>${n.toLocaleString()}</b></div>`).join('');
 const branch=$('rp-branch').value,found=rpFilter(source,branch,$('rp-search').value.trim().toLowerCase(),$('rp-availability').value,products),pages=Math.max(1,Math.ceil(found.length/20));rpPage=Math.min(rpPage,pages-1);
 const branchReady=source.filter(r=>r.location===branch&&Number(r.suggested)>0).length;$('rp-draft').disabled=rpBusy||!branchReady;
 $('rp-selection').textContent=branch?`${branchReady} transferable product lines for ${branch}. The draft includes all available suggestions for this branch, across all pages and filters.`:'Choose a branch to draft its replenishment transfer.';
 $('rp-list').innerHTML=found.slice(rpPage*20,rpPage*20+20).map(r=>{const p=products.get(r.productId)||{};return `<article class="rp-row"><h3>${esc(r.name)}</h3><p>${esc(r.location)}${p.SKU?' · '+esc(p.SKU):''}</p><div class="sh-values">${[['Average daily sales',r.averageDailySales??'\u2014'],['On hand',r.onHand],['Target',r.target],['Pending transfer',r.pending],['Shortage',r.needed],['Suggested transfer',r.suggested]].map(([label,value])=>`<span>${label}<b>${esc(value)}</b></span>`).join('')}</div><span class="badge ${Number(r.suggested)>0?'':'rp-blocked'}">${r.baselinePending?'Initialize balances':Number(r.suggested)>0?Number(r.suggested)<Number(r.needed)?'Partial stock available':'Ready to draft':'Warehouse stock needed'}</span><p>${esc(r.basis)} · ${esc(r.sold30Days)} sold in 30 days</p></article>`;}).join('')||'<p class="note">'+(d.lastSync?'No matching replenishment shortages.':'Sync StoreHub and map locations to see replenishment suggestions.')+'</p>';
 $('rp-page').textContent=`${found.length} results · Page ${rpPage+1} of ${pages}`;$('rp-prev').disabled=rpPage===0;$('rp-next').disabled=rpPage+1>=pages;
}
async function rpRun(action){if(rpBusy)return;rpBusy=true;document.querySelectorAll('#replenishment button').forEach(b=>b.disabled=true);try{await action();}catch(e){rpFeedback(e.message,true);}finally{rpBusy=false;document.querySelectorAll('#replenishment button').forEach(b=>b.disabled=false);renderReplenishment();}}
async function draftReplenishment(){
 const branch=$('rp-branch').value;if(!branch)throw Error('Choose a branch first.');
 if(lines.length)throw Error('Save or clear your current product request before drafting a replenishment transfer.');
 await load();await loadReplenishment();if(lines.length)throw Error('A product request is already in progress.');
 const items=rpData.suggestions.filter(r=>r.location===branch&&Number(r.suggested)>0),products=rpProducts();
 if(!items.length)throw Error('There are no available transfer quantities for this branch after refreshing.');
 if(items.some(r=>!products.has(r.productId)))throw Error('The catalog changed. Refresh before drafting this transfer.');
 $('kind').value='transfer';$('kind').onchange();$('target').value=branch;
 lines=items.map(r=>({product_id:r.productId,quantity:r.suggested,cost:products.get(r.productId).Cost||'0'}));renderLines();view('requests');window.scrollTo({top:0,behavior:'instant'});message('Replenishment transfer drafted. Review quantities and requested-by name, then create the request and export its CSV.');
}
document.querySelectorAll('[data-rp-open]').forEach(b=>b.onclick=()=>{view('replenishment');window.scrollTo({top:0,behavior:'instant'});});
$('rp-refresh').onclick=()=>rpRun(loadReplenishment);
$('rp-sync').onclick=()=>rpRun(async()=>{rpFeedback('Syncing StoreHub stock…');await api('/api/storehub/sync',{});await load();await loadReplenishment();rpFeedback('Stock sync completed. Recommendations refreshed.');});
$('rp-schedule-form').onsubmit=e=>{e.preventDefault();rpRun(async()=>{await api('/api/storehub/schedule',{interval:Number($('rp-interval').value)});await loadReplenishment();rpFeedback('Sync schedule saved.');});};
$('rp-draft').onclick=()=>rpRun(draftReplenishment);
['rp-branch','rp-availability'].forEach(id=>$(id).onchange=()=>{rpPage=0;renderReplenishment();});$('rp-search').oninput=()=>{rpPage=0;renderReplenishment();};
$('rp-prev').onclick=()=>{rpPage--;renderReplenishment();};$('rp-next').onclick=()=>{rpPage++;renderReplenishment();};
setInterval(()=>{if(document.body.dataset.section==='replenishment'&&!rpBusy)loadReplenishment().catch(()=>{});},60000);
