'use strict';
let priceView='groups', priceGroupIds=null, priceGroupSelection=new Set(), priceSearch='',priceDialogGroups=[];

// One group spans every matching listing and all unselected dimensions (e.g. colors).
function priceGroups(rows,dimensions){
 if(!dimensions.length)return [];
 const groups=new Map();
 for(const l of rows)for(const p of products(l)){
  const props=dimensions.map(id=>p.property_values.find(v=>String(v.property_id)===id));
  if(props.some(p=>!p))continue;
  const filters=Object.fromEntries(props.map(p=>[String(p.property_id),p.values]));
  const key=JSON.stringify(filters);
  if(!groups.has(key))groups.set(key,{key,filters,name:props.map(p=>p.values.join(', ')).join(' / '),listing_ids:new Set(),prices:[],combinations:0});
  const g=groups.get(key);g.listing_ids.add(String(l.listing_id));g.prices.push(...p.offerings.map(o=>o.price));g.combinations++;
 }
 return [...groups.values()].map(g=>({...g,listing_ids:[...g.listing_ids]})).sort((a,b)=>a.name.localeCompare(b.name));
}
function activePriceDimensions(rows){
 const available=properties(rows),ids=available.map(g=>g.id);
 const preferred=available.filter(g=>/style|size/i.test(g.name)).map(g=>g.id);
 priceGroupIds=(priceGroupIds|| (preferred.length?preferred:ids.slice(0,1))).filter(id=>ids.includes(id));
 if(!priceGroupIds.length&&ids.length)priceGroupIds=[ids[0]];
 return priceGroupIds;
}
function currentPriceGroups(){return priceGroups(editRows(),activePriceDimensions(editRows()));}
function pendingGroupPrice(g){return rules.find(r=>r.kind==='price'&&JSON.stringify(r.filters)===JSON.stringify(g.filters)&&JSON.stringify(r.listing_ids)===JSON.stringify(g.listing_ids));}
function groupedPriceTable(rows){
 const dimensions=activePriceDimensions(rows),groups=priceGroups(rows,dimensions),visible=groups.filter(g=>g.name.toLowerCase().includes(priceSearch.toLowerCase())),selectedGroups=groups.filter(g=>priceGroupSelection.has(g.key));
 return `<section class="panel"><div class="panel-head"><div><h2>${tr('Prices by variation')}</h2><p>${tr('One row per variation across your selected products. Other sizes keep their prices.')}</p></div>${button('Individual rows & stock','price-row-view','quiet small')}</div><div class="panel-body price-group-controls"><fieldset><legend>${tr('Group prices by')}</legend>${properties(rows).map(g=>`<label><input type="checkbox" data-price-dimension="${g.id}" ${dimensions.includes(g.id)?'checked':''}>${e(g.name)}</label>`).join('')}</fieldset><p class="hint">${tr('Select Style and Size to price each style/size together. Leave Color unchecked to apply the price to every existing color.')}</p></div><div class="toolbar"><div class="search-wrap"><span>⌕</span><input id="price-group-search" value="${e(priceSearch)}" placeholder="${tr('Find a style or size…')}" aria-label="${tr('Find price groups')}"></div>${button('Change selected prices','price-bulk','',selectedGroups.length?'':'disabled')}<span class="hint">${tr('{count} selected',{count:selectedGroups.length})}</span></div><div class="table-wrap"><table class="grouped-price-table"><thead><tr><th><input type="checkbox" id="price-groups-all" aria-label="${tr('Select visible price groups')}" ${visible.length&&visible.every(g=>priceGroupSelection.has(g.key))?'checked':''}></th><th>${tr('Variation')}</th><th>${tr('Products')}</th><th>${tr('Current price')}</th><th>${tr('New price')}</th></tr></thead><tbody>${visible.map(g=>{
 const min=Math.min(...g.prices),max=Math.max(...g.prices),pending=pendingGroupPrice(g);
 return `<tr><td><input type="checkbox" data-price-group="${e(g.key)}" aria-label="${tr('Select {name}',{name:g.name})}" ${priceGroupSelection.has(g.key)?'checked':''}></td><td><strong>${e(g.name)}</strong><small class="product-subtitle">${tr('{count} existing combinations',{count:g.combinations})}</small></td><td>${tr('{count} of {total}',{count:g.listing_ids.length,total:rows.length})}</td><td>${e(formatMoney(min))}${min!==max?' – '+e(formatMoney(max)):''}</td><td><input class="group-price-input" type="number" step="0.01" min="0.01" data-group-price="${e(g.key)}" aria-label="${tr('New price for {name}',{name:g.name})}" value="${pending?.mode==='set'?e(pending.value):''}" placeholder="${tr('Keep current')}">${pending?`<small class="pending-tag">${e(pending.mode==='set'?t('Pending price'):ruleSummary(pending))}</small>`:''}</td></tr>`;
 }).join('')||`<tr><td colspan="5" class="empty">${tr('No matching price groups. Choose a variation above or change your search.')}</td></tr>`}</tbody></table></div><div class="table-footer">${tr('{count} variation groups across {products} selected products',{count:groups.length,products:rows.length})}</div><div class="panel-body"><p class="hint">${tr('Enter a new price beside each variation, or select groups to change prices by an amount or percentage. Missing combinations are not created. Review before saving to Etsy.')}</p></div></section>`;
}
function stageGroupPrices(groups,mode,value){
 const additions=groups.map(g=>({kind:'price',mode,value,_price_group:true,filters:g.filters,listing_ids:g.listing_ids,_label:t('{name} · Price',{name:g.name})}));
 for(const r of additions){
  const i=rules.findIndex(x=>x.kind==='price'&&JSON.stringify(x.filters)===JSON.stringify(r.filters)&&JSON.stringify(x.listing_ids)===JSON.stringify(r.listing_ids));
  if(i>=0)rules[i]=r;else rules.push(r);
 }
 saveDraft();
}
function showGroupedPriceDialog(groups,rule=null){
 priceDialogGroups=groups;
  modal('Change selected prices',`<p>${tr('{count} variation groups across selected products',{count:groups.length})}</p>${field('Change',`<select id="group-price-mode">${[['set','Set to'],['add','Increase by'],['subtract','Decrease by'],['percent','Change by percentage']].map(([id,name])=>option(id,t(name),rule?.mode||'set')).join('')}</select>`)}${field('Value',`<input id="group-price-value" type="number" step="0.01" value="${e(rule?.value??'')}" required>`)}<div id="edit-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button('Add price changes','price-confirm','')}</div>`);
}
document.addEventListener('click',event=>{
 const n=event.target.closest('[data-action]');if(!n)return;
 if(!['price-group-view','price-row-view','price-bulk','price-confirm'].includes(n.dataset.action))return;
 event.stopImmediatePropagation();clearError();
 if(n.dataset.action==='price-group-view'){priceView='groups';render();}
 if(n.dataset.action==='price-row-view'){priceView='rows';render();}
 if(n.dataset.action==='price-bulk'){
  const groups=currentPriceGroups().filter(g=>priceGroupSelection.has(g.key));
  showGroupedPriceDialog(groups);
 }
 if(n.dataset.action==='price-confirm'){
  const raw=$('#group-price-value').value,mode=$('#group-price-mode').value,value=Number(raw);
  if(!raw||!Number.isFinite(value)||(mode==='set'&&value<=0)||(['add','subtract'].includes(mode)&&value<0)||(mode==='percent'&&value<=-100)){$('#edit-error').textContent=t('Enter a valid price change. Resulting prices must be greater than zero.');$('#edit-error').classList.remove('hidden');return;}
  stageGroupPrices(priceDialogGroups,mode,value);$('#dialog').close();render();
 }
},true);
document.addEventListener('change',event=>{
 const n=event.target;
 if(n.dataset.priceDimension){
  event.stopImmediatePropagation();const ids=activePriceDimensions(editRows());
  if(!n.checked&&ids.length===1){n.checked=true;return;}
  priceGroupIds=n.checked?[...ids,n.dataset.priceDimension]:ids.filter(id=>id!==n.dataset.priceDimension);
  priceGroupSelection.clear();render();
 }
 if(n.dataset.priceGroup){event.stopImmediatePropagation();n.checked?priceGroupSelection.add(n.dataset.priceGroup):priceGroupSelection.delete(n.dataset.priceGroup);render();}
 if(n.id==='price-groups-all'){event.stopImmediatePropagation();for(const g of currentPriceGroups().filter(g=>g.name.toLowerCase().includes(priceSearch.toLowerCase())))n.checked?priceGroupSelection.add(g.key):priceGroupSelection.delete(g.key);render();}
 if(n.dataset.groupPrice){
  event.stopImmediatePropagation();clearError();const g=currentPriceGroups().find(g=>g.key===n.dataset.groupPrice),value=Number(n.value);
  if(!g)return;
  if(!n.value){const r=pendingGroupPrice(g);if(r){rules.splice(rules.indexOf(r),1);saveDraft();}render();return;}
  if(!Number.isFinite(value)||value<=0){fail(Error(t('Enter a price greater than zero.')));n.focus();return;}
  stageGroupPrices([g],'set',value);render();
 }
},true);
document.addEventListener('input',event=>{
 const n=event.target;
 if(n.id==='price-group-search'){priceSearch=n.value;render();}
 if(n.dataset.groupPrice){
  const value=Number(n.value),g=currentPriceGroups().find(g=>g.key===n.dataset.groupPrice);
  if(!g||!n.value||!Number.isFinite(value)||value<=0)return;
  stageGroupPrices([g],'set',value);
  // Keep the active field in place while typing; Review is available immediately.
  for(const b of document.querySelectorAll('[data-action="preview"]')){b.disabled=false;b.textContent=t('Review changes ({count})',{count:rules.length});}
  const count=document.querySelector('.pending-panel .chip');if(count)count.textContent=rules.length;
 }
});
