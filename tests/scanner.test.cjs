const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup() {
  const elements = new Map();
  function el(id) {
    if (!elements.has(id)) elements.set(id, {value:'',textContent:'',hidden:false,disabled:false,open:false,srcObject:null,
      classList:{toggle(){}},listeners:{},files:[],click(){this.clicks=(this.clicks||0)+1;},focus(){},showModal(){this.open=true;},close(){this.open=false;},
      addEventListener(name,handler){this.listeners[name]=handler;}});
    return elements.get(id);
  }
  el('product-form').elements = {sku:{value:''},name:{focus(){}}};
  el('receive-form').elements = {quantity:{focus(){}}};
  const calls = [], camera = {stops:0}, buttons = [{dataset:{scan:'new'}},{dataset:{scan:'catalog'}}], listeners = {};
  const ctx = {
    $:el, data:{products:[{id:'known',Barcode:'123, 00123',SKU:'known','Product Name':'Known product','Track Stock Levels':'1',Cost:'3'}]},
    lines:[], page:0, message:(...a)=>calls.push(a), view:id=>calls.push(['view',id]),
    renderProducts:()=>calls.push(['render']),renderLines:()=>calls.push(['lines']),selectReceive:p=>calls.push(['receive',p.id]),
    document:{hidden:false,querySelectorAll(){return buttons;},addEventListener(name,handler){listeners[name]=handler;}},
    navigator:{mediaDevices:{async getUserMedia(constraints){camera.constraints=constraints;return {getTracks:()=>[{stop(){camera.stops++;}}]};}}},
    window:{isSecureContext:true,ZXingBrowser:true,addEventListener(){}},
    URL, URLSearchParams, location:{hash:'',pathname:'/',search:'',origin:'http://192.168.110.89:5077'},
    ZXingBrowser:{BrowserMultiFormatOneDReader:class {
      async decodeFromImageUrl(){return {getText:()=>"00000999"};}
      async decodeFromStream(stream,video,callback) {
        camera.callback=callback;
        video.srcObject=stream;
        const controls={stop(){camera.stops++;}};
        camera.controls=controls;
        return controls;
      }
    }}
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/scanner.js'),'utf8'),ctx);
  return {ctx,el,calls,camera,buttons,listeners,run:code=>vm.runInContext(code,ctx)};
}

test('new scans preserve leading zeros and fill only an empty SKU',()=>{
  const s=setup();
  s.run("applyNewBarcode('00000999')");
  assert.equal(s.el('product-barcode').value,'00000999');
  assert.equal(s.el('product-form').elements.sku.value,'00000999');
  s.run("applyNewBarcode('00000888')");
  assert.equal(s.el('product-form').elements.sku.value,'00000999');
});

test('existing comma-separated barcode offers receiving without creating a product',()=>{
  const s=setup();s.run("applyNewBarcode('00123')");
  assert.equal(s.el('existing-receive').hidden,false);
  assert.match(s.el('barcode-feedback').textContent,/Known product/);
  assert.equal(s.el('product-form').elements.sku.value,'');
  s.el('existing-receive').onclick();
  assert.deepEqual(s.calls,[['receive','known'],['view','receiving']]);
});

test('USB Enter checks barcode without submitting the product form',()=>{
  const s=setup();let prevented=false;
  s.el('product-barcode').listeners.keydown({key:'Enter',target:{value:'00000999'},preventDefault(){prevented=true;}});
  assert.ok(prevented);assert.equal(s.el('product-barcode').value,'00000999');
});

test('editing a checked barcode clears stale existing-product feedback',()=>{
  const s=setup();s.run("applyNewBarcode('00123')");
  s.el('product-barcode').listeners.input();
  assert.equal(s.el('existing-receive').hidden,true);
  assert.equal(s.el('barcode-feedback').textContent,'');
});

test('camera result fills the product, closes scanner, and stops stream',async()=>{
  const s=setup();s.el('scanner-dialog').open=true;
  await s.run('startCamera()');
  assert.equal(s.camera.constraints.video.facingMode.ideal,'environment');
  s.camera.callback({getText:()=> '00000999'},null,s.camera.controls);
  assert.equal(s.el('product-barcode').value,'00000999');
  assert.equal(s.el('scanner-dialog').open,false);
  assert.equal(s.el('scanner-video').srcObject,null);
  assert.ok(s.camera.stops>0);
});

test('permission failure leaves manual and USB input available',async()=>{
  const s=setup();s.ctx.navigator.mediaDevices.getUserMedia=async()=>{throw Object.assign(new Error('denied'),{name:'NotAllowedError'});};
  s.el('scanner-dialog').open=true;await s.run('startCamera()');
  assert.match(s.el('scanner-status').textContent,/denied/);
  assert.equal(s.el('scan-code').disabled,false);
  assert.equal(s.el('scanner-dialog').open,true);
});

test('cancelled startup stops a late stream without processing a barcode',async()=>{
  const s=setup();let release;
  s.ctx.ZXingBrowser.BrowserMultiFormatOneDReader=class {decodeFromStream(){return new Promise(resolve=>{release=resolve;});}};
  s.el('scanner-dialog').open=true;const pending=s.run('startCamera()');
  await new Promise(resolve=>setImmediate(resolve));
  s.run('closeScanner()');let stopped=false;release({stop(){stopped=true;}});await pending;
  assert.ok(stopped);assert.equal(s.el('product-barcode').value,'');
});

test('request scans add known products and reject duplicate lines',()=>{
  const s=setup();s.run("scanContext='request'; useBarcode('00123')");
  assert.equal(s.ctx.lines.length,1);
  assert.throws(()=>s.run("useBarcode('00123')"),/already in the request/);
});

test('opening scanner starts the camera automatically without extra buttons',async()=>{
  const s=setup();await s.buttons[0].onclick();
  assert.equal(s.el('scanner-dialog').open,true);
  assert.ok(s.camera.callback);
  assert.match(s.el('scanner-status').textContent,/Scanning/);
  const template=fs.readFileSync(path.join(__dirname,'../templates/index.html'),'utf8');
  assert.doesNotMatch(template,/id="(?:start|stop)-camera"/);
});

test('camera stops in background and resumes automatically on return',async()=>{
  const s=setup();await s.buttons[0].onclick();
  s.ctx.document.hidden=true;s.listeners.visibilitychange();
  assert.equal(s.el('scanner-video').srcObject,null);
  s.ctx.document.hidden=false;s.listeners.visibilitychange();
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(s.el('scanner-video').srcObject);
  s.run('closeScanner()');
  s.listeners.visibilitychange();
  assert.equal(s.el('scanner-video').srcObject,null);
});

test('mobile camera preferences fall back to basic camera settings',async()=>{
  const s=setup();const requests=[];
  s.ctx.navigator.mediaDevices.getUserMedia=async constraints=>{
    requests.push(constraints);
    if(requests.length===1)throw Object.assign(new Error('resolution'),{name:'OverconstrainedError'});
    return {getTracks:()=>[{stop(){}}]};
  };
  await s.buttons[0].onclick();
  assert.equal(requests.length,2);
  assert.equal(requests[1].video,true);
  assert.equal(requests[1].audio,false);
  assert.equal(s.el('scanner-video').playsInline,true);
  assert.equal(s.el('scanner-video').muted,true);
  assert.match(s.el('scanner-status').textContent,/Scanning/);
});

test('insecure phone URL explains HTTPS instead of requesting a blocked camera',async()=>{
  const s=setup();s.ctx.window.isSecureContext=false;
  await s.buttons[0].onclick();
  assert.equal(s.camera.constraints,undefined);
  assert.match(s.el('scanner-status').textContent,/HTTPS/);
  assert.equal(s.el('scanner-dialog').open,true);
});

test('unrecognized camera errors show recovery guidance without raw errors',async()=>{
  const s=setup();s.ctx.navigator.mediaDevices.getUserMedia=async()=>{throw new Error('INTERNAL_DEVICE_FAILURE');};
  await s.buttons[0].onclick();
  assert.doesNotMatch(s.el('scanner-status').textContent,/INTERNAL_DEVICE_FAILURE/);
  assert.match(s.el('scanner-status').textContent,/permission/);
});

test('closing while permission is pending stops the late camera immediately',async()=>{
  const s=setup();let permit;
  s.ctx.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>{permit=resolve;});
  const pending=s.buttons[0].onclick();s.run('closeScanner()');
  let stopped=false;permit({getTracks:()=>[{stop(){stopped=true;}}]});await pending;
  assert.ok(stopped);assert.equal(s.camera.callback,undefined);
});

