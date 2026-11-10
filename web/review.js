'use strict';
let dependencyConsent=new Set();
const dependencyFields={price_on_property:'Price',quantity_on_property:'Stock',sku_on_property:'SKU',readiness_state_on_property:'Processing profile'};
function reviewDependencies(item){
 if(!item.before?.inventory||!item.after?.inventory)return [];
 return Object.entries(dependencyFields).flatMap(([key,name])=>{
  const before=item.before.inventory[key]||[],after=item.after.inventory[key]||[];
  if(JSON.stringify(before)===JSON.stringify(after))return [];
  const names=new Map([...item.before.inventory.products,...item.after.inventory.products].flatMap(p=>p.property_values.map(v=>[String(v.property_id),v.property_name||t('Variation')])));
  const describe=ids=>ids.length?ids.map(id=>names.get(String(id))||t('Variation')).join(' + '):t('Shared across all variations');
  const change=item.changes.find(c=>c.dependency===key);
  return [{key,name,before:describe(before),after:describe(after),reason:change?.reason||t('The requested values cannot be represented with the current variation settings.')}];
 });
}
function dependencyConsentKey(batch,item){return JSON.stringify([batch.id,item.listing_id,reviewDependencies(item)]);}
function reviewDependencyApprovals(batch){return batch.items.filter(i=>dependencyConsent.has(dependencyConsentKey(batch,i))).map(i=>i.listing_id);}
function reviewAdditions(item){
 const groups=new Map();
 for(const c of item.changes.filter(c=>c.kind==='added')){
  if(!groups.has(c.option))groups.set(c.option,{name:c.option,count:0,colors:new Set()});
  const g=groups.get(c.option);g.count++;(c.colors||[]).forEach(v=>g.colors.add(v));
 }
 return groups.size?`<div class="addition-summary"><h3>${tr('New options being added')}</h3>${[...groups.values()].map(g=>`<p><span class="status-pill">${tr('Added')}</span> <strong>${e(g.name)}</strong> · ${tr('{count} new combinations',{count:g.count})}${g.colors.size?' · '+tr('{count} colors',{count:g.colors.size}):''}</p>`).join('')}<p class="hint">${tr('These are additional combinations. The template option is not being replaced.')}</p></div>`:'';
}
function reviewOptionOrder(item){
 const added=new Set(item.changes.filter(c=>c.kind==='added').map(c=>c.option));
 return item.changes.filter(c=>c.kind==='order').map(c=>`<section class="option-order-review"><h3>${tr('{name} order',{name:c.property_name||t('Variation')})}</h3><p>${tr('Options will appear in this order. Prices, stock and SKUs are not changed by moving them.')}</p><div class="order-preview-columns">${[['Before',c.before],['After',c.after]].map(([label,values])=>`<div><h4>${tr(label)}</h4><ol tabindex="0" aria-label="${tr('{label} option order',{label:t(label)})}">${values.map(value=>`<li>${e(value)} ${label==='After'&&added.has(value)?`<span class="pending-tag">${tr('Added')}</span>`:''}</li>`).join('')}</ol></div>`).join('')}</div></section>`).join('');
}
function dependencyReview(batch,item){
 const dependencies=reviewDependencies(item);if(!dependencies.length)return '';
 const eligible=['ready','failed','uncertain','interrupted'].includes(item.status);
 return `<section class="dependency-review"><h3>${tr('Variation settings need a separate decision')}</h3><p>${tr('Existing variation values stay unchanged. These settings control which variations share stock, SKU, price or processing values.')}</p>${dependencies.map(d=>`<div class="dependency-row"><strong>${tr(d.name)}</strong><p>${tr('Before')}: ${e(d.before)}<br>${tr('After')}: ${e(d.after)}</p><p>${e(t(d.reason))}</p><p class="hint">${tr('Effect: values can differ by {dimensions}; combinations sharing those dimensions still share this setting.',{dimensions:d.after})}</p></div>`).join('')}${eligible?`<label><input type="checkbox" data-dependency-consent="${item.listing_id}" ${dependencyConsent.has(dependencyConsentKey(batch,item))?'checked':''}><span>${tr('Allow only the setting changes shown above for this product.')}</span></label><div class="actions">${button('Keep current settings and edit additions','edit','secondary small')}</div><p class="hint">${tr('Keeping the current settings requires changing the conflicting values or removing the affected edit. Saving stays blocked until you choose.')}</p>`:''}</section>`;
}
function saveTimingReview(batch){
 const progress=state.status.progress?.batch_id===batch.id?state.status.progress:null,timing=progress?null:batch.timing;
 if(!timing&&!progress)return '';
 return `<section class="panel save-timing"><div class="panel-head"><div><h2>${tr(progress?'Saving your products':'Save timings')}</h2>${progress?`<p id="active-save-step">${tr('{done} of {total} products saved',{done:progress.completed,total:progress.total})} · ${e(progress.product)} · ${e(t(progress.step))}</p>`:''}</div></div>${timing?`<div class="panel-body"><div class="timing-grid">${[['Elapsed time',timing.elapsed],['Etsy reads',timing.etsy_read],['Etsy writes',timing.etsy_write],['Waiting for Etsy request allowance',timing.rate_wait],['Local saves',timing.local_save]].map(([label,value])=>`<div><strong>${tr('{seconds}s',{seconds:Number(value||0).toFixed(2)})}</strong><span>${tr(label)}</span></div>`).join('')}</div><p class="hint">${tr('Timings describe the latest save attempt. Elapsed time also includes local comparisons and processing. Measurements are recorded on this computer, not estimates of Etsy server performance.')}</p><details><summary>${tr('Show steps for each product')}</summary>${batch.items.filter(i=>i.timings?.length).map(i=>`<h3>${e(i.title)}</h3><table><thead><tr><th>${tr('Step')}</th><th>${tr('Elapsed')}</th><th>${tr('Etsy response')}</th><th>${tr('Request allowance wait')}</th></tr></thead><tbody>${i.timings.map(step=>`<tr><td>${e(t(step.step))}${step.failed?' · '+tr('Not confirmed'):''}</td><td>${Number(step.seconds).toFixed(2)}s</td><td>${Number(step.http_seconds).toFixed(2)}s</td><td>${Number(step.wait_seconds).toFixed(2)}s</td></tr>`).join('')}</tbody></table>`).join('')}</details></div>`:''}</section>`;
}
function reviewPage(){
 if(!batchData)return '';
 if(batchData.batch.type==='images')return imageReviewPage();
 const b=batchData.batch,counts={};b.items.forEach(i=>counts[i.status]=(counts[i.status]||0)+1);
 const eligible=b.items.filter(i=>['ready','failed','uncertain','interrupted'].includes(i.status));
 const ready=eligible.length,issues=(counts.attention||0)+(counts.conflict||0),removing=b.rules.some(r=>r.kind==='option'&&r.mode==='remove')&&!b.restore_of;
 const needsConsent=eligible.some(i=>reviewDependencies(i).length&&!dependencyConsent.has(dependencyConsentKey(b,i)));
 return `${sampleStrip()}${steps(2)}${headings('Review your changes','Check the changes below. Only the ready products will be updated.')}<div class="notice">${tr('This batch belongs to {shop}.',{shop:b.shop_name})} ${b.sample?tr('Sample changes stay in this workspace.'):tr('Approved updates to active products are visible to buyers on Etsy.')}</div><div class="review-top ${busy&&state.status.progress?.batch_id===b.id?'hidden':''}"><div class="review-count"><strong>${ready}</strong><span>${tr('Ready to save')}</span></div><div class="review-count"><strong>${issues}</strong><span>${tr('Needs attention')}</span></div><div class="review-count"><strong>${counts.complete||0}</strong><span>${tr('Completed')}</span></div><div class="review-count"><strong>${counts.unchanged||0}</strong><span>${tr('No changes needed')}</span></div></div>${saveTimingReview(b)}${b.items.map(item=>{
 const ordinary=item.changes.filter(c=>!['dependency','order'].includes(c.kind)&&!Object.values(dependencyFields).some(name=>c.label===(name==='Stock'?'Quantity':name==='Processing profile'?'Readiness State Id':name)+' varies by'));
 return `<details class="review-item" ${item.status==='attention'||item.status==='conflict'||reviewDependencies(item).length?'open':''}><summary><span>${e(item.title)}</span><span class="status-pill ${['attention','conflict','uncertain','failed'].includes(item.status)?'draft':''}">${e(statusLabel(item.status))}</span><span>${ordinary.length?tr('{count} combination/detail changes',{count:ordinary.length}):''}${item.changes.some(c=>c.kind==='order')?' · '+tr('Order updated'):''}</span></summary>${item.error?`<div class="panel-body"><div class="notice danger">${e(t(item.error))}</div></div>`:''}${reviewAdditions(item)}${reviewOptionOrder(item)}${dependencyReview(b,item)}${ordinary.length?`<details class="review-combinations"><summary>${tr('View individual changes ({count})',{count:ordinary.length})}</summary><div class="table-wrap"><table><thead><tr><th>${tr('What changes')}</th><th>${tr('Before')}</th><th>${tr('After')}</th></tr></thead><tbody>${ordinary.map(c=>`<tr><td>${c.kind==='added'?`<span class="status-pill">${tr('Added')}</span> `:c.kind==='removed'?`<span class="status-pill draft">${tr('Removed')}</span> `:''}${e(t(c.label))}</td><td class="before-value">${c.kind==='added'?tr('Not present'):e(reviewValue(c.before))}</td><td class="after-value">${c.kind==='removed'?tr('Not present'):e(reviewValue(c.after))}</td></tr>`).join('')}</tbody></table></div></details>`:''}</details>`;
 }).join('')}${ready&&b.status!=='discarded'?`<div class="approval">${busy&&state.status.progress?.batch_id!==b.id?`<div class="notice" role="status">${tr('Currently running: {task}. Your review is saved. Save changes when this task finishes.',{task:t(state.status.message)})}</div>`:''}${needsConsent?`<div class="notice warning">${tr('Review the separate variation-setting decisions above before saving.')}</div>`:''}<label><input type="checkbox" id="approve-batch" ${approved?'checked':''}><span>${tr('I have reviewed these changes and approve updating the ready products in {shop}.',{shop:b.shop_name})}${removing?`<br><strong>${tr('I also approve removing the listed variation combinations.')}</strong>`:''}</span></label><div class="actions">${button('Keep editing','edit')}${button(b.sample?'Apply to sample shop':'Save changes to Etsy','apply',removing?'destructive':'',approved&&!busy&&!needsConsent?'':'disabled')}</div></div>`:`<div class="actions">${button('Back to products','products')}${counts.complete?button('Undo these changes…','restore','secondary',busy?'disabled':''):''}</div>`}<p class="hint">${tr('If a product changed on Etsy after this preview, its update will stop for a fresh review. Completed updates are never repeated.')}</p>`;
}
document.addEventListener('change',event=>{
 const id=event.target.dataset.dependencyConsent;if(!id||!batchData)return;
 const item=batchData.batch.items.find(i=>i.listing_id===id);if(!item)return;
 const key=dependencyConsentKey(batchData.batch,item);
 event.target.checked?dependencyConsent.add(key):dependencyConsent.delete(key);
 approved=false;render();
});
