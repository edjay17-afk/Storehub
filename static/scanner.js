// Barcode text is kept as a string so leading zeroes survive CSV export.
let scanContext = 'new', scanControls = null, scanGeneration = 0, matchedProduct = null;
const scannerPages = ['catalog','requests','receiving','documents','activity','purchase-orders','imported-transfers','manual-inventory','wastage','replenishment','storehub','partner-tools'];
function prepareSecureScanReturn() {
  const returned = new URLSearchParams(location.hash.slice(1)).get('scanReturn');
  if (!returned) return;
  try {
    const pending = JSON.parse(sessionStorage.getItem('centro-scan-pending') || 'null');
    if (pending?.token === returned && Number.isFinite(pending.created) && pending.created <= Date.now() && Date.now()-pending.created <= 30*60*1000 && scannerPages.includes(pending.draft?.view)) {
      view(pending.draft.view, false);
    }
  } catch { /* Leave invalid or expired returns for the normal error message. */ }
  finally { document.documentElement?.classList?.remove('scan-return-loading'); }
}

function barcodeMatches(code) {
  return data.products.filter(p => p.Barcode.split(',').some(b => b.trim() === code));
}

function barcodeFeedback(text, error = false) {
  $('barcode-feedback').textContent = text;
  $('barcode-feedback').classList.toggle('negative', error);
}

function scannerFeedback(context, text) {
  if (context === 'inventory' && $('inventory-dialog').open) inventoryFeedback(text, true, true);
  else message(text, true);
}

function applyNewBarcode(code) {
  if (!code) { barcodeFeedback('Scan or enter a barcode first.', true); return; }
  const found = barcodeMatches(code);
  $('product-barcode').value = code;
  matchedProduct = found.length === 1 ? found[0] : null;
  $('existing-receive').hidden = !matchedProduct || matchedProduct['Track Stock Levels'] !== '1';
  if (found.length) {
    barcodeFeedback(`Already listed: ${found.map(p => p['Product Name']).join(', ')}. Receive stock for the existing product.`, true);
    return;
  }
  const form = $('product-form');
  if (!form.elements.sku.value) form.elements.sku.value = code;
  barcodeFeedback(`Barcode ${code} added. Enter the product name, cost and price.`);
  form.elements.name.focus();
}

function useBarcode(raw) {
  const code = String(raw).trim();
  if (!code) throw Error('Scan or enter a barcode first.');
  if (scanContext === 'new') {
    applyNewBarcode(code);
    return;
  }
  if (scanContext === 'inventory') {
    inventoryApplyBarcode(code);
    return;
  }
  const found = barcodeMatches(code);
  if (scanContext === 'catalog') {
    view('catalog');
    $('search').value = code;
    page = 0;
    renderProducts();
    message(found.length ? `${found.length} product(s) match barcode ${code}.` : `No existing product has barcode ${code}. Use New product to add it.`);
    return;
  }
  if (found.length !== 1) throw Error(found.length ? 'More than one product has this barcode. Select the product by name.' : 'Barcode not found. Add the product first.');
  const p = found[0];
  if (p['Track Stock Levels'] !== '1') throw Error('This product does not track stock.');
  if (scanContext === 'receive') {
    selectReceive(p);
    view('receiving');
    $('receive-form').elements.quantity.focus();
  } else if (scanContext === 'request') {
    if (lines.some(l => l.product_id === p.id)) throw Error('This product is already in the request. Change its quantity in the list.');
    lines.push({product_id:p.id, quantity:'1', cost:p.Cost || '0'});
    renderLines();
  }
}

function stopCamera() {
  scanGeneration++;
  if (scanControls) { scanControls.stop(); scanControls = null; }
  const video = $('scanner-video');
  if (video.srcObject) video.srcObject.getTracks().forEach(track => track.stop());
  video.srcObject = null;
}

function closeScanner() {
  stopCamera();
  if ($('scanner-dialog').open) $('scanner-dialog').close();
}

function finishScan(code) {
  stopCamera();
  $('scan-code').value = code;
  try {
    useBarcode(code);
    closeScanner();
  } catch (error) {
    $('scanner-status').textContent = error.message;
  }
}

function scanDraft() {
  const values = id => Object.fromEntries(new FormData($(id)));
  return {
    product:values('product-form'), request:values('request-form'), receive:values('receive-form'),
    receiveId:$('receive-id').value, receiveSearch:$('receive-search').value,
    lines, location:$('location').value, search:$('search').value,
    view:document.querySelector('.view:not(.hidden)')?.id || 'catalog',
    inventory:scanContext === 'inventory' && typeof inventoryScanDraft === 'function' ? inventoryScanDraft() : null
  };
}