test('mobile scanner does not open a keyboard over the camera',async()=>{
  const s=setup();s.ctx.window.matchMedia=()=>({matches:true});
  let focused=false;s.el('scan-code').focus=()=>{focused=true;};
  await s.buttons[0].onclick();assert.equal(focused,false);
});

test('bundled decoder reads a real EAN-13 barcode raster',()=>{
  const originalWindow=global.window;
  try {
    global.window={};
    const zxing=require('../static/vendor/zxing-browser.min.js');
    const fixture=require('./fixtures/retail-barcode.json');
    const pixels=new Uint8ClampedArray(fs.readFileSync(path.join(__dirname,'fixtures/retail-barcode.rgba')));
    const canvas={width:fixture.width,height:fixture.height,getContext:()=>({getImageData:()=>({data:pixels})})};
    assert.equal(new zxing.BrowserMultiFormatOneDReader().decodeFromCanvas(canvas).getText(),fixture.barcode);
  } finally {global.window=originalWindow;}
});

test('inventory scanner routes decoded barcode to the inventory form without receiving stock',async()=>{
 const s=setup();let scanned;s.ctx.inventoryApplyBarcode=code=>{scanned=code;};await s.run("openScanner('inventory')");s.camera.callback({getText:()=> '00123'},null,s.camera.controls);
 assert.equal(scanned,'00123');assert.equal(s.el('scanner-dialog').open,false);assert.ok(s.camera.stops>0);assert.deepEqual(s.calls,[]);
});

