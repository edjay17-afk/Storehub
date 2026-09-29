const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(){
 const els=new Map(),storage=new Map();let tones=0,alerts=[];
 const el=id=>{if(!els.has(id))els.set(id,{textContent:'',innerHTML:'',setAttribute(){},classList:{remove(){},add(){}},close(){},showModal(){}});return els.get(id);};
 class Audio{constructor(){this.state='running';this.currentTime=0;}createOscillator(){return {frequency:{},connect(){},start(){tones++;},stop(){}};}createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}};}}
 const context={window:{AudioContext:Audio},document:{querySelectorAll:()=>[],addEventListener(){}},$:el,esc:s=>String(s).replaceAll('<','&lt;'),localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},fetch:async()=>({ok:true,json:async()=>({alerts})}),Date,setInterval(){},setTimeout(){},clearTimeout(){}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('static/notifications.js','utf8'),context);
 return {api:context.window.WarehouseNotifications,el,storage,setAlerts:a=>alerts=a,tones:()=>tones,settle:()=>new Promise(r=>setImmediate(r))};
}
const alert=(fingerprint='one')=>({id:'stock',fingerprint,title:'Low stock',detail:'Rice',target:'catalog',level:'warning'});
test('initial alerts are silent; unchanged polls stay silent; new alerts sound once',async()=>{
 const h=harness();h.setAlerts([alert()]);await h.settle();await h.el('notification-sound').onclick();assert.equal(h.tones(),2);
 await h.api.refresh();assert.equal(h.tones(),2);
 h.setAlerts([alert('two')]);await h.api.refresh();assert.equal(h.tones(),4);
 await h.api.refresh();assert.equal(h.tones(),4);
});
test('read history persists and resolved alerts become unread if they recur',async()=>{
 const h=harness();await h.settle();assert.equal(h.api.ingest([alert()]).length,1);
 h.el('notifications-read').onclick();assert.equal(JSON.parse(h.storage.get('centro-notifications-v1'))[0].read,true);
 assert.equal(h.api.ingest([alert()]).length,0);h.api.ingest([]);
 assert.equal(h.api.ingest([alert()]).length,1);assert.equal(JSON.parse(h.storage.get('centro-notifications-v1'))[0].read,false);
});
test('successful writes notify; replayed writes and read operations do not',async()=>{
 const h=harness();await h.settle();h.api.success('/api/wastage/x/edit');
 assert.equal(JSON.parse(h.storage.get('centro-notifications-v1')).length,1);
 h.api.success('/api/storehub/partner/commit',{replayed:true});h.api.success('/api/storehub/partner/read');
 assert.equal(JSON.parse(h.storage.get('centro-notifications-v1')).length,1);
});
test('notification text is escaped before inserting into the panel',async()=>{
 const h=harness();await h.settle();h.api.ingest([{...alert(),title:'<script>bad</script>'}]);
 assert.ok(!h.el('notification-list').innerHTML.includes('<script>'));
});
