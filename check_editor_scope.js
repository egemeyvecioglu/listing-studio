// Focused offline regression: selection-scoped drafts and named drag/drop moves.
const fs=require('node:fs'),vm=require('node:vm');
const saved=new Map(),listeners=new Map();
const dom={addEventListener(){},close(){}};
const ctx={requestAnimationFrame:()=>1,cancelAnimationFrame(){},structuredClone,setTimeout,clearTimeout,localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},window:{addEventListener(){},scrollTo(){}},document:{querySelector:()=>dom,querySelectorAll:()=>[],addEventListener:(type,fn)=>{if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);}}};
vm.createContext(ctx);
for(const file of ['app','images','prices','review','option_order'])vm.runInContext(fs.readFileSync('web/'+file+'.js','utf8').replace(/^start\(\);$/m,''),ctx);
vm.runInContext(`
render=()=>{};notify=()=>{};
state={shops:[{shop_id:'sample',name:'Sample',currency:'USD'}],listings:['1','2'].map(id=>({listing_id:id,shop_id:'sample',snapshot:{inventory:{products:['Youth Tee / S','Adult Hoodie / XL'].map(style=>({sku:'',property_values:[{property_id:513,property_name:'Style / Size',values:[style]}],offerings:[{price:20,quantity:20}]}))}}}))};
shopId='sample';selected=new Set(['1','2']);editorScope=scopeKey(selected);
rules=[{kind:'price',mode:'set',value:30,listing_ids:['1','2'],filters:{}}];saveDraft();
inspect('1');
if(rules.length||selected.size!==1||!selected.has('1'))throw Error('Single editor inherited a bulk draft');
if(editorDrafts[scopeKey(['1','2'])].rules.length!==1)throw Error('Bulk draft was lost');
rules=[{kind:'price',mode:'set',value:25,listing_ids:['1'],filters:{}}];saveDraft();
let payload=reviewRequest();if(JSON.stringify(payload.listing_ids)!=='["1"]'||payload.rules.some(r=>r.listing_ids.some(id=>id!=='1')))throw Error('Single review expanded its targets');
beginEditor(['1','2']);if(rules[0].value!==30)throw Error('Bulk draft was not restored');
inspect('1');if(rules[0].value!==25)throw Error('Single draft was not restored');
rules.push({kind:'price',mode:'set',value:99,listing_ids:['2'],filters:{}});
payload=reviewRequest();if(payload.rules.length!==1||payload.listing_ids.length!==1)throw Error('Foreign targets leaked into preview');
localStorage.setItem('studio:draft:sample',JSON.stringify({selected:['1'],rules:[{kind:'price',mode:'set',value:33,listing_ids:['1','2']}]}));loadDraft();
if(rules.length)throw Error('Legacy bulk draft migrated into a single editor');
beginEditor(['1','2']);if(rules[0].value!==33)throw Error('Legacy bulk draft was discarded');
inspect('1');rules=[{kind:'option',mode:'copy',property_id:'513',old:'Youth Tee / S',value:'Youth Long Sleeve S',quantity:999,listing_ids:['1']}];
`,ctx);
const classes=new Set(),row={dataset:{optionRow:'Youth Long Sleeve S',optionGroup:'513'},classList:{add:x=>classes.add(x),remove:(...xs)=>xs.forEach(x=>classes.delete(x))}};
const handle={dataset:{optionDrag:'Youth Long Sleeve S',group:'513'},closest:()=>row,setPointerCapture(){}};
for(const fn of listeners.get('pointerdown'))fn({target:{closest:()=>handle},button:0,pointerId:1,clientX:20,clientY:200,preventDefault(){}});
const target={dataset:{optionRow:'Adult Hoodie / XL',optionGroup:'513'},getBoundingClientRect:()=>({top:100,height:50}),classList:row.classList};
ctx.document.elementFromPoint=()=>({closest:()=>target});
const event={clientX:100,clientY:105};
for(const fn of listeners.get('pointermove'))fn(event);
if(!classes.has('option-drop-before'))throw Error('Drop position was not indicated');
for(const fn of listeners.get('pointerup'))fn(event);
vm.runInContext(`
const order=rules.find(r=>r.kind==='order');
if(order.moves[0].value!=='Youth Long Sleeve S'||order.moves[0].anchor!=='Adult Hoodie / XL'||order.moves[0].position!=='before')throw Error('Drag used an index instead of named options');
if(orderedOptionEntries(groupById('513')).map(x=>x.value).join('|')!=='Youth Tee / S|Youth Long Sleeve S|Adult Hoodie / XL')throw Error('Dragged option did not move in the list');
`,ctx);
// Single-listing image completion must leave other pending targets intact.
vm.runInContext(`
imageDraftShop=shopId;
imageDraft={uploads:[{id:'asset',name:'sample.png',url:'/api/image-file?id=asset'}],position:'last',targets:['1','2'],removed:{'1':['a'],'2':['b']},reviewId:'one-image-review'};
batchData={batch:{id:'one-image-review',type:'images',status:'complete',sample:true,shop_name:'Sample',shop_id:'sample',items:[{listing_id:'1',title:'One',status:'complete',before_images:[],after_images:[]}]}};
imageReviewPage();
if(imageDraft.targets.join()!=='2'||imageDraft.uploads.length!==1||!imageDraft.removed['2'])throw Error('Single image save cleared another listing draft');
`,ctx);
console.log('Editor check passed: single/bulk drafts isolated and preserved, legacy migration, strict preview scope, and named drag/drop placement.');
