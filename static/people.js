(() => {
 let directory={employees:[],customers:[]}, pending=null, loaded=0;
 const fields={employeeId:'employees',employeeRefId:'employees',completedBy:'employees',requestedBy:'employees',cancelledBy:'employees',createdBy:'employees',modifiedBy:'employees',clockedInBy:'employees',clockedOutBy:'employees',customerRefId:'customers',customerId:'customers'};
 async function load(){
  if(Date.now()-loaded<15*60000)return directory;
  if(pending)return pending;
  pending=(async()=>{try{const r=await fetch('/api/storehub/partner/people',{cache:'no-store'});if(r.ok){const d=await r.json();directory={employees:d.employees||[],customers:d.customers||[]};if(!d.warnings?.length)loaded=Date.now();}}catch{}finally{pending=null;}return directory;})();
  return pending;
 }
 function resolve(kind,id){return directory[kind]?.find(p=>p.id===String(id))?.name||'';}
 function display(key,value){const kind=fields[key];if(!kind)return value;if(!value)return '—';return resolve(kind,value)||`Unknown ${kind==='employees'?'employee':'customer'} (${value})`;}
 function options(kind,selected=''){return directory[kind].map(p=>({id:p.id,name:p.name})).concat(selected&&!directory[kind].some(p=>p.id===selected)?[{id:selected,name:`Unavailable ${kind==='employees'?'employee':'customer'}`}]:[]);}
 function named(value){if(Array.isArray(value))return value.map(named);if(value&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,fields[k]?display(k,v):named(v)]));return value;}
 window.StoreHubPeople={load,resolve,display,options,named,fields};
})();
