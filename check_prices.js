// Focused offline check for grouping and staging prices across listings.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const ctx={document:{addEventListener(){}},products:l=>l.snapshot.inventory.products,t:(k,a)=>k.replace('{name}',a.name),rules:[],saveDraft(){}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/prices.js','utf8'),ctx);
const rows=[1,2,3].map(id=>({listing_id:id,snapshot:{inventory:{products:['Sweatshirt / S','Sweatshirt / M'].flatMap((style,i)=>Array.from({length:id},(_,c)=>({property_values:[{property_id:513,values:[style]},{property_id:514,values:['Color '+id+'-'+c]}],offerings:[{price:20+id+i}]})))}}}));
ctx.rows=rows;
vm.runInContext(`
 const grouped=priceGroups(rows,['513']);
 if(grouped.length!==2)throw Error('Expected two shared style/size rows, not listing/color rows');
 for(const g of grouped)if(g.listing_ids.length!==3||g.combinations!==6)throw Error('Missing listings or colors');
 stageGroupPrices([grouped.find(g=>g.name==='Sweatshirt / S')],'set',35);
 stageGroupPrices([grouped.find(g=>g.name==='Sweatshirt / M')],'set',40);
 stageGroupPrices([grouped.find(g=>g.name==='Sweatshirt / S')],'set',36);
`,ctx);
assert.equal(ctx.rules.length,2);assert.deepEqual(Array.from(ctx.rules,r=>r.value),[36,40]);
assert.equal(Object.hasOwn(ctx.rules[0].filters,'514'),false);
assert.deepEqual(Array.from(ctx.rules[0].listing_ids),['1','2','3']);
assert.equal(vm.runInContext("priceGroups(rows,['513','514']).length",ctx),12);
// Separate Style and Size dimensions must also group correctly without Color.
ctx.separate=[{listing_id:1,snapshot:{inventory:{products:['Red','Blue'].map(color=>({property_values:[{property_id:1,values:['Sweatshirt']},{property_id:2,values:['S']},{property_id:3,values:[color]}],offerings:[{price:30}]}))}}}];
assert.equal(vm.runInContext("priceGroups(separate,['1','2']).length",ctx),1);
console.log('Grouped price check passed: shared rows, different listing/color sets, separate style/size dimensions, and independent prices.');