function openSecureScanner(context) {
  try {
    scanContext = context;
    const secure = new URL(data.scannerUrl);
    if (secure.protocol !== 'https:' || !secure.hostname.endsWith('.trycloudflare.com')) throw Error('The secure scanner address is invalid.');
    const token = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('');
    sessionStorage.setItem('centro-scan-pending', JSON.stringify({token, context, created:Date.now(), draft:scanDraft()}));
    // A fragment is not sent to the HTTPS service. No form data leaves this browser.
    secure.hash = new URLSearchParams({back:location.origin, token}).toString();
    location.assign(secure.href);
  } catch (error) { scannerFeedback(context, error.message || 'Could not open the secure scanner. Check browser storage permissions.'); }
}

async function restoreSecureScanReturn() {
  const params = new URLSearchParams(location.hash.slice(1));
  const returned = params.get('scanReturn');
  if (!returned || !data) return;
  history.replaceState(null, '', location.pathname + location.search);
  let pending;
  try { pending = JSON.parse(sessionStorage.getItem('centro-scan-pending') || 'null'); } catch { /* invalid or unavailable storage */ }
  if (!pending || pending.token !== returned || !Number.isFinite(pending.created) || pending.created > Date.now() || Date.now() - pending.created > 30 * 60 * 1000 || !['new','catalog','receive','request','inventory'].includes(pending.context) || !pending.draft) {
    message('This scan has expired or was opened in a different tab. Tap Scan barcode again.', true);
    return;
  }
  sessionStorage.removeItem('centro-scan-pending');
  const d = pending.draft;
  for (const [id, values] of [['product-form',d.product],['request-form',d.request],['receive-form',d.receive]]) {
    for (const [name,value] of Object.entries(values)) {
      const element = $(id).elements.namedItem(name);
      if (element) element.value = value;
    }
  }
  $('receive-id').value = d.receiveId;
  $('receive-search').value = d.receiveSearch;
  $('search').value = d.search;
  if (data.locations.includes(d.location)) $('location').value = d.location;
  lines = (d.lines || []).filter(l => data.products.some(p => p.id === l.product_id));
  $('kind').onchange(); renderLines(); renderProducts();
  if (pending.context === 'inventory') {
    try {
      await inventoryRestoreScan(d.inventory);
      if (params.get('cancelled')) return;
      const barcode = params.get('barcode') || '';
      if (barcode.length > 256) throw Error('This barcode is too long.');
      inventoryApplyBarcode(barcode);
    } catch (error) { scannerFeedback('inventory',error.message); }
    return;
  }
  view(scannerPages.includes(d.view) ? d.view : 'catalog');
  scanContext = pending.context;
  if (scanContext === 'new') { $('product-dialog').showModal(); }
  if (params.get('cancelled')) return;
  try {
    const barcode = params.get('barcode') || '';
    if (barcode.length > 256) throw Error('This barcode is too long.');
    useBarcode(barcode);
    if (scanContext === 'new' && !barcodeMatches(barcode).length) $('product-form').elements.name.focus();
  } catch (error) { message(error.message, true); }
}

