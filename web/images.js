'use strict';
// Image operations are staged separately from inventory edits and share the
// existing explicit review/approval boundary. Uploaded bytes stay on this PC.
let galleries = {}, galleryPage = 0, galleryLoading = false, galleryError = '', galleryShop = '', uploadBusy = false;
let imageDraft = {uploads:[],position:'last',removed:{},targets:[]}, imageDraftShop='', removeImageEnd='first', removeImageCount=1;
function imageDraftLoad(){
 if(imageDraftShop===shopId)return;
 imageDraftShop=shopId;galleries={};galleryShop=shopId;galleryPage=0;galleryError='';
 try{imageDraft=JSON.parse(localStorage.getItem('studio:images:'+shopId)||'null')||{uploads:[],position:'last',removed:{},targets:[]};}catch{imageDraft={uploads:[],position:'last',removed:{},targets:[]};}
}
function imageDraftSave(){localStorage.setItem('studio:images:'+imageDraftShop,JSON.stringify(imageDraft));}
function imageEditCount(){return imageDraft.uploads.length+Object.values(imageDraft.removed).reduce((n,v)=>n+v.length,0);}
function imageRows(){return editRows().slice(galleryPage*12,galleryPage*12+12);}
function orderedImages(g){return [...(g?.images||[])].sort((a,b)=>a.rank-b.rank);}
function imageUrl(image){
 const raw=image.url||image.url_170x135||image.url_fullxfull;
 if(!raw)return '/sample-image.svg';
 try{const u=new URL(raw,location.origin);if(u.origin===location.origin&&['/sample-image.svg','/api/image-file'].includes(u.pathname))return u.href;if(u.protocol==='https:'&&u.hostname.endsWith('.etsystatic.com'))return u.href;}catch{}
 return '/sample-image.svg';
}
function resultingGallery(g){
 const old=orderedImages(g).filter(i=>!(imageDraft.removed[g.listing_id]||[]).includes(String(i.listing_image_id)));
 const uploads=imageDraft.targets.includes(g.listing_id)?imageDraft.uploads.map(i=>({...i,isNew:true})):[];
 return imageDraft.position==='first'?[...uploads,...old]:[...old,...uploads];
}
function galleryThumb(image,index,settings={}){
 const id=String(image.listing_image_id||image.id||image.key),removed=settings.removed;
 return `<div class="gallery-tile ${removed?'image-removed':''} ${image.isNew?'image-new':''}"><div class="gallery-photo"><img src="${e(imageUrl(image))}" alt="${tr('Image {number}',{number:index+1})}" loading="lazy">${settings.button||''}</div><div class="gallery-caption"><span>${index===0&&!settings.numberOnly?tr('Cover'):index+1}</span>${removed?`<strong>${tr('Remove')}</strong>`:image.isNew?`<strong>${tr('New')}</strong>`:''}${settings.linked?`<span title="${tr('Linked to a variation')}">🔗</span>`:''}</div></div>`;
}
async function loadImageGalleries(force=false){
 imageDraftLoad();if(galleryLoading)return;
 const expectedShop=shopId,ids=imageRows().map(l=>String(l.listing_id)).filter(id=>force||!galleries[id]);
 if(!ids.length)return;
 galleryLoading=true;galleryError='';render();
 try{const result=await post('/api/image-gallery',{shop_id:expectedShop,listing_ids:ids});if(expectedShop===shopId)for(const g of result.galleries)galleries[g.listing_id]=g;}
 catch(error){galleryError=error.message;}
 finally{galleryLoading=false;if(page==='edit'&&editTab==='images')render();}
}
function imageEditorPage(){
 imageDraftLoad();const rows=imageRows(),count=editRows().length,affected=new Set([...Object.keys(imageDraft.removed).filter(k=>imageDraft.removed[k].length),...(imageDraft.uploads.length?imageDraft.targets:[])]).size;
 return `${sampleStrip()}<div class="editor-heading"><div><button class="text-button back-button" data-action="products">← ${tr('All products')}</button><h1>${count===1?e(editRows()[0].title):tr('Edit {count} products',{count})}</h1><p>${tr('Add or remove images here. Review before saving to Etsy.')}</p></div>${button('Review image changes ({count})','image-review','',imageEditCount()&&!uploadBusy&&!galleryLoading&&!busy?'':'disabled').replace('{count}',String(affected))}</div><div class="editor-tabs" role="tablist" aria-label="${tr('Product editor')}">${[['options','Variations'],['inventory','Prices & stock'],['details','Product details'],['images','Images']].map(([id,name])=>`<button role="tab" aria-selected="${id==='images'}" class="${id==='images'?'active':''}" data-action="editor-tab" data-tab="${id}">${tr(name)}</button>`).join('')}</div>${rules.length?`<div class="notice">${tr('Your product and variation edits are kept separately. This review saves image changes only.')}</div>`:''}<section class="panel"><div class="panel-head"><div><h2>${tr('Images')}</h2><p>${tr('The first image is your cover. New files are added to all currently selected products.')}</p></div><button class="button" data-action="image-pick" ${uploadBusy||busy?'disabled':''}>${icons.plus}${tr('Add images')}</button><input id="image-picker" type="file" accept="image/jpeg,image/png" multiple hidden></div><div class="toolbar image-tools"><div class="image-remove-controls"><span>${tr('Remove')}</span><select id="remove-image-end" aria-label="${tr('Remove from')}">${option('first',t('First'),removeImageEnd)}${option('last',t('Last'),removeImageEnd)}</select><input id="remove-image-count" type="number" min="1" max="20" step="1" value="${e(removeImageCount)}" aria-label="${tr('Number of images')}"><span>${tr('images')}</span>${button('Select for removal','image-remove-n','secondary',galleryLoading||busy?'disabled':'')}</div><div class="row-actions">${button('Refresh galleries','image-refresh','quiet small',galleryLoading||busy?'disabled':'')}${button('Discard image edits','image-reset','quiet small',imageEditCount()?'':'disabled')}</div></div>${uploadBusy?`<div class="panel-body"><div class="notice" role="status">${tr('Preparing your images locally…')}</div></div>`:''}${imageDraft.uploads.length?`<div class="upload-tray"><div class="upload-tray-head"><h3>${tr('New images · {count} products',{count:imageDraft.targets.length})}</h3><label>${tr('Add at')} <select id="image-position" aria-label="${tr('New image position')}">${option('first',t('Beginning'),imageDraft.position)}${option('last',t('End'),imageDraft.position)}</select></label></div><div class="gallery-strip">${imageDraft.uploads.map((im,i)=>galleryThumb({...im,isNew:true},i,{numberOnly:true,button:actionIcon('image-drop-upload',t('Remove {name}',{name:im.name}),'trash',`data-index="${i}"`)})).join('')}<button class="upload-more" data-action="image-pick" aria-label="${tr('Add more images')}">${icons.plus}</button></div><p class="hint">${tr('Files use the order shown. Use the arrows below each filename to change it. JPG or PNG, up to 20 MB each.')}</p><div class="upload-file-list">${imageDraft.uploads.map((im,i)=>`<div><span>${e(im.name)}</span><button class="icon-button" data-action="image-upload-up" data-index="${i}" aria-label="${tr('Move {name} earlier',{name:im.name})}" ${i===0?'disabled':''}>←</button><button class="icon-button" data-action="image-upload-down" data-index="${i}" aria-label="${tr('Move {name} later',{name:im.name})}" ${i===imageDraft.uploads.length-1?'disabled':''}>→</button></div>`).join('')}</div></div>`:''}</section>${galleryError?`<div class="notice danger" role="alert">${e(galleryError)} ${button('Try again','image-refresh','secondary small')}</div>`:''}${galleryLoading?`<div class="notice" role="status">${tr('Loading current galleries from Etsy…')}</div>`:''}${rows.map(l=>{
 const g=galleries[String(l.listing_id)];if(!g)return `<section class="panel"><div class="panel-body"><strong>${e(l.title)}</strong><p class="hint">${tr('Gallery not loaded yet.')}</p>${button('Load galleries','image-refresh','quiet small',galleryLoading?'disabled':'')}</div></section>`;
 const old=orderedImages(g),removed=imageDraft.removed[g.listing_id]||[],after=resultingGallery(g);
 return `<section class="panel gallery-card"><div class="panel-head"><div><h2>${e(g.title)}</h2><p>${tr('{before} images → {after} after your edits',{before:old.length,after:after.length})}</p></div>${removed.length?button('Undo removals','image-undo-removals','quiet small',`data-id="${g.listing_id}"`):''}</div><div class="panel-body"><div class="gallery-strip">${old.map((im,i)=>galleryThumb(im,i,{removed:removed.includes(String(im.listing_image_id)),linked:g.linked_ids.includes(String(im.listing_image_id)),button:removed.includes(String(im.listing_image_id))?`<button class="icon-button gallery-remove" data-action="image-toggle-remove" data-listing="${g.listing_id}" data-image="${im.listing_image_id}" aria-label="${tr('Keep image {number}',{number:i+1})}">↶</button>`:actionIcon('image-toggle-remove',t('Remove image {number}',{number:i+1}),'trash',`data-listing="${g.listing_id}" data-image="${im.listing_image_id}"`)})).join('')||`<p class="hint">${tr('No images yet. Add your first image above.')}</p>`}</div>${removed.length||imageDraft.uploads.length&&imageDraft.targets.includes(g.listing_id)?`<h3 class="gallery-after-title">${tr('After saving')}</h3><div class="gallery-strip">${after.map((im,i)=>galleryThumb(im,i)).join('')}</div>${after.length>20||!after.length?`<div class="notice danger">${tr('Keep between 1 and 20 images before saving.')}</div>`:''}`:''}</div></section>`;
 }).join('')}<div class="gallery-pagination"><span>${tr('Products {start}–{end} of {total}',{start:count?galleryPage*12+1:0,end:Math.min((galleryPage+1)*12,count),total:count})}</span><div class="row-actions">${button('Previous','image-prev','quiet small',galleryPage>0?'':'disabled')}${button('Next','image-next','quiet small',(galleryPage+1)*12<count?'':'disabled')}</div></div><p class="hint">${tr('Nothing changes on Etsy until you approve the image review. The removal count always refers to the current Etsy gallery, not newly added files.')}</p>`;
}
function imageReviewPage(){
 imageDraftLoad();const b=batchData.batch;const ready=b.items.filter(i=>['ready','failed'].includes(i.status)),saved=b.items.filter(i=>i.status==='complete'),attention=b.items.filter(i=>['attention','conflict','uncertain'].includes(i.status));
 if(b.status==='complete'&&imageDraft.reviewId===b.id){
  imageDraftLoad();for(const item of b.items){delete imageDraft.removed[item.listing_id];delete galleries[item.listing_id];}
  imageDraft.uploads=[];imageDraft.targets=[];delete imageDraft.reviewId;imageDraftSave();
 }
 return `${sampleStrip()}${headings('Review image changes','Check the cover and image order for each product before saving.')}<div class="notice">${tr('Shop: {shop}',{shop:b.shop_name})} ${b.sample?tr('Sample edits never change Etsy.'):tr('Approved changes update the live Etsy gallery.')}</div><div class="review-top"><div class="review-count"><strong>${ready.length}</strong><span>${tr('Ready to save')}</span></div><div class="review-count"><strong>${attention.length}</strong><span>${tr('Needs attention')}</span></div><div class="review-count"><strong>${saved.length}</strong><span>${tr('Saved')}</span></div></div>${b.items.map(item=>`<section class="panel gallery-card"><div class="panel-head"><h2>${e(item.title)}</h2><span class="status-pill ${item.error?'draft':''}">${e(statusLabel(item.status))}</span></div><div class="panel-body">${item.error?`<div class="notice danger">${e(t(item.error))}</div>`:''}<h3>${tr('Before')}</h3><div class="gallery-strip">${item.before_images.map((im,i)=>galleryThumb(im,i)).join('')}</div>${item.after_images?.length?`<h3 class="gallery-after-title">${tr('After saving')}</h3><div class="gallery-strip">${item.after_images.map((im,i)=>galleryThumb({...im,isNew:im.key?.startsWith('upload:')},i)).join('')}</div>`:''}</div></section>`).join('')}${ready.length&&b.status!=='discarded'?`<div class="approval"><label><input type="checkbox" id="approve-batch" ${approved?'checked':''}><span>${tr('I approve the additions, removals and cover image shown for {shop}.',{shop:b.shop_name})}</span></label><div class="actions">${button('Keep editing images','image-back')}${button(b.sample?'Apply to sample shop':'Save images to Etsy','apply','',approved&&!busy?'':'disabled')}</div></div>`:`<div class="actions">${button('Back to images','image-back')}${button('All products','products','secondary')}</div>`}<p class="hint">${tr('If an upload result is uncertain, automatic re-upload is blocked. Inspect the refreshed gallery before preparing another review. Keep original files for images you may want to restore.')}</p>`;
}
async function uploadImageFiles(files){
 imageDraftLoad();const targetShop=shopId;const targets=editRows().map(l=>String(l.listing_id));
 if(imageDraft.uploads.length&&JSON.stringify([...imageDraft.targets].sort())!==JSON.stringify([...targets].sort()))throw Error(t('Your existing uploads target a different product selection. Review or discard them before choosing more files.'));
 if(files.length+imageDraft.uploads.length>20)throw Error(t('Choose up to 20 new images.'));
 delete imageDraft.reviewId;uploadBusy=true;render();
 try{
  for(const file of files){
   if(!['image/jpeg','image/png'].includes(file.type)||file.size>20*1024*1024)throw Error(t('Choose JPG or PNG images smaller than 20 MB.'));
   const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error(t('This file could not be read.')));reader.readAsDataURL(file)});
   const asset=await post('/api/image-upload',{shop_id:targetShop,name:file.name,content});
   if(shopId!==targetShop)throw Error(t('Shop changed. Return to the original shop before adding more images.'));
   imageDraft.uploads.push(asset);imageDraft.targets=targets;imageDraftSave();
  }
 }finally{uploadBusy=false;render();}
}
document.addEventListener('change',async event=>{
 const n=event.target;
 if(n.id==='image-picker')try{await uploadImageFiles([...n.files]);}catch(error){fail(error)}
 if(n.id==='remove-image-end')removeImageEnd=n.value;
 if(n.id==='remove-image-count')removeImageCount=n.value;
 if(n.id==='image-position'){delete imageDraft.reviewId;imageDraft.position=n.value;imageDraftSave();render();}
});
document.addEventListener('click',async event=>{
 const n=event.target.closest('[data-action]');if(!n)return;const a=n.dataset.action;
 if(!['image-pick','image-refresh','image-next','image-prev','image-toggle-remove','image-undo-removals','image-drop-upload','image-upload-up','image-upload-down','image-remove-n','image-reset','image-review','image-back'].includes(a))return;
 event.stopImmediatePropagation();clearError();imageDraftLoad();
 try{
  if(a==='image-pick'){$('#image-picker').click();return;}
  if(a==='image-refresh'){await loadImageGalleries(true);return;}
  if(a==='image-next'||a==='image-prev'){galleryPage+=a==='image-next'?1:-1;render();await loadImageGalleries();return;}
  if(a==='image-back'){page='edit';editTab='images';batchData=null;approved=false;render();await loadImageGalleries(true);return;}
  if(['image-toggle-remove','image-undo-removals','image-drop-upload','image-upload-up','image-upload-down','image-reset','image-remove-n'].includes(a))delete imageDraft.reviewId;
  if(a==='image-toggle-remove'){
   const g=galleries[n.dataset.listing],id=n.dataset.image;
   const removed=new Set(imageDraft.removed[g.listing_id]||[]);
   if(removed.has(id))removed.delete(id);else{if(g.linked_ids.includes(id))throw Error(t('This image is linked to a variation. Unlink it on Etsy first, then refresh the gallery.'));removed.add(id);}
   imageDraft.removed[g.listing_id]=[...removed];
  }
  if(a==='image-undo-removals')delete imageDraft.removed[n.dataset.id];
  if(a==='image-drop-upload')imageDraft.uploads.splice(Number(n.dataset.index),1);
  if(a==='image-upload-up'||a==='image-upload-down'){
   const i=Number(n.dataset.index),j=i+(a==='image-upload-up'?-1:1);if(j>=0&&j<imageDraft.uploads.length)[imageDraft.uploads[i],imageDraft.uploads[j]]=[imageDraft.uploads[j],imageDraft.uploads[i]];
  }
  if(a==='image-reset'){imageDraft={uploads:[],position:'last',removed:{},targets:[]};}
  if(a==='image-remove-n'){
   const count=Number($('#remove-image-count').value),end=$('#remove-image-end').value;
   if(!Number.isInteger(count)||count<1||count>20)throw Error(t('Choose a whole number from 1 to 20.'));
   // Read and validate every selected gallery before staging any removal.
   const targets=editRows().map(l=>String(l.listing_id));
   galleryLoading=true;render();
   try{for(let i=0;i<targets.length;i+=12){const ids=targets.slice(i,i+12);if(ids.every(id=>galleries[id]))continue;const r=await post('/api/image-gallery',{shop_id:shopId,listing_ids:ids});for(const g of r.galleries)galleries[g.listing_id]=g;}}finally{galleryLoading=false;}
   const changes=[];
   for(const id of targets){const g=galleries[id],images=orderedImages(g);if(count>images.length)throw Error(t('“{title}” has only {count} images. Choose a smaller number.',{title:g.title,count:images.length}));const removed=(end==='first'?images.slice(0,count):images.slice(-count)).map(im=>String(im.listing_image_id));if(removed.some(i=>g.linked_ids.includes(i)))throw Error(t('The selection includes a variation-linked image in “{title}”. Unlink it on Etsy first.',{title:g.title}));changes.push([id,removed]);}
   for(const [id,removed] of changes)imageDraft.removed[id]=[...new Set([...(imageDraft.removed[id]||[]),...removed])];
  }
  if(a==='image-review'){
   const ids=[...new Set([...Object.keys(imageDraft.removed).filter(k=>imageDraft.removed[k].length),...(imageDraft.uploads.length?imageDraft.targets:[])])];
   if(!ids.length)throw Error(t('Add or remove an image first.'));
   galleryLoading=true;render();
   try{
    for(let i=0;i<ids.length;i+=12){const missing=ids.slice(i,i+12).filter(id=>!galleries[id]);if(missing.length){const r=await post('/api/image-gallery',{shop_id:shopId,listing_ids:missing});for(const g of r.galleries)galleries[g.listing_id]=g;}}
    batchData=await post('/api/image-review',{shop_id:shopId,edits:ids.map(id=>({listing_id:id,removed:imageDraft.removed[id]||[],uploads:imageDraft.targets.includes(id)?imageDraft.uploads.map(a=>a.id):[],position:imageDraft.position}))});imageDraft.reviewId=batchData.batch.id;approved=false;page='review';
   }finally{galleryLoading=false;}
  }
  imageDraftSave();render();
 }catch(error){galleryLoading=false;render();fail(error)}
},true);