test('secure inventory return restores draft before applying barcode; cancellation only restores',async()=>{
 for(const cancelled of [false,true]){
  const s=setup(),token='b'.repeat(32),events=[];s.ctx.data.locations=['Warehouse'];
  const inventory={values:{branch:'Branch A'},token:'draft-token'};
  const pending={token,context:'inventory',created:Date.now(),draft:{inventory,product:{},request:{},receive:{},receiveId:'',receiveSearch:'',search:'',location:'Warehouse',lines:[]}};
  let stored=JSON.stringify(pending);s.ctx.sessionStorage={getItem:()=>stored,removeItem(){stored=null;}};s.ctx.history={replaceState(){s.ctx.location.hash='';}};
  for(const id of ['product-form','request-form','receive-form']){const elements=s.el(id).elements||{};elements.namedItem=name=>elements[name]||(elements[name]={value:''});s.el(id).elements=elements;}
  s.el('kind').onchange=()=>{};s.ctx.inventoryRestoreScan=async draft=>{events.push(['restore',draft.token]);};s.ctx.inventoryApplyBarcode=code=>events.push(['barcode',code]);
  s.ctx.location.hash='#'+new URLSearchParams({scanReturn:token,barcode:'00123',...(cancelled?{cancelled:'1'}:{})});await s.run('restoreSecureScanReturn()');
  assert.equal(events[0][0],'restore');assert.equal(events[0][1],'draft-token');assert.equal(events.length,cancelled?1:2);if(!cancelled)assert.deepEqual(events[1],['barcode','00123']);assert.equal(stored,null);
 }
});

test('secure scan restores unsaved form fields and applies barcode only once',()=>{
  const s=setup(), token='a'.repeat(32);
  const pending={token,context:'new',created:Date.now(),draft:{product:{sku:'DRAFT-SKU',name:'Unsaved product'},request:{},receive:{},receiveId:'',receiveSearch:'',search:'',location:'Warehouse',lines:[],view:'catalog'}};
  let stored=JSON.stringify(pending);
  s.ctx.data.locations=['Warehouse'];
  s.ctx.sessionStorage={getItem:()=>stored,removeItem(){stored=null;}};
  s.ctx.history={replaceState(){s.ctx.location.hash='';}};
  for(const id of ['product-form','request-form','receive-form']) {
    const elements=s.el(id).elements||{};
    elements.namedItem=name=>elements[name]||(elements[name]={value:''});
    s.el(id).elements=elements;
  }
  s.el('kind').onchange=()=>{};
  s.ctx.location.hash='#'+new URLSearchParams({scanReturn:token,barcode:'00000999'});
  s.run('restoreSecureScanReturn()');
  assert.equal(s.el('product-form').elements.sku.value,'DRAFT-SKU');
  assert.equal(s.el('product-form').elements.name.value,'Unsaved product');
  assert.equal(s.el('product-barcode').value,'00000999');
  assert.equal(s.el('product-dialog').open,true);assert.equal(stored,null);
  const count=s.calls.length;s.run('restoreSecureScanReturn()');assert.equal(s.calls.length,count);
});

test('forged scan return cannot overwrite forms',()=>{
  const s=setup();s.ctx.location.hash='#scanReturn=forged&barcode=999';
  s.ctx.history={replaceState(){}};
  s.ctx.sessionStorage={getItem:()=>JSON.stringify({token:'valid',context:'new',created:Date.now()})};
  s.run('restoreSecureScanReturn()');
  assert.equal(s.el('product-barcode').value,'');assert.match(s.calls[0][0],/expired/);
});

