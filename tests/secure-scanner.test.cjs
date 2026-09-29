const test=require('node:test'), assert=require('node:assert/strict'), vm=require('node:vm'), fs=require('node:fs'), path=require('node:path');
function setup(back='http://192.168.110.89:5077') {
  const elements={camera:{srcObject:null},status:{},close:{}}, calls=[], token='a'.repeat(32);
  const ctx={URL,URLSearchParams,location:{hash:'#'+new URLSearchParams({back,token}),replace:url=>calls.push(url)},
    document:{hidden:false,getElementById:id=>elements[id],addEventListener(){}},window:{isSecureContext:true,addEventListener(){}},
    navigator:{mediaDevices:{async getUserMedia(value){calls.push(value);return {getTracks:()=>[{stop(){calls.push('stopped');}}]};}}},
    ZXingBrowser:{BrowserMultiFormatOneDReader:class {async decodeFromStream(stream,video,callback){ctx.callback=callback;return {stop(){calls.push('stopped');}};}}}};
  vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/secure-scanner.js'),'utf8'),ctx);
  return {ctx,elements,calls,token};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('secure page requests live rear camera automatically and returns decoded barcode',async()=>{
  const s=setup();await settle();
  assert.equal(s.calls[0].video.facingMode.ideal,'environment');
  assert.match(s.elements.status.textContent,/Scanning automatically/);
  s.ctx.callback({getText:()=> '00000999'},null,{stop(){}});
  const returned=new URL(s.calls.find(value=>typeof value==='string'&&value.startsWith('http:')));
  assert.equal(returned.origin,'http://192.168.110.89:5077');
  assert.equal(new URLSearchParams(returned.hash.slice(1)).get('barcode'),'00000999');
  assert.equal(new URLSearchParams(returned.hash.slice(1)).get('scanReturn'),s.token);
  assert.equal(s.elements.camera.srcObject,null);
});
test('public scanner refuses external return destinations without opening camera',async()=>{
  const s=setup('https://example.com:5077');await settle();
  assert.equal(s.calls.length,0);assert.match(s.elements.status.textContent,/warehouse app/);
});
test('cancel stops camera and returns to the same warehouse tab',async()=>{
  const s=setup();await settle();s.elements.close.onclick();
  const returned=new URL(s.calls.find(value=>typeof value==='string'&&value.startsWith('http:')));
  assert.equal(new URLSearchParams(returned.hash.slice(1)).get('cancelled'),'1');
  assert.equal(s.elements.camera.srcObject,null);
});
test('late camera permission after cancellation closes the camera immediately',async()=>{
  const s=setup();await settle();let resolveCamera;
  s.ctx.navigator.mediaDevices.getUserMedia=()=>new Promise(resolve=>resolveCamera=resolve);
  const pending=vm.runInContext('startScanner()',s.ctx);
  s.elements.close.onclick();let stopped=false;
  resolveCamera({getTracks:()=>[{stop(){stopped=true;}}]});await pending;
  assert.ok(stopped);assert.equal(s.elements.camera.srcObject,null);
});
