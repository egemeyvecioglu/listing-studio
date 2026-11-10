'use strict';
let orderDialogGroup='';
function baseOptionEntries(group){
 const entries=group.values.map(original=>{
  const pending=rules.find(r=>r.kind==='option'&&String(r.property_id)===group.id&&r.old===original&&r.mode!=='copy');
  return {original,value:pending?.mode==='rename'?pending.value:original,removed:pending?.mode==='remove',edited:pending?.mode==='rename'};
 });
 rules.forEach((r,index)=>{
  if(r.kind==='option'&&r.mode==='copy'&&String(r.property_id)===group.id&&!entries.some(x=>x.value===r.value))entries.push({value:r.value,new:true,index,rule:r});
 });
 return entries;
}
function orderedOptionEntries(group){
 const entries=baseOptionEntries(group),active=entries.filter(x=>!x.removed);
 const selectedIds=JSON.stringify(editRows().map(l=>String(l.listing_id)).sort());
 const order=rules.find(r=>r.kind==='order'&&String(r.property_id)===group.id&&JSON.stringify([...(r.listing_ids||[])].sort())===selectedIds);
 for(const move of order?.moves||[]){
  const i=active.findIndex(x=>x.value===move.value),j=active.findIndex(x=>x.value===move.anchor);
  if(i<0||j<0||i===j)continue;
  const [entry]=active.splice(i,1);
  active.splice(active.findIndex(x=>x.value===move.anchor)+(move.position==='after'?1:0),0,entry);
 }
 return [...active,...entries.filter(x=>x.removed)];
}
function renameOrderReferences(group,oldName,newName){
 for(const rule of rules.filter(r=>r.kind==='order'&&String(r.property_id)===String(group)))for(const move of rule.moves){
  if(move.value===oldName)move.value=newName;
  if(move.anchor===oldName)move.anchor=newName;
 }
}
function reconcileOrderRules(){
 rules=rules.filter(r=>{
  if(r.kind!=='order')return true;
  const group=groupById(r.property_id);if(!group)return true;
  const names=new Set(baseOptionEntries(group).filter(x=>!x.removed).map(x=>x.value));
  r.moves=r.moves.filter(m=>names.has(m.value)&&names.has(m.anchor)&&m.value!==m.anchor);
  return r.moves.length>0;
 });
}
function removePendingRule(index){
 const old=rules[index];
 if(old?.kind==='option'&&old.mode==='rename')renameOrderReferences(old.property_id,old.value,old.old);
 rules.splice(index,1);reconcileOrderRules();
}
function stageOptionMove(groupId,value,anchor,position){
 const group=groupById(groupId),names=orderedOptionEntries(group).filter(x=>!x.removed).map(x=>x.value);
 if(!names.includes(value)||!names.includes(anchor)||value===anchor)throw Error(t('Choose a different option to move beside.'));
 let rule=rules.find(r=>r.kind==='order'&&String(r.property_id)===String(groupId));
 if(rule&&JSON.stringify([...(rule.listing_ids||[])].sort())!==JSON.stringify(editRows().map(l=>String(l.listing_id)).sort()))throw Error(t('Review or discard the pending order change before changing the product selection.'));
 if(!rule){rule={kind:'order',property_id:String(groupId),property_name:group.name,moves:[],listing_ids:editRows().map(l=>String(l.listing_id))};rules.push(rule);}
 rule.moves.push({value,anchor,position});rule._label=ruleSummary(rule);saveDraft();
}
function optionRows(group){
 const entries=orderedOptionEntries(group),active=entries.filter(x=>!x.removed);
 return entries.map(entry=>{
  const i=active.indexOf(entry),count=entry.new?editRows().length:editRows().filter(l=>products(l).some(p=>p.property_values.some(v=>String(v.property_id)===group.id&&v.values.includes(entry.original)))).length;
  const attrs=`data-group="${group.id}" data-value="${e(entry.value)}"`;
  const arrows=entry.removed?'':`<button class="icon-button" id="option-up-${group.id}-${e(encodeURIComponent(entry.value))}" data-action="option-up" ${attrs} aria-label="${tr('Move {name} up',{name:entry.value})}" title="${tr('Move up')}" ${i<=0?'disabled':''}>↑</button><button class="icon-button" id="option-down-${group.id}-${e(encodeURIComponent(entry.value))}" data-action="option-down" ${attrs} aria-label="${tr('Move {name} down',{name:entry.value})}" title="${tr('Move down')}" ${i===active.length-1?'disabled':''}>↓</button><button class="text-button option-place" data-action="option-place" ${attrs} aria-label="${tr('Choose position for {name}',{name:entry.value})}" ${active.length<2?'disabled':''}>${tr('Move…')}</button>`;
  const edit=entry.new?actionIcon('edit-pending',t('Edit {name}',{name:entry.value}),'edit',`data-index="${entry.index}"`):actionIcon('rename-option',t('Edit {name}',{name:entry.value}),'edit',`data-group="${group.id}" data-value="${e(entry.original)}"`);
  const remove=entry.new?actionIcon('remove-pending',t('Remove {name}',{name:entry.value}),'trash',`data-index="${entry.index}"`):actionIcon('delete-option',t('Delete {name}',{name:entry.value}),'trash',`data-group="${group.id}" data-value="${e(entry.original)}"`);
  return `<div class="option-row ${entry.new?'staged-option':''}"><div><strong class="${entry.removed?'removed-value':''}">${entry.removed?'':`<span class="option-position">${i+1}.</span> `}${e(entry.value)}</strong>${entry.new||entry.removed||entry.edited?`<span class="pending-tag">${tr(entry.new?'New':entry.removed?'Pending removal':'Edited')}</span>`:''}<small>${entry.new?`${entry.rule.price==null||entry.rule.price===''?tr('Keeps each matching variation’s price'):e(formatMoney(entry.rule.price))} · ${tr('Stock {count}',{count:entry.rule.quantity})}`:tr('{count} of {total} products',{count,total:editRows().length})}</small></div><div class="row-actions option-row-actions">${arrows}${edit}${remove}</div></div>`;
 }).join('');
}
function showOrderDialog(groupId,value=''){
 const group=groupById(groupId);if(!group)return;
 const entries=orderedOptionEntries(group).filter(x=>!x.removed);if(entries.length<2)return;
 orderDialogGroup=groupId;
 const chosen=entries.find(x=>x.value===value)?.value||entries[0].value;
 modal('Choose option position',`<p>${tr('This changes display order only. Existing prices, stock and SKUs stay unchanged.')}</p>${field('Option',`<select id="order-value">${entries.map(x=>option(x.value,x.value,chosen)).join('')}</select>`)}${field('Place',`<select id="order-position">${option('before',t('Before'),'before')}${option('after',t('After'),'before')}</select>`)}${field('Other option',`<select id="order-anchor">${entries.filter(x=>x.value!==chosen).map(x=>option(x.value,x.value,'')).join('')}</select>`)}<p class="hint">${tr('For multiple products, both options must exist in each product’s preview. Products with missing options will be flagged before saving.')}</p><div id="edit-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button('Set position','confirm-order','')}</div>`);
}
document.addEventListener('click',event=>{
 const n=event.target.closest('[data-action]');if(!n||!['option-up','option-down','option-place','confirm-order'].includes(n.dataset.action))return;
 event.stopImmediatePropagation();clearError();
 try{
  if(n.dataset.action==='option-place'){showOrderDialog(n.dataset.group,n.dataset.value);return;}
  if(n.dataset.action==='confirm-order'){
   stageOptionMove(orderDialogGroup,$('#order-value').value,$('#order-anchor').value,$('#order-position').value);$('#dialog').close();render();return;
  }
  const group=groupById(n.dataset.group),entries=orderedOptionEntries(group).filter(x=>!x.removed),i=entries.findIndex(x=>x.value===n.dataset.value),j=i+(n.dataset.action==='option-up'?-1:1);
  if(j<0||j>=entries.length)return;
  stageOptionMove(group.id,entries[i].value,entries[j].value,n.dataset.action==='option-up'?'before':'after');render();
 }catch(error){if($('#edit-error')){$('#edit-error').textContent=error.message;$('#edit-error').classList.remove('hidden');}else fail(error);}
},true);
document.addEventListener('change',event=>{
 if(event.target.id!=='order-value')return;
 const entries=orderedOptionEntries(groupById(orderDialogGroup)).filter(x=>!x.removed&&x.value!==event.target.value);
 $('#order-anchor').innerHTML=entries.map(x=>option(x.value,x.value,'')).join('');
});
