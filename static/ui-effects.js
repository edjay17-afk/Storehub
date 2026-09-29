(() => {
 const busy=new WeakMap(), scripts=new Map();
 const reduced=()=>window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 function enter(element){
  if(!element||reduced()||!element.animate)return;
  element.getAnimations().forEach(animation=>animation.cancel());
  element.animate([{opacity:0,transform:'translateY(9px)'},{opacity:1,transform:'translateY(0)'}],{duration:220,easing:'cubic-bezier(.2,.7,.3,1)'});
 }
 function loading(element,label='Loading records'){
  if(!element)return ()=>{};
  let state=busy.get(element);
  if(!state){
   const placeholder=document.createElement('div');placeholder.className='ui-loading-placeholder';placeholder.setAttribute('role','status');placeholder.setAttribute('aria-label',label);
   placeholder.innerHTML='<span class="ui-loading-label"></span><span class="ui-skeleton"></span><span class="ui-skeleton"></span><span class="ui-skeleton"></span>';
   placeholder.querySelector('.ui-loading-label').textContent=label+'…';
   state={count:0,placeholder,previous:element.getAttribute('aria-busy')};busy.set(element,state);
   element.classList.add('ui-loading');element.setAttribute('aria-busy','true');element.append(placeholder);
  }
  state.count++;let finished=false;
  return ()=>{if(finished)return;finished=true;if(--state.count)return;state.placeholder.remove();element.classList.remove('ui-loading');if(state.previous===null)element.removeAttribute('aria-busy');else element.setAttribute('aria-busy',state.previous);busy.delete(element);};
 }
 async function json(url,options={},element){
  const finish=loading(element);
  try{const response=await fetch(url,options);const result=await response.json();if(!response.ok)throw Error(result.error||'Could not load records.');return result;}
  finally{finish();}
 }
 function script(src){
  if(scripts.has(src))return scripts.get(src);
  const promise=new Promise((resolve,reject)=>{const node=document.createElement('script');node.src=src;node.async=true;node.onload=resolve;node.onerror=()=>{node.remove();scripts.delete(src);reject(Error('Could not load the scanner. Check your connection and try again.'));};document.head.append(node);});
  scripts.set(src,promise);return promise;
 }
 window.WarehouseUI={enter,loading,json,script};
})();
