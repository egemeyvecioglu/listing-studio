'use strict';

let dictionary = {}, locale = 'en', state, shopId = '', page = 'products', selected = new Set(), rules = [], variantFilters = {}, listingVariants = {}, filters = {search:'',section:'',status:'',category:''}, batchData = null, approved = false, busy = false, toastTimer;
const $ = s => document.querySelector(s);
const t = (key, args={}) => Object.entries(args).reduce((text,[k,v]) => text.replaceAll('{'+k+'}', String(v)), dictionary[key] ?? key);
const e = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tr = (key,args) => e(t(key,args));
const button = (text,action,cls='secondary',extra='') => `<button class="button ${cls}" data-action="${action}" ${extra}>${tr(text)}</button>`;
const option = (value,label,current) => `<option value="${e(value)}" ${String(current)===String(value)?'selected':''}>${e(label)}</option>`;
const shop = () => state?.shops.find(s => String(s.shop_id)===shopId);
const listings = () => (state?.listings ?? []).filter(l => String(l.shop_id)===shopId);
const products = listing => listing.snapshot.inventory.products;
const label = p => p.property_values.map(v=>v.values.join(', ')).join(' / ') || t('Standard option');
const formatMoney = value => new Intl.NumberFormat(locale,{style:'currency',currency:shop()?.currency || 'USD'}).format(value);
const statusLabel = value => t(({ready:'Ready to save',attention:'Needs attention',conflict:'Changed on Etsy',complete:'Saved',failed:'Not saved',uncertain:'Check result',unchanged:'No changes',interrupted:'Interrupted',needs_attention:'Needs attention',review:'Awaiting review',discarded:'Discarded',applying:'Saving'})[value]||value);
const date = timestamp => timestamp ? new Date(timestamp*1000).toLocaleString(locale,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : t('Not synced yet');
const names = {price:'Price',quantity:'Quantity',sku:'SKU',enabled:'Availability',listing:'Listing information',option:'Variation option'};

async function get(path){const response=await fetch(path);const data=await response.json();if(!response.ok)throw new Error(data.error || t('Something went wrong. Please try again.'));return data;}
async function post(path,data={}){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw new Error(value.code==='TASK_BUSY'?t('Currently running: {task}. Your review and edits are saved. Wait for this task to finish, then try again.',{task:t(value.task)}):value.error || t('Something went wrong. Please try again.'));return value;}
function notify(message){$('#toast').textContent=t(message);$('#toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),5000);}
function fail(error){$('#global-error').textContent=t(error.message || String(error));$('#global-error').classList.remove('hidden');}
function clearError(){$('#global-error').classList.add('hidden');}
function saveDraft(){if(shopId)localStorage.setItem('studio:draft:'+shopId,JSON.stringify({selected:[...selected],rules,variantFilters,listingVariants,filters}));}
function loadDraft(){let draft={};try{draft=JSON.parse(localStorage.getItem('studio:draft:'+shopId)||'{}')}catch{} selected=new Set(draft.selected||[]);rules=draft.rules||[];variantFilters=draft.variantFilters||{};listingVariants=draft.listingVariants||{};filters={search:'',section:'',status:'',category:'',...draft.filters};}
async function refresh(){state=await get('/api/state');if(!state.shops.some(s=>String(s.shop_id)===shopId)){shopId=String(state.shops.find(s=>!s.sample)?.shop_id || state.shops[0]?.shop_id || '');loadDraft();}render();}
function useShop(id){inventorySelection.clear();inventorySearch='';priceGroupIds=null;priceGroupSelection.clear();priceView='groups';editTab='options';saveDraft();shopId=id;localStorage.setItem('studio:shop',shopId);loadDraft();batchData=null;page='products';render();}
function nav(next){saveDraft();page=next;if(next!=='review')batchData=null;render();}
function matches(p, values){return Object.entries(values).every(([id,allowed])=>!allowed.length || p.property_values.some(v=>String(v.property_id)===id && v.values.some(x=>allowed.includes(x))));}
function visibleListings(){const needle=filters.search.trim().toLowerCase();return listings().filter(l=> (!needle || [l.title,...(l.tags||[]),...products(l).flatMap(p=>[p.sku,label(p)])].join(' ').toLowerCase().includes(needle)) && (!filters.section || String(l.shop_section_id||0)===filters.section) && (!filters.status||l.state===filters.status) && (!filters.category||String(l.taxonomy_id)===filters.category) && products(l).some(p=>matches(p,listingVariants)));}
function properties(rows=listings()){const groups={};for(const l of rows)for(const p of products(l))for(const v of p.property_values){const id=String(v.property_id);groups[id]??={id,name:v.property_name || t('Variation'),values:new Set()};v.values.forEach(x=>groups[id].values.add(x));}return Object.values(groups).map(g=>({...g,values:[...g.values].sort((a,b)=>a.localeCompare(b))}));}
function sectionName(id){return shop()?.sections?.find(s=>String(s.shop_section_id)===String(id))?.title || t('No section');}
function shirt(art=0){const fills=['#ebe5d9','#8b9f85','#c5957e','#a09aae'];return `<svg viewBox="0 0 100 100" aria-hidden="true"><path d="M32 16 17 24 5 42 23 51 28 43 28 88 73 88 73 43 78 51 96 42 83 24 68 16Q50 29 32 16Z" fill="${fills[art%4]}" stroke="#5b665836" stroke-width="1.4"/><path d="M40 19Q50 30 60 19" fill="none" stroke="#5b665850" stroke-width="2"/><text x="50" y="49" text-anchor="middle" fill="#354b3b" font-size="8" font-family="Georgia">SLOW</text><text x="50" y="59" text-anchor="middle" fill="#354b3b" font-size="7" font-family="Georgia">MORNINGS</text><path d="M39 65Q50 60 61 65" stroke="#536e4c" fill="none"/></svg>`;}
function thumbnail(l){const image=(l.images||l.Images||[])[0];let url=image?.url_170x135 || image?.url_75x75;try{if(url && !new URL(url).hostname.endsWith('.etsystatic.com'))url=null}catch{url=null}return `<div class="thumb art-${l.art||0}">${url?`<img src="${e(url)}" alt="" loading="lazy">`:shirt(l.art||0)}</div>`;}
function headings(title,subtitle,action=''){return `<div class="eyebrow">${tr('LISTING STUDIO')}</div><div class="heading-row"><div><h1>${tr(title)}</h1><p>${tr(subtitle)}</p></div>${action}</div>`;}
function sampleStrip(){return shop()?.sample?`<div class="sample-strip"><span>✳ &nbsp;${tr('Sample workspace. These products are fictional; changes never reach Etsy.')}</span><button class="text-button" data-action="connect">${tr('Connect your shop')}</button></div>`:'';}
function steps(active){return `<div class="steps">${['Choose products','Edit products','Review & apply'].map((s,i)=>`<div class="step ${i===active?'active':i<active?'done':''}"><b>${i<active?'✓':i+1}</b>${tr(s)}</div>`).join('<span>—</span>')}</div>`;}
function render(){
  if(!state)return;
  const focus=document.activeElement;const focusId=focus?.id;const position=focus?.selectionStart;
  const current=shop();
  $('#shop-select').innerHTML=state.shops.length?state.shops.map(s=>option(s.shop_id,s.name,shopId)).join(''):`<option>${tr('Choose your shop')}</option>`;
  $('#shop-select').disabled=galleryLoading||uploadBusy;
  $('#shop-avatar').textContent=current?.name.split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase() || 'LS';
  $('#shop-mode').textContent=current?(current.sample?t('Sample shop'):t('Connected')):t('Not connected');$('#shop-mode').className='badge '+(current?.sample?'sample':'connected');
  $('#nav-count').textContent=listings().length;
  document.querySelectorAll('[data-page]').forEach(n=>n.classList.toggle('active',n.dataset.page===page || n.dataset.page==='products' && ['edit','review'].includes(page)));
  updateStatus();
  if(!current)$('#main').innerHTML=welcome();
  else if(page==='products')$('#main').innerHTML=productsPage();
  else if(page==='edit')$('#main').innerHTML=editorPage();
  else if(page==='review')$('#main').innerHTML=reviewPage();
  else if(page==='presets')$('#main').innerHTML=presetsPage();
  else if(page==='activity')$('#main').innerHTML=activityPage();
  if(focusId){const next=document.getElementById(focusId);if(next){next.focus({preventScroll:true});if(position!==null&&next.setSelectionRange)try{next.setSelectionRange(position,position)}catch{}}}
  saveDraft();
}
function updateStatus(){const status=state.status;busy=status.busy;$('#sync-button').disabled=busy||!shop();$('#sync-button .sync-icon').classList.toggle('spinning',busy);$('#sync-status').textContent=busy?t(status.message):t('Last synced {time}',{time:date(shop()?.last_sync)});if(status.error)fail(new Error(status.error));let progress=$('#sync-progress');if(!progress){progress=document.createElement('div');progress.id='sync-progress';progress.className='notice sync-progress hidden';$('.topbar').after(progress);}progress.classList.toggle('hidden',!busy);progress.textContent=busy?t('Currently running: {task}. You can keep editing and prepare reviews. Saving to Etsy becomes available when this finishes.',{task:t(status.message)}):'';progress.setAttribute('role','status');}
function welcome(){return `<div class="welcome">${headings('Your shop. Less busywork.','A calm place to keep your products in order.')}<div class="welcome-card"><div class="welcome-copy"><div class="eyebrow">${tr('WELCOME TO LISTING STUDIO')}</div><h2>${tr('More creating. Less clicking.')}</h2><p>${tr('Update products and variations together, see exactly what will change, and send it to Etsy when you are ready.')}</p><div class="actions start">${button('Connect with Etsy','connect','')}${button('Explore a sample shop','sample')}</div><p class="hint">${tr('Your connection stays on this computer. Nothing changes without your approval.')}</p></div><div class="welcome-art"><div class="floating-card"><div class="art-shirt">${shirt()}</div><div class="mock-line"></div><div class="mock-line"></div></div><div class="floating-check">✓ ${tr('Ready for your review')}</div></div></div><div class="stats"><div class="stat"><span class="stat-icon">▦</span><div><strong>01</strong><small>${tr('Choose your products')}</small></div></div><div class="stat"><span class="stat-icon">↗</span><div><strong>02</strong><small>${tr('Make changes together')}</small></div></div><div class="stat"><span class="stat-icon">✓</span><div><strong>03</strong><small>${tr('Review, then apply')}</small></div></div></div></div>`;}
function oldProductsPage(){const rows=visibleListings(),all=listings(),selectedRows=all.filter(l=>selected.has(String(l.listing_id))),categories=[...new Map(all.map(l=>[String(l.taxonomy_id),l.category_name||t('Other products')])).entries()],variantCount=all.reduce((n,l)=>n+products(l).length,0),filterCount=Object.values(listingVariants).flat().length;return `${sampleStrip()}${headings('Products','Make a small change. Or a shop-wide one.',button('Connect another shop','connect','secondary'))}<div class="stats"><div class="stat"><span class="stat-icon">▦</span><div><strong>${all.length}</strong><small>${tr('Products in your shop')}</small></div></div><div class="stat"><span class="stat-icon">◈</span><div><strong>${variantCount}</strong><small>${tr('Variation combinations')}</small></div></div><div class="stat"><span class="stat-icon">✓</span><div><strong>${selectedRows.length}</strong><small>${tr('Selected for your next edit')}</small></div></div></div><section class="panel"><div class="panel-head"><div><h2>${tr('Your products')}</h2><p>${tr('Find exactly what you want to change.')}</p></div><button class="text-button" data-action="reset-filters">${tr('Clear filters')}</button></div><div class="toolbar"><div class="search-wrap"><span>⌕</span><input id="search" data-filter="search" placeholder="${tr('Search products, tags, options or SKUs…')}" aria-label="${tr('Search products')}" value="${e(filters.search)}"></div><select data-filter="section" aria-label="${tr('Shop section')}">${option('',t('All sections'),filters.section)}${(shop().sections||[]).map(s=>option(s.shop_section_id,s.title,filters.section)).join('')}${option('0',t('No section'),filters.section)}</select><select data-filter="status" aria-label="${tr('Listing status')}">${option('',t('All statuses'),filters.status)}${['active','draft','inactive','expired','sold_out'].map(s=>option(s,t(s.replace('_',' ')),filters.status)).join('')}</select><select data-filter="category" aria-label="${tr('Product category')}">${option('',t('All categories'),filters.category)}${categories.map(([id,name])=>option(id,name,filters.category)).join('')}</select>${button(filterCount?t('Variations ({count})',{count:filterCount}):'Variations','listing-variations','quiet small')}</div><div class="table-wrap"><table><thead><tr><th><input type="checkbox" id="select-visible" aria-label="${tr('Select all matching products')}" ${rows.length&&rows.every(l=>selected.has(String(l.listing_id)))?'checked':''}></th><th>${tr('Product')}</th><th>${tr('Section')}</th><th>${tr('Price range')}</th><th>${tr('Variations')}</th><th>${tr('Status')}</th><th></th></tr></thead><tbody>${rows.map(l=>{const prices=products(l).flatMap(p=>p.offerings.map(o=>o.price));const min=Math.min(...prices),max=Math.max(...prices);return `<tr><td><input type="checkbox" data-select="${l.listing_id}" aria-label="${tr('Select {title}',{title:l.title})}" ${selected.has(String(l.listing_id))?'checked':''}></td><td><div class="product-cell">${thumbnail(l)}<div><span class="product-title" title="${e(l.title)}">${e(l.title)}</span><span class="product-subtitle">${shop().sample?tr('Sample product'):e(l.listing_id)}</span></div></div></td><td>${e(sectionName(l.shop_section_id))}</td><td>${Number.isFinite(min)?e(formatMoney(min))+(max!==min?' – '+e(formatMoney(max)):''):'—'}</td><td>${products(l).length} <span class="product-subtitle">${tr('combinations')}</span></td><td><span class="status-pill ${e(l.state)}">${tr(l.state.replace('_',' '))}</span></td><td><button class="icon-button" data-action="inspect" data-id="${l.listing_id}" aria-label="${tr('View variations')}">↗</button></td></tr>`}).join('') || `<tr><td colspan="7"><div class="empty"><h2>${tr(all.length?'No products match yet.':'Ready for your first sync.')}</h2><p>${tr(all.length?'Try a different search or clear your filters.':'Sync your shop to load its products and variations.')}</p>${button(all.length?'Clear filters':'Sync now',all.length?'reset-filters':'sync','quiet')}</div></td></tr>`}</tbody></table></div><div class="table-footer"><span>${tr('{shown} of {total} products',{shown:rows.length,total:all.length})}</span><button class="text-button" data-action="select-matching">${tr('Select all matching products')}</button></div></section>${selectedRows.length?`<div class="selection-bar"><div><strong>${tr('{count} products selected',{count:selectedRows.length})}</strong><small>${e(shop().name)}</small><button class="text-button" data-action="clear-selection">${tr('Clear selection')}</button></div>${button('Edit selected products','edit','')}</div>`:`<p class="hint">${tr('Start by selecting one or more products. You will review every change before applying it.')}</p>`}`;}
function variationCheckboxes(values,kind,rows=listings()){const groups=properties(rows);return groups.length?`<div class="variation-grid">${groups.map(g=>`<div class="variation-group"><strong>${e(g.name)}</strong><div class="check-list">${g.values.map((v,i)=>`<label><input type="checkbox" data-variant-kind="${kind}" data-prop="${g.id}" value="${e(v)}" ${values[g.id]?.includes(v)?'checked':''}>${e(v)}</label>`).join('')}</div></div>`).join('')}</div>`:`<p class="hint">${tr('These products have no variation options.')}</p>`;}
function field(labelText,content,hint=''){content=content.replace(/ aria-label="[^"]*"/g,'').replace(/<(input|select|textarea)\b/, '<$1 aria-label="'+tr(labelText)+'"');return `<div class="field"><label>${tr(labelText)}</label>${content}${hint?`<small>${tr(hint)}</small>`:''}</div>`;}
function defaultRule(kind='price'){return {kind,mode:kind==='option'?'copy':'set',field:'title',value:kind==='enabled'?true:'',rounding:'0.01'};}
function reviewValue(value){if(value&&typeof value==='object'&&!Array.isArray(value))return Object.entries(value).map(([k,v])=>t(k)+': '+(Array.isArray(v)?v.join(', '):v)).join(' · ');if(Array.isArray(value))return value.join(', ');if(typeof value==='boolean')return t(value?'Available':'Unavailable');return String(value??'—');}
function oldPresetsPage(){const presets=state.presets.filter(p=>p.shop_id===shopId);return `${sampleStrip()}${headings('Your useful shortcuts.','Saved edits prepare a batch. You always choose the products and review the result.')}<div class="preset-grid">${presets.map(p=>`<section class="preset-card"><span class="eyebrow">${tr('SAVED EDIT')}</span><h2>${e(p.name)}</h2><p>${tr('{count} changes · {shop}',{count:p.rules.length,shop:shop().name})}</p>${button('Use shortcut','use-preset','quiet',`data-id="${p.id}"`)}</section>`).join('')||`<div class="panel empty"><h2>${tr('Make your next edit easier.')}</h2><p>${tr('Save a set of changes from the editing screen and it will appear here.')}</p>${button('Choose products','products','')}</div>`}</div>`;}
function activityPage(){const batches=state.batches.filter(b=>b.shop_id===shopId);return `${sampleStrip()}${headings('Every change has a story.','Your saved reviews, completed updates, and anything that needs a second look.')}<div>${batches.map(b=>`<article class="activity-card"><span class="stat-icon">${b.status==='complete'?'✓':'◷'}</span><div><h3>${e(b.label)}</h3><p>${e(date(b.created_at))} · ${e(b.shop_name)} · ${tr('{count} completed',{count:b.counts.complete||0})}</p></div><span class="status-pill ${b.status==='complete'?'':'draft'}">${e(statusLabel(b.status))}</span>${button('Open review','open-batch','quiet small',`data-id="${b.id}"`)}</article>`).join('')||`<div class="panel empty"><h2>${tr('A fresh start.')}</h2><p>${tr('Your previews and approved updates will appear here. Unfinished work is kept safely for your return.')}</p></div>`}</div>`;}

function modal(title,body){$('#dialog-content').innerHTML=`<div class="dialog-head"><h2>${tr(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="${tr('Close')}">×</button></div><div class="dialog-body">${body}</div>`;if(!$('#dialog').open)$('#dialog').showModal();}
function connectionModal(){modal('Connect your Etsy shop',`<p>${tr('Sign in on Etsy and choose the shop you want to connect. This app remembers each authorized shop separately.')}</p><div class="notice">${tr('A one-time Etsy app setup is required before the first connection. Your parents will not need these settings for everyday use.')}</div>${!state.live_enabled?`<div class="notice warning">${tr('This preview supports sample mode. Use the HTTPS launcher on Windows or macOS for a secure Etsy connection.')}</div>`:''}<details class="connection-details" open><summary>${tr('First-time Etsy setup')}</summary><div class="setup-fields"><p>${tr('Register an Etsy app with the access appropriate for your shops. A Seller App is restricted to its registered shop; connecting separately owned shops may require separate Seller Apps or approved Personal App access.')}</p><p><a href="https://www.etsy.com/developers/your-apps" target="_blank" rel="noopener noreferrer">${tr('Get your API key from Etsy')} ↗</a></p>${field('Registered callback address',`<div class="copy-row"><input id="callback-url" readonly value="${e(state.callback)}"><button class="button secondary small" data-action="copy-callback">${tr('Copy')}</button></div>`,'Register this exact HTTPS address in your Etsy app settings.')}${field('API key',`<input id="api-key" autocomplete="off" spellcheck="false" placeholder="${tr('Paste Keystring from Etsy')}">`)}${field('Shared secret',`<input id="api-secret" type="password" autocomplete="new-password" placeholder="${tr('Paste Shared secret from Etsy')}">`)}<p class="hint">${tr('Credentials are protected using Windows secure storage or macOS Keychain. They are never stored in your browser.')}</p></div></details><div id="connection-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button('Connect with Etsy','authorize','',state.live_enabled?'':'disabled')}</div>`);}
function settingsModal(){modal('Settings & connections',`<h3>${tr('Your connected shops')}</h3>${state.shops.map(s=>`<div class="connected-shop"><span class="avatar">${e(s.name.slice(0,2).toUpperCase())}</span><div><strong>${e(s.name)}</strong><small>${s.sample?tr('Sample workspace'):tr('Authorized Etsy connection')}</small></div><span class="badge">${s.sample?tr('Sample'):tr('Connected')}</span></div>`).join('')||`<p>${tr('No shops connected yet.')}</p>`}<div class="actions start">${button('Connect another shop','connect','quiet small')}</div><hr><h3>${tr('Automatic sync')}</h3><p>${tr('Sync runs while the local app is running, even when its browser tab is closed. Pending edits stay untouched.')}</p>${field('Sync interval',`<select id="sync-interval">${[5,15,30,60,180,1440].map(n=>option(n,t(n===1440?'Once a day':n===60?'Every hour':'Every {minutes} minutes',{minutes:n}),state.sync_minutes)).join('')}</select>`,'A larger shop may need a longer interval to stay within its Etsy request quota.')}${field('Interface language',`<select id="language"><option value="en">English</option></select>`,'English today. Translation files are ready for Turkish support.')}<div class="actions">${button('Close','close-modal')}${button('Save settings','save-settings','')}</div>`);}
async function openBatch(id){batchData=await get('/api/batch?id='+encodeURIComponent(id));shopId=batchData.batch.shop_id;page='review';approved=false;render();window.scrollTo({top:0,behavior:'smooth'});}

document.addEventListener('click',async event=>{const node=event.target.closest('[data-action],[data-page]');if(!node)return;if(node.dataset.page){nav(node.dataset.page);return;}const action=node.dataset.action;clearError();try{
  if(action==='connect'){connectionModal();return;}
  if(action==='close-modal'){$('#dialog').close();return;}
  if(action==='sample'){await post('/api/sample');await refresh();useShop('sample');return;}
  if(action==='authorize'){const target=node;target.disabled=true;try{const credentials=await post('/api/setup',{key:$('#api-key').value,secret:$('#api-secret').value});$('#api-secret').value='';const result=await post('/api/connect',credentials);window.location.assign(result.url);}catch(error){$('#connection-error').textContent=t(error.message);$('#connection-error').classList.remove('hidden');target.disabled=false;}return;}
  if(action==='copy-callback'){await navigator.clipboard.writeText(state.callback);notify('Callback address copied.');return;}
  if(action==='save-settings'){await post('/api/settings',{sync_minutes:Number($('#sync-interval').value)});$('#dialog').close();await refresh();notify('Settings saved.');return;}
  if(action==='sync'){await post('/api/sync',{shop_id:shopId});await refresh();return;}
  if(action==='reset-filters'){filters={search:'',section:'',status:'',category:''};listingVariants={};render();return;}
  if(action==='listing-variations'){modal('Filter products by variations',`<p>${tr('Show products containing at least one matching combination. These filters choose products; you will choose which combinations to edit in the next step.')}</p>${variationCheckboxes(listingVariants,'listing')}<div class="actions">${button('Done','close-modal','')}</div>`);return;}
  if(action==='select-matching'){visibleListings().forEach(l=>selected.add(String(l.listing_id)));render();return;}
  if(action==='clear-selection'){selected.clear();render();return;}
  if(action==='inspect'){inspect(node.dataset.id);return;}
  if(action==='products'){nav('products');return;}
  if(action==='edit'){if(batchData){rules=structuredClone(batchData.batch.rules);variantFilters=structuredClone(batchData.batch.filters);selected=new Set(batchData.batch.items.map(i=>String(i.listing_id)));}page='edit';batchData=null;render();window.scrollTo({top:0,behavior:'smooth'});return;}
  if(action==='clear-variations'){variantFilters={};render();return;}
  if(action==='preview'){node.disabled=true;try{batchData=await post('/api/preview',{shop_id:shopId,listing_ids:[...new Set([...selected,...rules.flatMap(r=>r.listing_ids||[])])],rules,filters:{}});approved=false;page='review';await refresh();window.scrollTo({top:0,behavior:'smooth'});}finally{node.disabled=false}return;}
  if(action==='apply'){if(!approved)return;node.disabled=true;await post('/api/apply',{id:batchData.batch.id,approval:batchData.approval,dependency_approvals:reviewDependencyApprovals(batchData.batch)});approved=false;await refresh();return;}
  if(action==='restore'){batchData=await post('/api/restore',{id:batchData.batch.id});approved=false;render();return;}
  if(action==='open-batch'){await openBatch(node.dataset.id);return;}
  if(action==='save-preset'){modal('Save shortcut',`${field('Name',`<input id="preset-name" placeholder="${tr('For example, winter prices')}">`)}<p>${tr('The changes and variation targets are saved. Product selection is made fresh each time.')}</p><div class="actions">${button('Cancel','close-modal')}${button('Save edits','confirm-preset','')}</div>`);return;}
  if(action==='confirm-preset'){await post('/api/preset',{shop_id:shopId,name:$('#preset-name').value,rules:rules.map(r=>{const copy={...r};delete copy.listing_ids;return copy}),filters:variantFilters});$('#dialog').close();await refresh();notify('Edits saved for next time.');return;}
  if(action==='use-preset'){const p=state.presets.find(p=>p.id===node.dataset.id);rules=structuredClone(p.rules).map(r=>{delete r.listing_ids;return r});variantFilters=structuredClone(p.filters);if(selected.size){page='edit';render()}else{page='products';render();notify('Choose your products, then open Choose changes. Your saved edits are ready.')}return;}
  if(action==='save-match'){modal('Save matching variations',`${field('Group name',`<input id="match-name" placeholder="${tr('For example, all T-shirt sizes')}">`)}<p>${tr('Only the checked values are included. Groups are kept separately for each shop.')}</p><div class="actions">${button('Cancel','close-modal')}${button('Save matches','confirm-match','')}</div>`);return;}
  if(action==='confirm-match'){await post('/api/match',{shop_id:shopId,name:$('#match-name').value,values:variantFilters});$('#dialog').close();await refresh();notify('Matching variations saved.');return;}
  if(action==='load-match'){const values=state.matches.filter(m=>m.shop_id===shopId);modal('Choose saved matches',values.map(m=>`<div class="connected-shop"><div>${e(m.name)}</div>${button('Use matches','use-match','quiet small',`data-id="${m.id}"`)}</div>`).join('')||`<p>${tr('No saved matches yet. Select the variations you consider equivalent, then save them as a group.')}</p>`);return;}
  if(action==='use-match'){variantFilters=structuredClone(state.matches.find(m=>m.id===node.dataset.id).values);$('#dialog').close();render();return;}
}catch(error){fail(error);notify(error.message);}});

document.addEventListener('change',event=>{const node=event.target;
  if(node.id==='shop-select'){useShop(node.value);return;}
  if(node.id==='select-visible'){visibleListings().forEach(l=>node.checked?selected.add(String(l.listing_id)):selected.delete(String(l.listing_id)));render();return;}
  if(node.dataset.select){node.checked?selected.add(node.dataset.select):selected.delete(node.dataset.select);render();return;}
  if(node.dataset.filter){filters[node.dataset.filter]=node.value;render();return;}
  if(node.dataset.variantKind){const target=node.dataset.variantKind==='listing'?listingVariants:variantFilters;const id=node.dataset.prop;const values=new Set(target[id]||[]);node.checked?values.add(node.value):values.delete(node.value);target[id]=[...values];render();return;}
  if(node.dataset.rule!==undefined){const i=Number(node.dataset.rule),key=node.dataset.key;if(key==='kind')rules[i]=defaultRule(node.value);else{rules[i][key]=key==='value'&&rules[i].kind==='enabled'?node.value==='true':node.value;if(key==='field'){rules[i].mode='set';rules[i].value='';}if(key==='property_id')rules[i].old='';}render();return;}
  if(node.id==='approve-batch'){approved=node.checked;render();}
});
document.addEventListener('input',event=>{const node=event.target;if(node.id==='search'){filters.search=node.value;render();}else if(node.dataset.rule!==undefined && node.tagName!=='SELECT'){rules[Number(node.dataset.rule)][node.dataset.key]=node.value;saveDraft();}});
$('#settings-button').addEventListener('click',settingsModal);
$('#sync-button').addEventListener('click',async()=>{try{clearError();await post('/api/sync',{shop_id:shopId});await refresh()}catch(error){fail(error)}});
window.addEventListener('beforeunload',saveDraft);
window.addEventListener('storage',event=>{if(event.key==='studio:connection')refresh().catch(fail)});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh().catch(fail)});

async function start(){try{dictionary=await get('/locales/'+locale+'.json');document.documentElement.lang=locale;document.querySelectorAll('[data-i18n]').forEach(n=>n.textContent=t(n.dataset.i18n));shopId=localStorage.getItem('studio:shop')||'';loadDraft();await refresh();setInterval(async()=>{try{const status=await get('/api/status');if(status.revision!==state.status.revision || status.busy!==busy || page==='review'&&busy){state.status=status;if(batchData){batchData=await get('/api/batch?id='+encodeURIComponent(batchData.batch.id));if(batchData.batch.status==='complete'&&JSON.stringify(batchData.batch.rules)===JSON.stringify(rules)){rules=[];saveDraft();}}await refresh();}else{state.status=status;updateStatus();}}catch{}},2000);}catch(error){$('#main').innerHTML=`<div class="empty"><h1>${tr('The studio could not open.')}</h1><p>${e(error.message)}</p><a class="button" href="/">${tr('Try again')}</a></div>`;}}


// Direct editing UI. The review engine remains the single Etsy write boundary.
const icons = {
  edit:'<svg viewBox="0 0 24 24"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15z"/></svg>',
  trash:'<svg viewBox="0 0 24 24"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>',
  plus:'<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>'
};
let editTab='options', optionDialog=null, inventorySearch='', inventorySelection=new Set(), pendingEditor=null;
const editRows=()=>listings().filter(l=>selected.has(String(l.listing_id)));
const actionIcon=(action,name,icon,attrs='')=>`<button class="icon-button ${icon==='trash'?'delete-icon':''}" data-action="${action}" aria-label="${tr(name)}" title="${tr(name)}" ${attrs}>${icons[icon]}</button>`;
const groupById=id=>properties(editRows()).find(g=>g.id===String(id));
function ruleSummary(r){
 if(r.kind==='option')return t(r.mode==='copy'?'Add {value}':r.mode==='rename'?'Rename {old} to {value}':'Remove {old}',r);
 if(r.kind==='listing')return t('Update {field}',{field:t(({shop_section_id:'shop section'})[r.field]||r.field)});
 const modes={set:'Set {kind} to {value}',add:'Increase {kind} by {value}',subtract:'Decrease {kind} by {value}',percent:'Change {kind} by {value}%'};
 return t(modes[r.mode]||'Update {kind}',{kind:t(names[r.kind]||r.kind),value:r.value});
}
function variationAddButtons(rows){return properties(rows).map(g=>`<button class="button secondary small" data-action="new-option" data-group="${g.id}">${icons.plus}${tr('Add {name}',{name:g.name})}</button>`).join('');}
function productsPage(){
 let html=oldProductsPage();
 if(selected.size)html=html.replace(button('Edit selected products','edit',''),variationAddButtons(editRows())+button('Edit selected products','edit',''));
 html=html.replace(/aria-label="View variations"/g,'aria-label="Edit product" title="Edit product"').replace(/>↗<\/button>/g,'>'+icons.edit+'</button>');
 html=html.replace(tr('Make a small change. Or a shop-wide one.'),tr('Select products to edit together, or use the pencil to edit one product.'));
 if(rules.length)html=`<div class="notice pending-banner"><span>${tr('{count} pending edits saved on this computer',{count:rules.length})}</span>${button('Continue editing','edit','small')}</div>`+html;
 return html;
}
function inspect(id){selected=new Set([String(id)]);page='edit';editTab='options';inventorySelection.clear();render();window.scrollTo({top:0,behavior:'smooth'});}
function optionRows(group){return group.values.map((value,index)=>{
 const containing=editRows().filter(l=>products(l).some(p=>p.property_values.some(v=>String(v.property_id)===group.id&&v.values.includes(value))));
 const count=containing.length;const pending=rules.findIndex(r=>r.kind==='option'&&String(r.property_id)===group.id&&r.old===value&&r.mode!=='copy');const edit=pending>=0?rules[pending]:null;
 return `<div class="option-row"><div><strong class="${edit?.mode==='remove'?'removed-value':''}">${e(edit?.mode==='rename'?edit.value:value)}</strong>${edit?'<span class="pending-tag">'+tr(edit.mode==='remove'?'Pending removal':'Edited')+'</span>':''}<small>${tr('{count} of {total} products',{count,total:editRows().length})}</small></div><div class="row-actions">${actionIcon('rename-option','Edit '+value,'edit',`data-group="${group.id}" data-value="${e(value)}"`)}${actionIcon('delete-option','Delete '+value,'trash',`data-group="${group.id}" data-value="${e(value)}"`)}</div></div>`;
}).join('')+rules.map((r,i)=>r.kind==='option'&&r.mode==='copy'&&String(r.property_id)===group.id?`<div class="option-row staged-option"><div><strong>${e(r.value)}</strong><span class="pending-tag">${tr('New')}</span><small>${e(formatMoney(r.price))} · ${tr('Stock {count}',{count:r.quantity})}</small></div><div class="row-actions">${actionIcon('edit-pending','Edit '+r.value,'edit',`data-index="${i}"`)}${actionIcon('remove-pending','Remove '+r.value,'trash',`data-index="${i}"`)}</div></div>`:'').join('');}
function editorPage(){
 if(editTab==='images')return imageEditorPage();
 const rows=editRows(),groups=properties(rows);
 return `${sampleStrip()}<div class="editor-heading"><div><button class="text-button back-button" data-action="products">← ${tr('All products')}</button><h1>${rows.length===1?e(rows[0].title):tr('Edit {count} products',{count:rows.length})}</h1><p>${tr('Edits stay here until you review and save them to Etsy.')}</p></div><div class="row-actions">${variationAddButtons(rows)}${button('Save shortcut','save-preset','secondary',rules.length?'':'disabled')}${button('Review changes ({count})','preview','',rules.length?'':'disabled').replace('{count}',String(rules.length))}</div></div><div class="editor-tabs" role="tablist" aria-label="${tr('Product editor')}">${[['options','Variations'],['inventory','Prices & stock'],['details','Product details'],['images','Images']].map(([id,name])=>`<button role="tab" aria-selected="${editTab===id}" class="${editTab===id?'active':''}" data-action="editor-tab" data-tab="${id}">${tr(name)}</button>`).join('')}</div><div class="editor-workspace"><div class="editing-content">${editTab==='options'?`<div class="section-intro"><h2>${tr('Variation options')}</h2><p>${tr('Add, rename, or remove an option directly from its list.')}</p></div><div class="options-columns">${groups.map(g=>`<section class="panel"><div class="panel-head"><h2>${e(g.name)}</h2><button class="button secondary small" data-action="new-option" data-group="${g.id}" aria-label="${tr('Add {name}',{name:g.name})}">${icons.plus}${tr('Add')}</button></div><div class="option-list">${optionRows(g)}</div><button class="add-option-row" data-action="new-option" data-group="${g.id}">${icons.plus}${tr('Add {name}',{name:g.name})}</button></section>`).join('')||`<div class="panel empty"><h2>${tr('No variations on these products')}</h2><p>${tr('Adding a new variation type is not supported in this version. Existing types can be managed here.')}</p></div>`}</div>`:editTab==='inventory'?inventoryTable(rows):detailEditor(rows)}</div><aside class="pending-panel panel"><div class="panel-head"><h2>${tr('Pending changes')}</h2><span class="chip">${rules.length}</span></div><div class="pending-list">${rules.map((r,i)=>`<div class="pending-row"><div><strong>${e(r._label||ruleSummary(r))}</strong><small>${tr('{count} products',{count:r.listing_ids?.length||rows.length})}</small></div><div class="row-actions">${actionIcon('edit-pending','Edit pending change','edit',`data-index="${i}"`)}${actionIcon('remove-pending','Remove pending change','trash',`data-index="${i}"`)}</div></div>`).join('')||`<div class="pending-empty"><span>✎</span><p>${tr('No changes yet. Edit a value or add a variation to get started.')}</p></div>`}</div>${rules.length?`<div class="panel-body">${button('Review changes','preview','')}<button class="text-button" data-action="discard-pending">${tr('Discard all edits')}</button></div>`:''}</aside></div>`;
}
function inventoryTable(rows){
 if(priceView==='groups')return groupedPriceTable(rows);
 return individualInventoryTable(rows);
}
function individualInventoryTable(rows){
 const entries=[];for(const l of rows)for(const [pi,p] of products(l).entries())if(!inventorySearch||[l.title,label(p),p.sku].join(' ').toLowerCase().includes(inventorySearch.toLowerCase()))entries.push({l,p,pi});
 return `<section class="panel"><div class="panel-head"><div><h2>${tr('Individual variation rows')}</h2><p>${tr('Edit a row with its pencil, or select rows to change them together.')}</p></div>${button('Grouped prices','price-group-view','secondary small')}</div><div class="toolbar"><div class="search-wrap"><span>⌕</span><input id="inventory-search" placeholder="${tr('Find a style, size, color or product…')}" aria-label="${tr('Search variations')}" value="${e(inventorySearch)}"></div>${button('Change selected rows','bulk-inventory','',inventorySelection.size?'':'disabled')}<span class="hint">${tr('{count} selected',{count:inventorySelection.size})}</span></div><div class="table-wrap"><table><thead><tr><th><input type="checkbox" id="inventory-all" aria-label="${tr('Select visible variations')}" ${entries.length&&entries.every(({l,pi})=>inventorySelection.has(l.listing_id+':'+pi))?'checked':''}></th>${rows.length>1?`<th>${tr('Product')}</th>`:''}<th>${tr('Variation')}</th><th>${tr('Price')}</th><th>${tr('Stock')}</th><th>${tr('SKU')}</th><th>${tr('Available')}</th><th></th></tr></thead><tbody>${entries.map(({l,p,pi})=>{const o=p.offerings[0];return `<tr><td><input type="checkbox" data-inventory-select="${l.listing_id}:${pi}" aria-label="${e(t('Select {name}',{name:l.title+' '+label(p)}))}" ${inventorySelection.has(l.listing_id+':'+pi)?'checked':''}></td>${rows.length>1?`<td class="inventory-title">${e(l.title)}</td>`:''}<td>${e(label(p))}</td><td>${e(formatMoney(o.price))}</td><td>${o.quantity}</td><td>${e(p.sku)||'—'}</td><td>${tr(o.is_enabled?'Yes':'No')}</td><td>${actionIcon('edit-inventory','Edit '+label(p),'edit',`data-listing="${l.listing_id}" data-product="${pi}"`)}</td></tr>`}).join('')||`<tr><td colspan="8" class="empty">${tr('No matching variations. Try a different search.')}</td></tr>`}</tbody></table></div><div class="table-footer">${tr('{count} combinations',{count:entries.length})}</div></section>`;
}
function detailEditor(rows){const fields={title:'Title',description:'Description',tags:'Tags',materials:'Materials',shop_section_id:'Shop section'};return `<section class="panel"><div class="panel-head"><div><h2>${tr('Product details')}</h2><p>${tr('Use the pencil beside the detail you want to change.')}</p></div></div>${Object.entries(fields).map(([key,name])=>{const values=[...new Set(rows.map(l=>key==='shop_section_id'?sectionName(l.snapshot.fields[key]):reviewValue(l.snapshot.fields[key])))];return `<div class="detail-row"><div><strong>${tr(name)}</strong><p>${values.length===1?e(values[0]):tr('Different values across selected products')}</p></div>${actionIcon('edit-detail','Edit '+name,'edit',`data-field="${key}"`)}</div>`}).join('')}</section>`;}
function showOptionDialog(groupId,mode,value='',index=null){
 const group=groupById(groupId);if(!group)return;
 const rule=index!==null?rules[index]:null;
 const old=rule?.old??value;
 optionDialog={groupId,mode,old,index};
 const other=properties(editRows()).filter(g=>g.id!==groupId);
 const matchCount=mode==='copy'?editRows().length:editRows().filter(l=>products(l).some(p=>p.property_values.some(v=>String(v.property_id)===groupId&&v.values.includes(old)))).length;
 let body=`<p>${tr('For {count} selected products in {shop}',{count:matchCount,shop:shop().name})}</p>`;
 if(mode==='remove')body+=`<div class="notice warning">${tr('Remove “{name}” and its matching combinations? Existing orders are not changed.',{name:old})}</div>`;
 else body+=field('Name',`<input id="option-name" required value="${e(rule?.value??(mode==='rename'?old:''))}" placeholder="${tr('Enter the new option name')}" maxlength="70">`);
 if(mode==='copy')body+=`<div class="option-settings"><h3>${tr('Price & stock')}</h3><div class="rule-fields">${field('Price',`<input id="option-price" type="number" step="0.01" min="0.01" required value="${e(rule?.price??'')}">`)}${field('Stock',`<input id="option-quantity" type="number" step="1" min="0" required value="${e(rule?.quantity??'20')}">`)}</div>${other.map(g=>`<fieldset class="option-colors"><legend>${e(g.name)}</legend>${g.values.map(v=>`<label><input type="checkbox" data-option-filter="${g.id}" value="${e(v)}" ${!rule?.filters?.[g.id]||rule.filters[g.id].includes(v)?'checked':''}>${e(v)}</label>`).join('')}</fieldset>`).join('')}<details class="advanced"><summary>${tr('More settings')}</summary>${field('SKU (optional)',`<input id="option-sku" value="${e(rule?.sku||'')}" maxlength="32">`)}<p class="hint">${tr('Leave SKU empty to inherit the existing SKU for each matching combination.')}</p><p class="hint">${tr('An existing option supplies the remaining Etsy settings. Prices and stock use the values above; existing options stay unchanged.')}</p>${field('Use settings from',`<select id="option-source">${group.values.map(v=>option(v,v,rule?.old||group.values[0])).join('')}</select>`)}</details></div>`;
 body+=`<div id="edit-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button(mode==='remove'?'Remove option':mode==='rename'?'Save edit':'Add option','confirm-option',mode==='remove'?'destructive':'')}</div><p class="hint">${tr('This only adds a pending edit. Nothing is sent to Etsy yet.')}</p>`;
 modal((mode==='copy'?'Add ':mode==='rename'?'Edit ':'Remove ')+group.name,body);
 $('#option-name')?.focus();
}
function targetsForOption(rule){return editRows().filter(l=>products(l).some(p=>p.property_values.some(v=>String(v.property_id)===String(rule.property_id)&&v.values.includes(rule.old)))).map(l=>String(l.listing_id));}
function stageRule(rule,index=null){
 const key=r=>JSON.stringify([r.kind,r.field,r.property_id,r.mode,r.mode==='copy'?r.value:r.old,r.filters,r.listing_ids]);
 if(index!==null)rules[index]=rule;
 else{const existing=rules.findIndex(r=>key(r)===key(rule));if(existing>=0)rules[existing]=rule;else rules.push(rule);}
 saveDraft();$('#dialog').close();render();notify('Edit added. Review your changes before saving to Etsy.');
}
function showInventoryDialog(entries,index=null){
 pendingEditor={entries,index};
 const single=entries.length===1,one=single?structuredClone(entries[0].p.offerings[0]):null;
 const existing=index!==null?rules[index]:null;
 if(single&&existing){entries=structuredClone(entries);pendingEditor.entries=entries;if(existing.kind==='sku')entries[0].p.sku=existing.value;else one[existing.kind==='enabled'?'is_enabled':existing.kind]=existing.value;}
 modal(single?'Edit variation':'Edit selected variations',`<p>${single?e(label(entries[0].p)):tr('{count} selected variation rows',{count:entries.length})}</p>${single?`<div class="rule-fields">${field('Price',`<input id="row-price" type="number" step="0.01" min="0.01" value="${e(one.price)}">`)}${field('Stock',`<input id="row-stock" type="number" min="0" step="1" value="${one.quantity}">`)}${field('SKU',`<input id="row-sku" maxlength="32" value="${e(entries[0].p.sku)}">`)}${field('Available',`<select id="row-enabled">${option('true',t('Yes'),String(one.is_enabled))}${option('false',t('No'),String(one.is_enabled))}</select>`)}</div>`:`${field('What do you want to change?',`<select id="bulk-field">${Object.entries({price:'Price',quantity:'Stock',sku:'SKU',enabled:'Availability'}).map(([id,name])=>option(id,t(name),existing?.kind||'price')).join('')}</select>`)}${field('How?',`<select id="bulk-mode">${[['set','Set to'],['add','Increase by'],['subtract','Decrease by'],['percent','Change by percentage']].map(([id,name])=>option(id,t(name),existing?.mode||'set')).join('')}</select>`)}${field('Value',`<input id="bulk-value" value="${e(existing?.value??'')}" required>`)}<p class="hint" id="bulk-help">${tr('Use a negative percentage to reduce prices.')}</p>`}<div id="edit-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button('Save edit','confirm-inventory','')}</div><p class="hint">${tr('Nothing is sent to Etsy until you review and save.')}</p>`);
}
function showDetailDialog(key,index=null){
 const rows=editRows(),same=rows.every(l=>JSON.stringify(l.snapshot.fields[key])===JSON.stringify(rows[0].snapshot.fields[key]));
 const previous=index!==null?rules[index]:null;
 pendingEditor={field:key,index};
 const value=previous?.value??(same?rows[0].snapshot.fields[key]:'');
 const input=key==='shop_section_id'?`<select id="detail-value">${option(0,t('No section'),value)}${shop().sections.map(s=>option(s.shop_section_id,s.title,value)).join('')}</select>`:key==='description'?`<textarea id="detail-value" rows="7">${e(value)}</textarea>`:`<input id="detail-value" value="${e(Array.isArray(value)?value.join(', '):value)}" ${key==='title'?'maxlength="140"':''}>`;
 modal('Edit '+(({shop_section_id:'shop section'})[key]||key),`<p>${tr('For {count} selected products',{count:rows.length})}</p>${!same?`<div class="notice warning">${tr('These products currently have different values. Your change applies to all selected products.')}</div>`:''}${key!=='shop_section_id'?field('Change',`<select id="detail-mode">${(['tags','materials'].includes(key)?[['set','Replace all'],['add','Add'],['remove','Remove']]:[['set','Replace with'],['prefix','Add to beginning'],['suffix','Add to end'],['replace','Find and replace']]).map(([id,name])=>option(id,t(name),previous?.mode||'set')).join('')}</select>`):''}<div id="detail-find-wrap" class="${previous?.mode==='replace'?'':'hidden'}">${field('Find',`<input id="detail-find" value="${e(previous?.find||'')}">`)}</div>${field(['tags','materials'].includes(key)?'Values, separated by commas':'New value',input)}<div id="edit-error" class="notice danger hidden" role="alert"></div><div class="actions">${button('Cancel','close-modal')}${button('Save edit','confirm-detail','')}</div>`);
}

// Capture direct actions before the legacy navigation/review handlers.
 document.addEventListener('click',async event=>{
 const node=event.target.closest('[data-action]');if(!node)return;const a=node.dataset.action;
 const handled=['editor-tab','new-option','rename-option','delete-option','confirm-option','edit-inventory','bulk-inventory','confirm-inventory','edit-detail','confirm-detail','remove-pending','edit-pending','discard-pending'];
 if(!handled.includes(a))return;
 event.stopImmediatePropagation();clearError();
 try{
 if(a==='editor-tab'){editTab=node.dataset.tab;render();if(editTab==='images')loadImageGalleries();}
 if(a==='new-option'){page='edit';editTab='options';render();showOptionDialog(node.dataset.group,'copy');}
 if(a==='rename-option'){const i=rules.findIndex(r=>r.kind==='option'&&String(r.property_id)===node.dataset.group&&r.old===node.dataset.value&&r.mode==='rename');showOptionDialog(node.dataset.group,'rename',node.dataset.value,i>=0?i:null);}
 if(a==='delete-option'){const i=rules.findIndex(r=>r.kind==='option'&&String(r.property_id)===node.dataset.group&&r.old===node.dataset.value&&r.mode!=='copy');showOptionDialog(node.dataset.group,'remove',node.dataset.value,i>=0?i:null);}
 if(a==='confirm-option'){
  const {groupId,mode,old,index}=optionDialog;
  const rule={kind:'option',mode,property_id:groupId,old:mode==='copy'?$('#option-source').value:old,filters:{}};
  if(mode!=='remove'){rule.value=$('#option-name').value.trim();if(!rule.value)throw Error(t('Enter an option name.'));}
  if(mode==='copy'){
   rule.auto_source=true;
   const price=Number($('#option-price').value),quantity=Number($('#option-quantity').value);
   if(!$('#option-price').value||!Number.isFinite(price)||price<=0)throw Error(t('Enter a price greater than zero.'));
   if(!$('#option-quantity').value||!Number.isInteger(quantity)||quantity<0)throw Error(t('Enter a whole stock quantity of zero or more.'));
   rule.price=price;rule.quantity=quantity;rule.sku=$('#option-sku').value;
   for(const g of properties(editRows()).filter(g=>g.id!==groupId)){
    const chosen=[...document.querySelectorAll('[data-option-filter="'+g.id+'"]:checked')].map(n=>n.value);
    if(chosen.length!==g.values.length)rule.filters[g.id]=chosen;
    if(!chosen.length)throw Error(t('Choose at least one value for {name}.',{name:g.name}));
   }
  }
  rule.listing_ids=mode==='copy'?editRows().map(l=>String(l.listing_id)):targetsForOption(rule);if(!rule.listing_ids.length)throw Error(t('No selected product has this option.'));
  rule._label=ruleSummary(rule);stageRule(rule,index);
 }
 if(a==='edit-inventory'){const l=editRows().find(l=>String(l.listing_id)===node.dataset.listing);showInventoryDialog([{l,p:products(l)[Number(node.dataset.product)]}]);}
 if(a==='bulk-inventory'){
  const entries=[];for(const l of editRows())products(l).forEach((p,pi)=>{if(inventorySelection.has(l.listing_id+':'+pi))entries.push({l,p})});
  if(!entries.length)throw Error(t('Select at least one variation row.'));showInventoryDialog(entries);updateBulkInputs();
 }
 if(a==='confirm-inventory'){
  const {entries,index}=pendingEditor;const additions=[];
  for(const {l,p} of entries){
   const target={listing_ids:[String(l.listing_id)],filters:Object.fromEntries(p.property_values.map(v=>[String(v.property_id),v.values]))};
   if(entries.length===1){
    const o=p.offerings[0],price=Number($('#row-price').value),quantity=Number($('#row-stock').value);
    if(!$('#row-price').value||!Number.isFinite(price)||price<=0)throw Error(t('Enter a price greater than zero.'));
    if(!$('#row-stock').value||!Number.isInteger(quantity)||quantity<0)throw Error(t('Enter a whole stock quantity of zero or more.'));
    for(const [kind,value,old] of [['price',price,o.price],['quantity',quantity,o.quantity],['sku',$('#row-sku').value,p.sku],['enabled',$('#row-enabled').value==='true',o.is_enabled]])if(value!==old)additions.push({...target,kind,mode:'set',value,_label:label(p)+' · '+t(names[kind])});
   }else{
    const kind=$('#bulk-field').value,mode=$('#bulk-mode').value,text=$('#bulk-value').value.trim();let value=text;
    if(kind==='price'||kind==='quantity'){value=Number(text);if(!text||!Number.isFinite(value))throw Error(t('Enter a valid number.'));if(kind==='quantity'&&(mode==='percent'||!Number.isInteger(value)))throw Error(t('Stock must be a whole number.'));}
    if(kind==='enabled'){if(!['yes','no'].includes(text.toLowerCase()))throw Error(t('Enter yes or no.'));value=text.toLowerCase()==='yes';}
    additions.push({...target,kind,mode:kind==='enabled'?'set':mode,value,_label:label(p)+' · '+t(names[kind])});
   }
  }
  const grouped=new Map();for(const r of additions){const copy={...r};delete copy.listing_ids;const key=JSON.stringify(copy);if(grouped.has(key))grouped.get(key).listing_ids.push(...r.listing_ids);else grouped.set(key,r);}additions.splice(0,additions.length,...grouped.values());
  if(index!==null)rules.splice(index,1);
  for(const r of additions){const existing=rules.findIndex(x=>x.kind===r.kind&&JSON.stringify(x.filters)===JSON.stringify(r.filters)&&JSON.stringify(x.listing_ids)===JSON.stringify(r.listing_ids));if(existing>=0)rules[existing]=r;else rules.push(r);}
  $('#dialog').close();render();notify(additions.length?'Changes added for review.':'No values changed.');
 }
 if(a==='edit-detail'){const i=rules.findIndex(r=>r.kind==='listing'&&r.field===node.dataset.field);showDetailDialog(node.dataset.field,i>=0?i:null);}
 if(a==='confirm-detail'){const {field,index}=pendingEditor;const r={kind:'listing',field,mode:$('#detail-mode')?.value||'set',value:$('#detail-value').value,find:$('#detail-find').value,listing_ids:editRows().map(l=>String(l.listing_id)),filters:{}};if(['title','description'].includes(field)&&r.mode==='set'&&!r.value.trim())throw Error(t('This field cannot be empty.'));if(r.mode==='replace'&&!r.find)throw Error(t('Enter the text to find.'));r._label=ruleSummary(r);stageRule(r,index);}
 if(a==='remove-pending'){rules.splice(Number(node.dataset.index),1);render();}
 if(a==='edit-pending'){
  const index=Number(node.dataset.index),r=rules[index];
  if(r.kind==='option')showOptionDialog(String(r.property_id),r.mode,r.old,index);
  else if(r.kind==='listing')showDetailDialog(r.field,index);
  else if(r.kind==='price'&&r._price_group)showGroupedPriceDialog([{filters:r.filters,listing_ids:r.listing_ids,name:r._label}],r);
  else{const entries=[];for(const l of editRows())if(!r.listing_ids||r.listing_ids.includes(String(l.listing_id)))for(const p of products(l))if(matches(p,r.filters||{}))entries.push({l,p});showInventoryDialog(entries,index);updateBulkInputs();}
 }
 if(a==='discard-pending'){modal('Discard pending edits?',`<p>${tr('This removes your unsaved edits. Your Etsy products stay unchanged.')}</p><div class="actions">${button('Keep editing','close-modal')}${button('Discard edits','confirm-discard','destructive')}</div>`);}
 }catch(error){if($('#edit-error')){$('#edit-error').textContent=error.message;$('#edit-error').classList.remove('hidden')}else fail(error)}
 },true);
 document.addEventListener('click',event=>{if(event.target.closest('[data-action="confirm-discard"]')){rules=[];$('#dialog').close();render();}},true);
 document.addEventListener('change',event=>{
 const n=event.target;
 if(n.dataset.inventorySelect){event.stopImmediatePropagation();n.checked?inventorySelection.add(n.dataset.inventorySelect):inventorySelection.delete(n.dataset.inventorySelect);render();}
 if(n.id==='inventory-all'){event.stopImmediatePropagation();for(const l of editRows())products(l).forEach((p,pi)=>{if(!inventorySearch||[l.title,label(p),p.sku].join(' ').toLowerCase().includes(inventorySearch.toLowerCase()))n.checked?inventorySelection.add(l.listing_id+':'+pi):inventorySelection.delete(l.listing_id+':'+pi)});render();}
 if(n.id==='bulk-field')updateBulkInputs();
 if(n.id==='detail-mode')$('#detail-find-wrap').classList.toggle('hidden',n.value!=='replace');
 },true);
 document.addEventListener('input',event=>{if(event.target.id==='inventory-search'){inventorySearch=event.target.value;render();}});
start();

function updateBulkInputs(){
 const field=$('#bulk-field');if(!field)return;
 const kind=field.value,mode=$('#bulk-mode');
 const old=$('#bulk-value'),value=old.value;
 if(kind==='enabled'){
  old.outerHTML='<select id="bulk-value" aria-label="'+tr('Availability')+'">'+option('yes',t('Available'),value)+option('no',t('Unavailable'),value)+'</select>';
  mode.parentElement.classList.add('hidden');$('#bulk-help').textContent='';
 }else{
  mode.parentElement.classList.remove('hidden');
  mode.innerHTML=(kind==='sku'?[['set','Replace with'],['prefix','Add to beginning'],['suffix','Add to end']]:[['set','Set to'],['add','Increase by'],['subtract','Decrease by'],...(kind==='price'?[['percent','Change by percentage']]:[])]).map(([id,name])=>option(id,t(name),mode.value)).join('');
  old.outerHTML='<input id="bulk-value" aria-label="'+tr('Value')+'" type="'+(kind==='sku'?'text':'number')+'" step="'+(kind==='quantity'?'1':'any')+'" required value="'+e(value)+'">';
  $('#bulk-help').textContent=kind==='price'?t('Use a negative percentage to reduce prices.'):'';
 }
}

function presetsPage(){
 const values=state.presets.filter(p=>p.shop_id===shopId);
 return headings('Saved shortcuts','Choose products, reuse your edits, then review before saving.')+'<div class="preset-grid">'+(values.map(p=>`<section class="preset-card"><div class="panel-head"><h2>${e(p.name)}</h2><div class="row-actions">${actionIcon('rename-preset','Rename shortcut','edit',`data-id="${p.id}"`)}${actionIcon('delete-preset','Delete shortcut','trash',`data-id="${p.id}"`)}</div></div><ul class="shortcut-summary">${p.rules.map(r=>`<li>${e(r._label||ruleSummary(r))}</li>`).join('')}</ul>${button('Use shortcut','use-preset','quiet',`data-id="${p.id}"`)}</section>`).join('')||`<div class="panel empty"><h2>${tr('No shortcuts yet')}</h2><p>${tr('Edit a product, then choose Save shortcut to reuse those changes next time.')}</p>${button('Open products','products','')}</div>` )+'</div>';
}
let shortcutId='';
document.addEventListener('click',async event=>{
 const n=event.target.closest('[data-action]');if(!n)return;const action=n.dataset.action;
 if(!['rename-preset','delete-preset','confirm-rename-preset','confirm-delete-preset'].includes(action))return;
 event.stopImmediatePropagation();
 try{
 if(action==='rename-preset'){shortcutId=n.dataset.id;const p=state.presets.find(p=>p.id===shortcutId);modal('Rename shortcut',field('Name',`<input id="shortcut-name" value="${e(p.name)}">`)+`<div class="actions">${button('Cancel','close-modal')}${button('Save name','confirm-rename-preset','')}</div>`);}
 if(action==='delete-preset'){shortcutId=n.dataset.id;const p=state.presets.find(p=>p.id===shortcutId);modal('Delete shortcut?',`<p>${tr('Delete “{name}”? Your products and previous changes will not be affected.',{name:p.name})}</p><div class="actions">${button('Cancel','close-modal')}${button('Delete shortcut','confirm-delete-preset','destructive')}</div>`);}
 if(action==='confirm-rename-preset'){const p=state.presets.find(p=>p.id===shortcutId);await post('/api/preset',{...p,name:$('#shortcut-name').value});$('#dialog').close();await refresh();}
 if(action==='confirm-delete-preset'){await post('/api/delete-preset',{id:shortcutId});$('#dialog').close();await refresh();}
 }catch(error){fail(error)}
},true);
