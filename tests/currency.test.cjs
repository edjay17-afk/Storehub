const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const context=vm.createContext({});
vm.runInContext(source.match(/^function formatCost\(value\).*$/m)[0],context);
test('amounts display PHP with two decimals without converting their numeric value',()=>{
 const format=value=>context.formatCost(value);
 assert.equal(format('1,234.567'),'PHP 1,234.57');
 assert.equal(format(0),'PHP 0.00');assert.equal(format(-2.5),'PHP -2.50');
 assert.equal(format(''),'—');assert.equal(format(null),'—');assert.equal(format('invalid'),'—');
});
