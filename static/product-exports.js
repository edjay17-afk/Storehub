let productExportBusy=false;
document.querySelectorAll('a[href^="/exports/products.csv"]').forEach(link=>link.addEventListener('click',async event=>{
 event.preventDefault();if(productExportBusy)return;productExportBusy=true;
 const changed=!new URL(link.href).searchParams.has('scope');
 try{
  const response=await fetch('/api/product-export-status?scope='+(changed?'changed':'all'));
  const result=await response.json();if(!response.ok)throw Error(result.error||'Could not check the export.');
  if(!result.missing.length){window.location.assign(link.href);return;}
  $('product-export-title').textContent=changed?'Export changed products':'Export full catalog';
  $('product-export-summary').textContent=`${result.total.toLocaleString()} products selected. ${result.missing.length.toLocaleString()} synced products are missing fields from the StoreHub Products CSV.`;
  $('product-export-missing').innerHTML=result.missing.map(p=>`<li>${esc(p.name)}${p.sku?` <small>SKU ${esc(p.sku)}</small>`:''}${p.baseline_pending?' <small>Local balances not initialized</small>':''}</li>`).join('');
  $('product-export-review').href='/exports/products.csv?scope='+(changed?'changed':'all');
  $('product-export-review').textContent=`Download all ${result.total.toLocaleString()} for review`;
  $('product-export-ready').href='/exports/products.csv?scope=ready'+(changed?'&changed=1':'');
  $('product-export-ready').textContent=`Download StoreHub template (${result.ready.toLocaleString()} products)`;
  $('product-export-ready').hidden=result.ready===0;
  $('product-export-dialog').showModal();
 }catch(error){message(error.message,true);}
 finally{productExportBusy=false;}
}));
$('product-export-close').onclick=()=>$('product-export-dialog').close();
$('product-export-review').onclick=()=>message('Downloading the full catalog for review. This file includes all selected products and is not a StoreHub import template.');
$('product-export-ready').onclick=()=>message('Downloading the StoreHub template. Products missing CSV fields are excluded; see the list in the export dialog.');
