// This page receives only a local return address and a random token in the URL fragment.
// It never fetches inventory, uploads images, or sends barcode values to its server.
const camera = document.getElementById('camera'), status = document.getElementById('status');
const options = new URLSearchParams(location.hash.slice(1));
let scannerControls = null, scannerGeneration = 0;

function localReturn(value) {
  try {
    const url = new URL(value);
    const parts = url.hostname.split('.').map(Number);
    const ipv4 = parts.length === 4 && parts.every(n => Number.isInteger(n) && n >= 0 && n <= 255);
    const privateIp = ipv4 && (parts[0] === 10 || parts[0] === 127 || (parts[0] === 192 && parts[1] === 168) || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31));
    if (!['http:', 'https:'].includes(url.protocol) || !(privateIp || url.hostname === 'localhost') || url.port !== '5077' || url.username || url.password) return null;
    return new URL('/', url.origin);
  } catch { return null; }
}

const returnUrl = localReturn(options.get('back'));
const token = options.get('token') || '';
const validReturn = returnUrl && /^[a-f0-9]{32}$/.test(token);

function stopScanner() {
  scannerGeneration++;
  if (scannerControls) { scannerControls.stop(); scannerControls = null; }
  if (camera.srcObject) camera.srcObject.getTracks().forEach(track => track.stop());
  camera.srcObject = null;
}

function returnToWarehouse(code) {
  stopScanner();
  if (!validReturn) { status.textContent = 'Open this scanner using Scan barcode in the warehouse app.'; return; }
  returnUrl.hash = new URLSearchParams({scanReturn:token, ...(code ? {barcode:code} : {cancelled:'1'})}).toString();
  location.replace(returnUrl.href);
}

async function startScanner() {
  stopScanner();
  if (!validReturn) { status.textContent = 'Open this scanner using Scan barcode in the warehouse app.'; return; }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) { status.textContent = 'Open the secure scanner address directly in Chrome to enable camera access.'; return; }
  const generation = scannerGeneration;
  let stream;
  status.textContent = 'Waiting for camera permission. Tap Allow if asked.';
  try {
    try {
      stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
    } catch (error) {
      if (generation !== scannerGeneration) return;
      if (!['OverconstrainedError', 'NotFoundError'].includes(error.name)) throw error;
      stream = await navigator.mediaDevices.getUserMedia({video:true,audio:false});
    }
    if (generation !== scannerGeneration) { stream.getTracks().forEach(track => track.stop()); return; }
    camera.muted = true; camera.playsInline = true; camera.srcObject = stream;
    const reader = new ZXingBrowser.BrowserMultiFormatOneDReader();
    const controls = await reader.decodeFromStream(stream, camera, (result, error, controls) => {
      if (generation !== scannerGeneration) { controls.stop(); return; }
      if (result) { controls.stop(); returnToWarehouse(result.getText()); }
    });
    if (generation !== scannerGeneration) { controls.stop(); return; }
    scannerControls = controls;
    status.textContent = 'Scanning automatically. Hold the barcode steady in good light.';
  } catch (error) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (generation !== scannerGeneration) return;
    stopScanner();
    const messages = {
      NotAllowedError:'Camera access is blocked. In Chrome, open this site’s permissions and allow Camera, then cancel and scan again.',
      NotReadableError:'The camera is busy. Close other camera apps and scan again.',
      NotFoundError:'No available camera was found on this device.'
    };
    status.textContent = messages[error.name] || 'The camera could not start. Check camera permissions, then cancel and scan again.';
  }
}

document.getElementById('close').onclick = () => returnToWarehouse();
window.addEventListener('pagehide', stopScanner);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopScanner(); else startScanner(); });
startScanner();