test('return opens the saved page immediately before catalog loading, for every system page',()=>{
 for(const previous of ['manual-inventory','wastage','replenishment','storehub','partner-tools','requests','receiving','documents','activity','purchase-orders','imported-transfers']){
  const s=setup(),token='c'.repeat(32);s.ctx.location.hash='#scanReturn='+token;
  s.ctx.sessionStorage={getItem:()=>JSON.stringify({token,context:'request',created:Date.now(),draft:{view:previous}})};
  s.run('prepareSecureScanReturn()');assert.deepEqual(s.calls,[['view',previous]]);
 }
 const expired=setup();expired.ctx.location.hash='#scanReturn=old';expired.ctx.sessionStorage={getItem:()=>JSON.stringify({token:'old',created:Date.now()-31*60000,draft:{view:'requests'}})};expired.run('prepareSecureScanReturn()');assert.deepEqual(expired.calls,[]);
});

test('new-product scan cancellation restores the original page without forcing Products',async()=>{
 const s=setup(),token='d'.repeat(32);s.ctx.data.locations=['Warehouse'];
 s.ctx.sessionStorage={getItem:()=>JSON.stringify({token,context:'new',created:Date.now(),draft:{product:{},request:{},receive:{},location:'Warehouse',lines:[],view:'replenishment'}}),removeItem(){}};
 s.ctx.history={replaceState(){s.ctx.location.hash='';}};
 for(const id of ['product-form','request-form','receive-form']){const elements=s.el(id).elements||{};elements.namedItem=name=>elements[name]||(elements[name]={value:''});s.el(id).elements=elements;}
 s.el('kind').onchange=()=>{};s.ctx.location.hash='#'+new URLSearchParams({scanReturn:token,cancelled:'1'});
 await s.run('restoreSecureScanReturn()');assert.deepEqual(s.calls.filter(c=>c[0]==='view'),[['view','replenishment']]);assert.equal(s.el('product-dialog').open,true);
});

test('insecure scanner click refreshes an expired secure address before navigation',async()=>{
  const s=setup();s.ctx.window.isSecureContext=false;
  s.ctx.data.scannerUrl='https://expired.trycloudflare.com';
  let requested,navigated;
  s.ctx.fetch=async(url,options)=>{requested={url,options};return {ok:true,json:async()=>({ready:true,url:'https://fresh.trycloudflare.com'})};};
  s.ctx.openSecureScanner=context=>{navigated={url:s.ctx.data.scannerUrl,context};};
  await s.run("openScanner('new')");
  assert.equal(requested.url,'/api/scanner-status');assert.equal(requested.options.cache,'no-store');
  assert.deepEqual(navigated,{url:'https://fresh.trycloudflare.com',context:'new'});
});
test('offline secure scanner shows recovery instead of opening a broken address',async()=>{
  const s=setup();s.ctx.window.isSecureContext=false;s.ctx.data.scannerUrl='https://expired.trycloudflare.com';
  let navigated=false;s.ctx.openSecureScanner=()=>{navigated=true;};
  s.ctx.fetch=async()=>({ok:true,json:async()=>({ready:false,message:'Scanner is reconnecting'})});
  await s.run("openScanner('new')");assert.equal(navigated,false);assert.equal(s.el('scanner-dialog').open,false);
  assert.deepEqual(s.calls.at(-1),['Scanner is reconnecting',true]);
});
test('repeated scanner taps do not start competing secure navigations',async()=>{
  const s=setup();s.ctx.window.isSecureContext=false;let release,requests=0,navigations=0;
  s.ctx.fetch=()=>{requests++;return new Promise(resolve=>{release=resolve;});};s.ctx.openSecureScanner=()=>{navigations++;};
  const first=s.run("openScanner('new')");await s.run("openScanner('new')");
  release({ok:true,json:async()=>({ready:true,url:'https://fresh.trycloudflare.com'})});await first;
  assert.equal(requests,1);assert.equal(navigations,1);
});


test('scanner loads its decoder on demand after camera permission',async()=>{
 const s=setup();s.ctx.window.ZXingBrowser=false;let scriptLoads=0;
 s.ctx.window.WarehouseUI={script:async src=>{assert.equal(src,'/static/vendor/zxing-browser.min.js');scriptLoads++;s.ctx.window.ZXingBrowser=true;}};
 await s.run("openScanner('new')");
 assert.equal(scriptLoads,1);assert.ok(s.camera.callback);assert.ok(s.camera.constraints);
});

test('closing during a lazy decoder load stops the camera immediately',async()=>{
 const s=setup();s.ctx.window.ZXingBrowser=false;let resolveDecoder;
 s.ctx.window.WarehouseUI={script:()=>new Promise(resolve=>{resolveDecoder=resolve;})};
 const opening=s.run("openScanner('new')");await new Promise(resolve=>setImmediate(resolve));
 assert.ok(s.el('scanner-video').srcObject);s.run('closeScanner()');assert.ok(s.camera.stops>0);
 s.ctx.window.ZXingBrowser=true;resolveDecoder();await opening;assert.equal(s.camera.callback,undefined);
});
