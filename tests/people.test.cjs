const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(fetch){const context={window:{},fetch,Date};vm.createContext(context);vm.runInContext(fs.readFileSync('static/people.js','utf8'),context);return context.window.StoreHubPeople;}
test('name directory shares requests and caches successful lookups',async()=>{
 let calls=0,resolve;const response=new Promise(r=>resolve=r),people=harness(()=>{calls++;return response;});
 const first=people.load(),second=people.load();assert.equal(calls,1);
 resolve({ok:true,json:async()=>({employees:[{id:'e1',name:'Ana Staff'}],customers:[{id:'c1',name:'Ben Customer'}],warnings:[]})});
 await Promise.all([first,second]);await people.load();assert.equal(calls,1);
 assert.equal(people.display('employeeId','e1'),'Ana Staff');assert.equal(people.display('customerRefId','c1'),'Ben Customer');
 assert.match(people.display('employeeId','gone'),/Unknown employee/);
 assert.equal(people.options('employees')[0].id,'e1');
 const raw={employeeId:'e1',payments:[{customerRefId:'c1'}]};const named=people.named(raw);
 assert.equal(named.payments[0].customerRefId,'Ben Customer');assert.equal(raw.payments[0].customerRefId,'c1');
});
test('a failed directory load can retry and does not erase known names',async()=>{
 let calls=0;const people=harness(async()=>{if(++calls===1)throw Error('Offline');return {ok:true,json:async()=>({employees:[{id:'e1',name:'Ana Staff'}],customers:[],warnings:[]})};});
 await people.load();await people.load();assert.equal(calls,2);assert.equal(people.resolve('employees','e1'),'Ana Staff');
});