async function startCamera() {
  stopCamera();
  const generation = scanGeneration;
  const video = $('scanner-video');
  let stream = null;
  $('scanner-status').textContent = 'Waiting for camera access. Choose Allow if your browser asks.';
  try {
    if (!window.isSecureContext) {
      $('scanner-status').textContent = 'Live camera scanning needs the secure HTTPS scanner connection. Refresh the app after the secure scanner is connected, then tap Scan barcode again.';
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      $('scanner-status').textContent = 'This browser does not support live camera scanning. Open the app in Safari on iPhone or Chrome on Android, or enter a barcode below.';
      return;
    }
    if (!window.ZXingBrowser && !window.WarehouseUI) {
      $('scanner-status').textContent = 'The scanner did not load. Refresh the page and open the scanner again, or enter a barcode below.';
      return;
    }
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    // Request permission directly, without a device-enumeration or Permissions API prerequisite.
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false
      });
    } catch (error) {
      if (generation !== scanGeneration) return;
      if (!['OverconstrainedError', 'NotFoundError'].includes(error.name)) throw error;
      // Some mobile cameras reject optional camera/resolution preferences.
      stream = await navigator.mediaDevices.getUserMedia({video:true,audio:false});
    }
    if (generation !== scanGeneration || !$('scanner-dialog').open) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    video.srcObject = stream;
    if (!window.ZXingBrowser) {
      $('scanner-status').textContent = 'Loading barcode scanner...';
      await window.WarehouseUI.script('/static/vendor/zxing-browser.min.js');
      if (generation !== scanGeneration || !$('scanner-dialog').open) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      if (!window.ZXingBrowser) throw Error('Barcode scanner did not load.');
    }
    const reader = new ZXingBrowser.BrowserMultiFormatOneDReader();
    const controls = await reader.decodeFromStream(
      stream,
      video,
      (result, error, controls) => {
        if (generation !== scanGeneration || !$('scanner-dialog').open) { controls.stop(); return; }
        if (result) { controls.stop(); finishScan(result.getText()); }
      }
    );
    if (generation !== scanGeneration || !$('scanner-dialog').open) { controls.stop(); return; }
    scanControls = controls;
    $('scanner-status').textContent = 'Hold the barcode steady in good light. Scanning...';
  } catch (error) {
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (generation !== scanGeneration) return;
    stopCamera();
    const explanations = {
      NotAllowedError:'Camera access was denied or blocked. Allow Camera in this website’s browser permissions, then close and reopen the scanner. You can also enter a barcode below.',
      SecurityError:'Camera access is blocked by browser settings. Open the HTTPS app directly in Safari or Chrome and allow Camera for this website.',
      NotFoundError:'No available camera was found. You can still enter a barcode or use a USB scanner.',
      NotReadableError:'The camera is busy. Close other camera apps, then close and reopen the scanner.',
      AbortError:'The camera could not open this time. Close and reopen the scanner to try again.',
      OverconstrainedError:'This camera cannot use the requested settings. You can still enter a barcode below.'
    };
    $('scanner-status').textContent = explanations[error.name] || 'The camera could not open. Check this website’s camera permission, then close and reopen the scanner. Barcode entry is still available below.';
  }
}

let secureScannerOpening = false;
async function openScanner(context) {
  if (!data) return message('Wait for the product catalog to load.', true);
  stopCamera();
  if (!window.isSecureContext && typeof fetch === 'function') {
    if (secureScannerOpening) return;
    secureScannerOpening = true;
    try {
      const response = await fetch('/api/scanner-status', {cache:'no-store'});
      if (!response.ok) throw Error('Could not check the secure scanner. Try again.');
      const state = await response.json();
      if (!state.ready) throw Error(state.message || 'The secure scanner is reconnecting. Try again shortly.');
      data.scannerUrl = state.url;
      openSecureScanner(context);
    } catch (error) { scannerFeedback(context, error.message || 'Could not reach the scanner. Check your Wi-Fi connection.'); }
    finally { secureScannerOpening = false; }
    return;
  }
  if (!window.isSecureContext && data.scannerUrl) { openSecureScanner(context); return; }
  scanContext = context;
  $('scanner-video').hidden = !window.isSecureContext;
  $('scanner-instructions').textContent = window.isSecureContext
    ? 'Allow camera access if asked, then point the camera at the product barcode. Scans are processed on this device.'
    : 'Live camera scanning needs the secure scanner connection.';
  $('scan-code').value = '';
  $('scanner-dialog').showModal();
  // Avoid opening the phone keyboard over the camera preview.
  const mobile = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
  if (!mobile) $('scan-code').focus();
  await startCamera();
}
document.querySelectorAll('[data-scan]').forEach(button => button.onclick = () => openScanner(button.dataset.scan));
$('close-scanner').onclick = closeScanner;
$('scanner-dialog').addEventListener('cancel', stopCamera);
$('scanner-dialog').addEventListener('close', stopCamera);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopCamera();
  else if ($('scanner-dialog').open) startCamera();
});
window.addEventListener('pagehide', stopCamera);
$('scan-code-form').onsubmit = event => { event.preventDefault(); finishScan($('scan-code').value); };
$('product-barcode').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); applyNewBarcode(event.target.value.trim()); }
});
$('product-barcode').addEventListener('input', () => {
  matchedProduct = null;
  $('existing-receive').hidden = true;
  barcodeFeedback('');
});
$('product-barcode').addEventListener('change', event => {
  const code = event.target.value.trim();
  if (code && !code.includes(',')) applyNewBarcode(code);
  else { matchedProduct = null; $('existing-receive').hidden = true; barcodeFeedback(''); }
});
$('existing-receive').onclick = () => {
  if (!matchedProduct) return;
  $('product-dialog').close();
  selectReceive(matchedProduct);
  view('receiving');
  $('receive-form').elements.quantity.focus();
};
prepareSecureScanReturn();
if (data) restoreSecureScanReturn();
