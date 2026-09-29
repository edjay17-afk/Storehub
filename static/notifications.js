(() => {
 const storageKey='centro-notifications-v1',soundKey='centro-notification-sound';
 let entries=[],soundOn=false,audio=null,polling=false,firstPoll=true,toastTimer,eventSequence=0;
 try{const saved=JSON.parse(localStorage.getItem(storageKey)||'[]');if(Array.isArray(saved))entries=saved.filter(e=>typeof e.id==='string'&&typeof e.title==='string'&&typeof e.detail==='string').slice(0,100);soundOn=localStorage.getItem(soundKey)==='on';}catch{}
 const save=()=>{try{localStorage.setItem(storageKey,JSON.stringify(entries.slice(0,100)));localStorage.setItem(soundKey,soundOn?'on':'off');}catch{}};
 const targets=new Set(['manual-inventory','catalog','documents','purchase-orders','storehub','partner-tools','wastage','imported-transfers','receiving','activity']);
 function ingest(alerts){
  const active=new Set(alerts.map(a=>a.id+'/'+a.fingerprint)),fresh=[];
  for(const e of entries)if(e.source==='alert'&&!active.has(e.id)){e.active=false;e.read=true;}
  for(const a of alerts){
   const id=a.id+'/'+a.fingerprint,existing=entries.find(e=>e.id===id);
   if(existing){if(!existing.active){existing.read=false;existing.created=Date.now();fresh.push(existing);}Object.assign(existing,a,{id,active:true});}
   else{const entry={...a,id,source:'alert',active:true,read:false,created:Date.now()};entries.unshift(entry);fresh.push(entry);}
  }
  entries.sort((a,b)=>b.created-a.created||(a.level==='error'?-1:0)-(b.level==='error'?-1:0)||a.title.localeCompare(b.title));entries=entries.slice(0,100);save();render();return fresh;
 }
 function render(){
  const unread=entries.filter(e=>!e.read&&(e.source!=='alert'||e.active)).length;
  document.querySelectorAll('[data-notification-count]').forEach(e=>{e.hidden=!unread;e.textContent=unread>99?'99+':String(unread);});
  document.querySelectorAll('[data-notifications-open]').forEach(e=>e.setAttribute('aria-label',unread?`Notifications, ${unread} unread`:'Notifications'));
  $('notification-sound').textContent=soundOn?'Sound on':'Enable sound';$('notification-sound').setAttribute('aria-pressed',String(soundOn));
  $('notification-sound-note').textContent=soundOn?'Sound is on. After reopening this app, tap anywhere to activate audio.':'Sound plays while this app is open. Enable sound to hear new alerts.';
  $('notification-list').innerHTML=entries.map(e=>`<article class="notification-entry ${e.read?'read':''} ${esc(e.level)}"><span class="notification-dot" aria-hidden="true"></span><div><h3>${esc(e.title)}</h3><p>${esc(e.detail)}</p><small>${new Date(e.created).toLocaleString()}${e.source==='alert'&&!e.active?' · Resolved':''}${e.read?' · Read':''}</small><br><button type="button" data-notification-open="${esc(e.id)}">${e.target?'View':'Mark as read'}</button></div></article>`).join('')||'<p class="note">You’re all caught up. Stock warnings, pending orders and sync problems will appear here.</p>';
  document.querySelectorAll('[data-notification-open]').forEach(b=>b.onclick=()=>{const e=entries.find(e=>e.id===b.dataset.notificationOpen);if(!e)return;e.read=true;save();render();if(targets.has(e.target)){$('notifications-dialog').close();openTarget(e);}});
 }
 async function openTarget(e){
  if(e.target==='partner-tools'&&typeof partnerOpen==='function')partnerOpen();
  else if(e.target==='storehub'){view('storehub');if(typeof shRun==='function')shRun(getStoreHub);}
  else view(e.target);
  if(e.target==='manual-inventory'&&e.location){await loadManualInventory();$('inventory-filter-branch').value=e.location;$('inventory-filter-deleted').value='0';$('inventory-filter-search').value='';$('inventory-filter-status').value='';inventoryPage=1;await loadManualInventory();}
  if(e.target==='catalog'&&e.location&&[...$('location').options].some(o=>o.value===e.location)){$('location').value=e.location;renderProducts();}
  window.scrollTo({top:0,behavior:'instant'});
 }
 async function unlock(){
  if(!soundOn)return;
  try{const Constructor=window.AudioContext||window.webkitAudioContext;if(!Constructor)return;audio ||= new Constructor();if(audio.state==='suspended')await audio.resume();}catch{}
 }
 function chime(){
  if(!soundOn||!audio||audio.state!=='running')return;
  try{const start=audio.currentTime;for(const [offset,freq] of [[0,660],[.14,880]]){const tone=audio.createOscillator(),gain=audio.createGain();tone.type='sine';tone.frequency.value=freq;gain.gain.setValueAtTime(0,start+offset);gain.gain.linearRampToValueAtTime(.09,start+offset+.02);gain.gain.exponentialRampToValueAtTime(.001,start+offset+.22);tone.connect(gain);gain.connect(audio.destination);tone.start(start+offset);tone.stop(start+offset+.24);}}catch{}
 }
 function toast(text){$('notification-toast').textContent=text;$('notification-toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('notification-toast').classList.add('hidden'),4500);}
 async function refresh(){
  if(polling)return;polling=true;
  try{const r=await fetch('/api/notifications',{cache:'no-store'});if(!r.ok)throw Error();const d=await r.json();const fresh=ingest(d.alerts||[]);if(!firstPoll&&fresh.length){chime();toast(fresh.length===1?fresh[0].title:`${fresh.length} new notifications`);}firstPoll=false;$('notifications-status').textContent='Checked '+new Date().toLocaleTimeString()+(d.lastSync?' · StoreHub synced '+new Date(d.lastSync).toLocaleString():'');}
  catch{$('notifications-status').textContent='Could not refresh alerts. Previous notifications are still available.';}
  finally{polling=false;}
 }
 function success(url,result={}){
  if(result.repeated||result.replayed)return;
  let title='',detail='',target='';
  if(url==='/api/receive'){title='Warehouse stock recorded';detail='The warehouse stock record was saved.';target='activity';}
  else if(url==='/api/products'){title='New warehouse product saved';detail='The product is now in the warehouse catalog.';target='catalog';}
  else if(url==='/api/documents'){title='Product request created';detail='Your purchase order or stock transfer is ready for review.';target='documents';}
  else if(/^\/api\/documents\/[^/]+\/action$/.test(url)){title='Order updated';detail='The order status and any required stock movement were recorded.';target='documents';}
  else if(url==='/api/imported-transfers/import'){title='Stock-transfer report imported';detail='The imported StoreHub report is ready to view.';target='imported-transfers';}
  else if(url==='/api/storehub/partner/commit'){title='StoreHub change confirmed';detail='StoreHub confirmed the reviewed change.';target='partner-tools';}
  else if(url==='/api/storehub/publish'){title='Product published to StoreHub';detail='StoreHub confirmed the product publication.';target='storehub';}
  else if(url==='/api/storehub/baseline'){title='Opening balances initialized';detail='Imported products now have local opening balances.';target='storehub';}
  else if(url==='/api/wastage/import'){title='StoreHub wastage imported';detail=`${result.imported_lines} product lines imported. Stock balances were preserved.`;target='wastage';}
  else if(/^\/api\/wastage(?:\/[^/]+\/(?:edit|delete|restore))?$/.test(url)){title=url.endsWith('/delete')?'Wastage record moved to Deleted records':url.endsWith('/restore')?'Wastage record restored':'Wastage record saved';detail='The wastage record was updated. Stock balances were preserved.';target='wastage';}
  else if(/^\/api\/manual-inventory(?:\/[^/]+\/(?:edit|delete|restore))?$/.test(url)){title=url.endsWith('/delete')?'Inventory record deleted':url.endsWith('/restore')?'Inventory record restored':'Inventory count saved';detail='The batch count and expiration date were recorded. Stock balances were preserved.';target='manual-inventory';}
  if(!title){refresh();return;}
  entries.unshift({id:'event:'+Date.now()+':'+(++eventSequence),source:'event',active:true,read:false,created:Date.now(),title,detail,target,level:'success'});entries=entries.slice(0,100);save();render();chime();toast(title);refresh();
 }
 window.WarehouseNotifications={refresh,success,ingest};
 document.querySelectorAll('[data-notifications-open]').forEach(b=>b.onclick=()=>{$('notifications-dialog').showModal();refresh();});
 $('notifications-close').onclick=()=>$('notifications-dialog').close();
 $('notifications-read').onclick=()=>{entries.forEach(e=>e.read=true);save();render();};
 $('notification-sound').onclick=async()=>{soundOn=!soundOn;save();render();if(soundOn){await unlock();chime();if(!audio||audio.state!=='running')$('notification-sound-note').textContent='This browser could not enable audio. Notifications will still appear in the bell.';}};
 document.addEventListener('pointerdown',unlock);document.addEventListener('keydown',unlock);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
 render();refresh();setInterval(refresh,60000);
})();
