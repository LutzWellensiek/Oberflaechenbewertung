"use strict";
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateNames={exif_original:'EXIF Aufnahme',exif_digitized:'EXIF Digitalisierung',exif_datetime:'EXIF Änderung',modified:'Datei geändert',created:'Datei erstellt',import:'Importdatum',manual:'Manuell'};
const statusNames={pending:'Offen',rated:'Bewertet',ignored:'Zurückgestellt',removed:'Entfernt'};
const taskNames={quality:'Qualität',region:'Ausschnitt',relevance:'Eignung'};
let state,rows=[],libraryRows=[],current=null,picture=null,crop=null,score=null;
let currentTab='workspace',saving=false,loading=false,drawing=false,keyboardCrop=false,zoomed=false;
let loadVersion=0,draftTimer,toastTimer,cropDrag=null,libraryLimit=100,foregroundJobs=0;
let seed=sessionStorage.getItem('surface-shuffle')||crypto.randomUUID();
sessionStorage.setItem('surface-shuffle',seed);
const selected=new Set();
const scoreText=v=>v==null?'—':v===0?'0 · nicht bewertbar':String(v);
const guarded=fn=>async(...args)=>{try{return await fn(...args);}catch(e){toast(e.message,true);reportError(e);}};
function toast(message,error=false){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').className='toast'+(error?' error':'');$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?14000:5500);}
function reportError(e){if(state?.token&&!e.reported)fetch('/api/client-error',{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state.token},body:JSON.stringify({message:e.message||String(e),details:e.stack||''}),keepalive:true}).catch(()=>{});}
async function api(path,body,renew=true){
 const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state?.token||''},body:JSON.stringify(body)}),value=await response.json();
 if(!response.ok){if(renew&&body!==undefined&&response.status===403&&value.error?.startsWith('Sitzung ungültig')){state.token=(await api('/api/state')).token;return api(path,body,false);}const e=Error(value.error||'Anfrage fehlgeschlagen');e.reported=!!value.error_id;throw e;}return value;
}
async function followJob(id){foregroundJobs++;try{while(true){const job=await api('/api/jobs/'+id);$('jobBar').hidden=false;$('jobTitle').textContent=job.title;$('jobMessage').textContent=job.message;if(job.status==='error')throw Error(job.message);if(job.status==='done')return job.result;await new Promise(resolve=>setTimeout(resolve,700));}}finally{foregroundJobs--;if(!foregroundJobs)$('jobBar').hidden=true;}}
function options(select,entries,placeholder){const previous=select.value;select.innerHTML=(placeholder!==undefined?`<option value="">${esc(placeholder)}</option>`:'')+entries.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('');if([...select.options].some(o=>o.value===previous))select.value=previous;}
async function refreshState(){state=await api('/api/state');renderState();}
function renderState(){
 const c=state.counts;$('ratedCount').textContent=c.rated;$('pendingCount').textContent=c.pending;$('progress').max=Math.max(1,c.total-(c.removed||0));$('progress').value=c.rated+c.ignored;$('progressText').textContent=`${c.rated} bewertet · ${c.ignored} zurückgestellt · ${c.removed||0} entfernt`;
 if(document.activeElement!==$('criteria'))$('criteria').value=state.criteria;if(document.activeElement!==$('outputFolder'))$('outputFolder').value=state.output;
 options($('filterFolder'),state.folders.map(f=>[f,f]),'Alle Ordner');options($('filterRun'),state.runs.map(r=>[r.id,`${r.name}${r.stale?' · veraltet':''}`]),'Kein Modellvergleich');options($('evaluationRun'),state.runs.map(r=>[r.id,`${r.name} · ${taskNames[r.task]}${r.stale?' · veraltet':''}`]),'Trainingsstand wählen');options($('baseModel'),state.models.map(m=>[m.id,`${m.name}${m.installed?'':' · nicht installiert'}`]));renderRuns();renderModelCards();updateSelection();updateControls();
}
async function tab(name){const previousTab=currentTab;if(currentTab==='workspace'&&name!=='workspace')await flushDraft();currentTab=name;document.querySelectorAll('.tab').forEach(e=>e.hidden=e.id!==name);document.querySelectorAll('.nav').forEach(e=>{e.classList.toggle('active',e.dataset.tab===name);e.setAttribute('aria-current',e.dataset.tab===name?'page':'false');});$('pageTitle').textContent={workspace:'Bilder bewerten',library:'Deine Bildübersicht',models:'Modelle & Ergebnisse',settings:'Einstellungen'}[name];if(name==='workspace'&&previousTab!=='workspace')await refreshRows(current?.id);if(name==='library')await refreshLibrary();if(name==='models'){await refreshState();await renderEvaluation();}}
function workQuery(){return new URLSearchParams({status:$('statusFilter').value,order:$('workOrder').value,seed});}
async function refreshRows(preferId){rows=await api('/api/images?'+workQuery());const target=rows.find(r=>r.id===preferId)||rows[0];renderQueue();if(target)await loadImage(target.id);else clearImage();}
function renderQueue(){
 $('queueCount').textContent=`${rows.length} Bilder`;const pos=rows.findIndex(r=>r.id===current?.id);$('positionText').textContent=current?`${pos+1} / ${rows.length}`:'';const start=Math.max(0,pos-12);
 $('imageQueue').innerHTML=rows.slice(start,start+36).map(r=>`<button class="queue-item${r.id===current?.id?' active':''}" data-image="${r.id}" aria-label="${esc(r.relative_name)} · ${esc(scoreText(r.score))}" aria-current="${r.id===current?.id}"><img loading="lazy" src="/api/photo?id=${r.id}&size=100" alt=""><span>${esc(r.relative_name.split(/[\\/]/).pop())}</span></button>`).join('');$('previous').disabled=saving||loading||pos<=0;$('next').disabled=saving||loading||pos<0||pos>=rows.length-1;
}
function clearImage(){loadVersion++;current=null;picture=null;crop=null;score=null;loading=false;drawing=false;cropDrag=null;$('empty').hidden=false;$('imageWork').hidden=true;$('imageName').textContent='Noch kein Bild';$('imageMeta').textContent='';$('imageStatus').textContent='';for(const id of ['captureDate','alternatives'])$(id).value='';for(const id of ['dateWarning','allDates','imageHistory','imageModelResults'])$(id).textContent='';$('dateSource').innerHTML='';renderScore();updateControls();renderQueue();}
async function loadImage(id){
 if(saving)return;const version=++loadVersion;await flushDraft();if(version!==loadVersion)return;loading=true;updateControls();
 try{const row=await api('/api/image?id='+id);if(version!==loadVersion)return;const image=new Image();image.src='/api/photo?id='+id+'&size=3000';await image.decode();if(version!==loadVersion)return;current=row;picture=image;keyboardCrop=false;drawing=false;cropDrag=null;const d=row.status==='pending'?(row.draft||{}):{};crop=d.crop||(row.label?JSON.parse(row.label.crop):[0,0,row.width,row.height]);score=d.score??row.label?.score??null;$('alternatives').value=(d.alternatives||JSON.parse(row.label?.alternatives||'[]')).join(', ');$('trainingEnabled').checked=d.training_enabled??(row.label?.training_enabled!==0);$('fitSelect').value=row.relevance==null?'':String(row.relevance);$('captureDate').value=d.date||row.chosen_date;options($('dateSource'),[...Object.entries(row.dates).filter(([k])=>dateNames[k]).map(([k])=>[k,dateNames[k]]),['manual','Manuell']]);$('dateSource').value=d.date_source||row.date_source;$('dateWarning').textContent=row.warning||'';$('allDates').textContent=JSON.stringify(row.dates,null,2);$('imageName').textContent=row.relative_name.split(/[\\/]/).pop();$('imageMeta').textContent=`${row.origin_folder} · ${row.width} × ${row.height} px`;$('imageStatus').textContent=statusNames[row.status];$('empty').hidden=true;$('imageWork').hidden=false;zoomed=row.status!=='pending'&&!!row.label;draw();renderScore();renderImageDetails();renderQueue();localStorage.setItem('surface-last-image',id);
 }finally{if(version===loadVersion){loading=false;updateControls();renderQueue();}}
}
function draw(){
 if(!picture||!crop)return;
 const canvas=$('sourceCanvas'),preview=$('cropCanvas');
 canvas.width=picture.naturalWidth;canvas.height=picture.naturalHeight;
 canvas.getContext('2d').drawImage(picture,0,0);
 markCrop(canvas,[0,0,current.width,current.height]);
 // While refining, keep the image and coordinate system fixed until release.
 const region=cropDrag?.canvas===preview?cropDrag.region:crop;
 const sx=canvas.width/current.width,sy=canvas.height/current.height;
 const box=[region[0]*sx,region[1]*sy,(region[2]-region[0])*sx,(region[3]-region[1])*sy];
 const scale=Math.min(1400/box[2],1000/box[3]);
 preview.width=Math.max(1,Math.round(box[2]*scale));preview.height=Math.max(1,Math.round(box[3]*scale));
 preview.getContext('2d').drawImage(picture,...box,0,0,preview.width,preview.height);
 if(cropDrag?.canvas===preview)markCrop(preview,region);
 const mini=$('miniCanvas');mini.width=130;mini.height=Math.max(1,Math.round(130*picture.naturalHeight/picture.naturalWidth));
 mini.getContext('2d').drawImage(picture,0,0,mini.width,mini.height);
 canvas.hidden=zoomed;preview.hidden=!zoomed;
 $('viewLabel').textContent=keyboardCrop?'Tastatur-Ausschnitt · W A S D · Umschalt = Größe · F bestätigt':zoomed?'Dein Ausschnitt · zum Verfeinern direkt ziehen':'Original · Rechteck aufziehen';
 $('cropInfo').textContent=`Ausschnitt: ${crop[2]-crop[0]} × ${crop[3]-crop[1]} px`;
 $('cropHint').textContent=zoomed?'Zum Verfeinern direkt im Ausschnitt ziehen. Das Gesamtbild zeigt alle Bereiche.':'Direkt ein Rechteck zeichnen → Ausschnitt wird groß. C startet die Tastaturauswahl.';
 exportPreview();
}
function markCrop(canvas,region){
 const ctx=canvas.getContext('2d'),sx=canvas.width/(region[2]-region[0]),sy=canvas.height/(region[3]-region[1]);
 const box=[(crop[0]-region[0])*sx,(crop[1]-region[1])*sy,(crop[2]-crop[0])*sx,(crop[3]-crop[1])*sy];
 ctx.fillStyle='#14251c80';ctx.beginPath();ctx.rect(0,0,canvas.width,canvas.height);ctx.rect(...box);ctx.fill('evenodd');
 ctx.strokeStyle='#c5ff65';ctx.lineWidth=Math.max(2,canvas.width/450);ctx.strokeRect(...box);
}
function exportPreview(){if(current)$('exportPreview').textContent=`Ausgabe: ${current.origin_folder} / ${$('captureDate').value.replaceAll(':','-').replace('T','_')}_[${current.relative_name.split(/[\\/]/).pop().replace(/\.[^.]+$/,'')}]_${score??'Bewertung'}.jpg`;}
function showOriginal(){if(!picture)return;zoomed=false;keyboardCrop=false;draw();$('sourceCanvas').focus({preventScroll:true});}
function point(event,canvas,region){
 const rect=canvas.getBoundingClientRect();
 const x=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width));
 const y=Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height));
 return [Math.round(region[0]+x*(region[2]-region[0])),Math.round(region[1]+y*(region[3]-region[1]))];
}
function editable(){return !saving&&!loading&&current?.status==='pending';}
function renderScore(){$('scoreValue').textContent=scoreText(score);$('scoreValue').classList.toggle('zero',score===0);document.querySelectorAll('[data-score]').forEach(b=>{const active=score!==null&&(Number(b.dataset.score)===0?score===0:Math.floor(score/10)*10===Number(b.dataset.score));b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active));});$('fineSlider').value=score?score%10:0;exportPreview();updateControls();}
function setScore(value){if(!editable())return;score=value===0?0:Math.max(10,Math.min(99,Math.round(value)));if(score===0)$('alternatives').value='';renderScore();scheduleDraft();}
function fine(delta){if(score==null||score===0)return;setScore(Math.max(10,Math.min(99,score+delta)));}
function updateControls(){
 $('cropCanvas').style.cursor=editable()?'crosshair':'default';
 const edit=editable();for(const id of ['captureDate','dateSource','alternatives','fullImage','trainingEnabled'])$(id).disabled=!edit;document.querySelectorAll('[data-score]').forEach(b=>b.disabled=!edit);for(const id of ['fineSlider','minus','plus'])$(id).disabled=!edit||!score;$('save').disabled=!edit||score===null||!picture;$('reopen').hidden=current?.status!=='rated';$('reopen').disabled=saving||loading;$('ignore').hidden=!current||!['pending','rated'].includes(current.status);$('ignore').disabled=saving||loading;$('remove').hidden=!current||current.status==='removed';$('remove').disabled=saving||loading;$('restore').hidden=!current||!['ignored','removed'].includes(current.status);$('restore').disabled=saving||loading;$('fitSelect').disabled=!current||saving||loading||current.status==='removed';$('savedNotice').hidden=!current?.label;$('savedNotice').textContent=current?.label?`Gespeichertes menschliches Urteil: ${scoreText(current.label.score)} · Version ${current.label.revision}. ${current.status==='pending'?'Du überarbeitest dieses Urteil.':'Zum Ändern „Neu bewerten“ wählen.'}`:'';$('humanBadge').textContent=current?.label?'Mensch · bestätigt':'Deine Eingabe';for(const id of ['statusFilter','workOrder','shuffle'])$(id).disabled=saving;
}
function parseAlternatives(){return $('alternatives').value.trim()?$('alternatives').value.split(/[,;\s]+/).filter(Boolean).map(Number):[];}
function draftBody(){return {image_id:current.id,expected_revision:current.label?.revision||0,draft:{crop:[...crop],score,date:$('captureDate').value,date_source:$('dateSource').value,alternatives:parseAlternatives(),training_enabled:$('trainingEnabled').checked}};}
function scheduleDraft(){clearTimeout(draftTimer);if(editable())draftTimer=setTimeout(guarded(flushDraft),450);}
async function flushDraft(){clearTimeout(draftTimer);if(editable()&&crop)await api('/api/draft',draftBody());}
async function move(delta){if(saving||loading||!current)return;const index=rows.findIndex(r=>r.id===current.id),target=rows[index+delta];if(target)await loadImage(target.id);}
async function save(){if($('save').disabled)return;const request={image_id:current.id,expected_revision:current.label?.revision||0,crop:[...crop],score,date:$('captureDate').value,date_source:$('dateSource').value,alternatives:parseAlternatives(),training_enabled:$('trainingEnabled').checked},next=rows[rows.findIndex(r=>r.id===current.id)+1]?.id;saving=true;clearTimeout(draftTimer);updateControls();renderQueue();try{await api('/api/save',request);current.status='rated';await refreshState();saving=false;await refreshRows(next);toast('Menschliche Bewertung gespeichert. Bild zugeschnitten und nach Herkunftsordner abgelegt.');}finally{saving=false;updateControls();renderQueue();}}
async function imageAction(action,id=current?.id,extra={}){if(!id||saving||loading)return;await flushDraft();const result=await api('/api/image-state',{image_id:id,action,...extra});if(current?.id===id){current.status=result.status||current.status;current.draft=null;}await refreshState();if(currentTab==='workspace'){if(action==='reopen'){$('statusFilter').value='pending';await refreshRows(id);}else {if(action==='restore')$('statusFilter').value=result.status;await refreshRows(action==='restore'?id:undefined);}}else if(currentTab==='library')await refreshLibrary();toast({reopen:'Bewertung zum Überarbeiten geöffnet. Die vorige Version bleibt in der Historie.',remove:'Bild entfernt. Unter „Entfernt“ kannst du es wiederherstellen.',restore:'Bild wiederhergestellt.',ignore:'Bild zurückgestellt und vom Training ausgeschlossen.',training:'Trainingsfreigabe geändert.',relevance:'Eignung gespeichert.'}[action]);}
function renderImageDetails(){
 $('imageHistory').innerHTML=(current.history||[]).map(h=>`<p class="small">${esc(h.created_at.replace('T',' '))} · ${esc({created:'Erste Bewertung',revised:'Überarbeitet',reopen:'Zur Neubewertung geöffnet',remove:'Entfernt',restore:'Wiederhergestellt',ignore:'Zurückgestellt'}[h.action]||h.action)}${JSON.parse(h.snapshot).score!=null?' · vorher '+esc(scoreText(JSON.parse(h.snapshot).score)):''}</p>`).join('')||'<p class=muted>Noch keine Änderungen.</p>';
 $('imageModelResults').innerHTML=(current.model_results||[]).map(r=>{const run=state.runs.find(x=>x.id===r.run_id),v=JSON.parse(r.result),stale=run?.stale||r.label_revision!==(current.label?.revision||0);return `<p>${esc(run?.name||r.run_id)}: <b>${esc(resultText(v))}</b> <small class="ki">KI</small> ${stale?'· veraltet':''}</p>`;}).join('');
}
function filterQuery(){return new URLSearchParams({status:$('filterStatus').value,search:$('filterSearch').value,folder:$('filterFolder').value,min_score:$('filterMin').value,max_score:$('filterMax').value,run_id:$('filterRun').value,min_gap:$('filterGap').value,training:$('filterTraining').value,relevance:$('filterRelevance').value,ai_relevance:$('filterAiRelevance').value,order:$('filterOrder').value,seed});}
async function refreshLibrary(){libraryRows=await api('/api/images?'+filterQuery());libraryLimit=100;renderLibrary();}
function renderLibrary(){
 $('libraryCount').textContent=`${libraryRows.length} Bilder`;
 $('libraryRows').innerHTML=libraryRows.slice(0,libraryLimit).map(r=>`<tr><td><input type="checkbox" data-select="${r.id}" aria-label="${esc(r.relative_name)} auswählen" ${selected.has(r.id)?'checked':''}></td><td><button class="image-link" data-open="${r.id}"><img src="/api/photo?id=${r.id}&size=120" loading="lazy" alt=""><span>${esc(r.relative_name)}</span></button></td><td>${esc(r.origin_folder)}<br><span class="pill">${esc(statusNames[r.status])}</span></td><td class="number">${esc(scoreText(r.score))}${r.alternatives&&JSON.parse(r.alternatives).length?`<small>auch ${esc(JSON.parse(r.alternatives).join(', '))}</small>`:''}</td><td>${r.result_stale?'Veraltet':r.result?esc(resultText(r.result))+' <small class=ki>KI</small>':'—'}</td><td class="number ${r.gap>=20?'large-gap':''}">${r.gap==null?'—':r.gap+' P.'}</td><td>${r.training_enabled?'Freigegeben':'Nicht freigegeben'}<br><small>${r.relevance==null?'Eignung offen':r.relevance?'Passend':'Unpassend'}</small></td><td><button data-open="${r.id}">Ansehen</button>${r.status==='rated'?`<button data-reopen="${r.id}">Neu bewerten</button>`:''}${['ignored','removed'].includes(r.status)?`<button data-restore="${r.id}">Wiederherstellen</button>`:''}</td></tr>`).join('')||'<tr><td colspan="8" class="empty-row">Keine Bilder für diese Filter.</td></tr>';
 $('libraryMore').hidden=libraryRows.length<=libraryLimit;updateSelection();
}
function updateSelection(){$('selectedCount').textContent=`${selected.size} ausgewählt`;$('trainingSelection').textContent=`${selected.size} Bilder ausgewählt. Nur geeignete, freigegebene menschliche Bewertungen gehen ins Qualitätstraining.`;}
async function openImage(id){await tab('workspace');const row=await api('/api/image?id='+id);$('statusFilter').value=row.status;await refreshRows(id);$('main').focus({preventScroll:true});}
async function batch(action,extra={}){if(!selected.size)throw Error('Bitte zuerst Bilder auswählen.');let done=0;const errors=[];for(const id of selected){try{await api('/api/image-state',{image_id:id,action,...extra});done++;}catch(e){errors.push(e.message);}}await refreshState();await refreshLibrary();toast(`${done} Bilder aktualisiert.${errors.length?' '+errors.length+' nicht geändert: '+errors[0]:''}`,!!errors.length);}
function resultText(r){return r.task==='region'?(r.crop?'Ausschnitt vorgeschlagen':'Kein Vorschlag'):r.task==='relevance'?(r.relevant==null?'Kein Vorschlag':r.relevant?'Passend':'Unpassend'):scoreText(r.score);}
function renderRuns(){
 $('runCards').innerHTML=state.runs.map(r=>`<article class="card run-card"><div class="button-row"><h3>${esc(r.name)}</h3><span class="pill${r.stale?' warning':''}">${r.stale?'Training veraltet':'Trainiert'}</span></div><p>${esc(taskNames[r.task])} · ${esc(state.models.find(m=>m.id===r.base_model)?.name||r.base_model)}</p><dl><dt>Kennt</dt><dd>${r.count} ausgewählte Beispiele</dd><dt>Zeitkontext</dt><dd>${r.temporal?'Ja · nichtlinear':'Nein'}</dd><dt>Trainiert</dt><dd>${esc(r.created_at.replace('T',' '))}</dd><dt>Prüfung</dt><dd>${r.evaluated} Bilder${r.metrics.n?` · ${r.metrics.mae} Punkte mittlere Abweichung · ${Math.round(r.metrics.stage_accuracy*100)} % richtige Hauptstufe`:''}${r.stale?' · Ergebnisse veraltet':''}</dd></dl><details><summary>Trainingsinhalt & Entscheidungsgrundlage</summary><p>${esc(r.recipe.algorithm)}</p><p>${esc(r.recipe.limitations)}</p><ul class="sample-list">${r.recipe.samples.map(s=>`<li><button data-open="${s.id}">${esc(s.relative_name)}</button> · ${esc(s.origin_folder)} · ${r.task==='relevance'?(s.relevance?'passend':'unpassend'):esc(scoreText(s.score))}${s.alternatives&&JSON.parse(s.alternatives).length?' · auch '+esc(JSON.parse(s.alternatives).join(', ')):''}</li>`).join('')}</ul><p class="small">Encoder: ${esc(r.recipe.encoder_key)}</p></details><div class="button-row"><button data-use-run="${r.id}" ${r.stale?'disabled':''}>Ergebnisse ansehen</button><button data-run-images="${r.id}">Trainingsbilder auswählen</button><button data-run-library="${r.id}">Abweichungen filtern</button></div></article>`).join('')||'<div class="card empty-row">Noch kein eigener Trainingsstand. Wähle bewertete Bilder in der Übersicht aus.</div>';
}
function renderModelCards(){$('modelCards').innerHTML=state.models.map(m=>`<div class="encoder-card"><h3>${esc(m.name)}</h3><p>${esc(m.description)}</p><p>Gewichte: ${m.installed?'lokal vorhanden':'nicht installiert'}. ${m.installed?`Revision: ${esc(m.revision||'lokale Texturmerkmale')}`:''}</p>${m.blocked_reason?`<p class=warning>${esc(m.blocked_reason)}</p>`:''}${!m.installed&&!state.portable?`<label>Rechenart <select data-device="${m.id}"><option value="cpu">CPU</option><option value="cuda">NVIDIA CUDA</option></select></label><button data-install="${m.id}">Basismodell installieren</button>`:''}</div>`).join('');}
async function renderEvaluation(){
 const run=state.runs.find(r=>r.id===$('evaluationRun').value);if(!run){$('evaluationRows').innerHTML='';return;}const results=await api('/api/images?'+new URLSearchParams({status:'all',run_id:run.id,order:'gap'}));
 $('evaluationRows').innerHTML=results.filter(r=>r.result).map(row=>{const r=row.result;return `<tr><td><button data-open="${row.id}">${esc(row.relative_name)}</button></td><td>${esc(scoreText(row.score))}</td><td><b>${esc(resultText(r))}</b> <small class=ki>KI</small>${row.result_stale?'<p class=warning>Veraltet · neu trainieren / auswerten</p>':''}</td><td>${esc(r.reason)}${r.alternatives?`<p>${r.alternatives.map(a=>`${a.stage}er-Stufe: ${Math.round(a.weight*100)} % Gewicht`).join(' · ')}</p>`:''}${r.neighbors?`<details><summary>${r.train_count} Trainingsbilder · ähnliche Beispiele</summary>${r.neighbors.map(n=>`<p><button data-open="${n.image_id}">${esc(n.file)}</button> · ${esc(scoreText(n.score))} · Ähnlichkeitsgewicht ${Math.round(n.weight*100)} %</p>`).join('')}</details>`:''}</td><td><button data-open="${row.id}">Bild ansehen</button>${r.crop?`<button data-use-crop="${row.id}" ${row.result_stale?'disabled':''}>Ausschnitt übernehmen</button>`:''}${r.score!=null?`<button data-ai-export="${row.id}" ${row.result_stale?'disabled':''}>Als KI-Datei exportieren</button>`:''}</td></tr>`;}).join('')||'<tr><td colspan="5">Noch keine Ergebnisse für diesen Trainingsstand.</td></tr>';
}
async function trainRun(event){event.preventDefault();if(!selected.size)throw Error('Wähle zuerst Trainingsbilder in der Übersicht aus.');const button=event.submitter;button.disabled=true;try{const job=await api('/api/model-runs/train',{name:$('runName').value,base_model:$('baseModel').value,task:$('runTask').value,temporal:$('temporal').checked,image_ids:[...selected]}),run=await followJob(job.job);await refreshState();$('evaluationRun').value=run.id;await renderEvaluation();toast(`„${run.name}“ aus ${run.count} Beispielen trainiert.`);}finally{button.disabled=false;}}
async function evaluate(){const runId=$('evaluationRun').value;if(!runId)throw Error('Bitte einen Trainingsstand wählen.');let ids=[...selected];const scope=$('evaluationScope').value;if(scope!=='selection')ids=(await api('/api/images?status='+scope)).map(r=>r.id);if(!ids.length)throw Error('Keine Bilder ausgewählt.');$('evaluate').disabled=true;try{const job=await api('/api/model-runs/evaluate',{run_id:runId,image_ids:ids}),report=await followJob(job.job);await refreshState();await renderEvaluation();$('evaluationSummary').textContent=`${report.results.length} Bilder ausgewertet${report.metrics.n?` · ${report.metrics.mae} Punkte mittlere Abweichung · ${Math.round(report.metrics.stage_accuracy*100)} % richtige Hauptstufe`:''}. Menschliche Bewertungen unverändert.`;}finally{$('evaluate').disabled=false;}}
async function useCrop(id){const entries=await api('/api/images?'+new URLSearchParams({status:'all',run_id:$('evaluationRun').value})),entry=entries.find(r=>r.id===id);if(!entry?.result?.crop||entry.result_stale)throw Error('Der Ausschnittvorschlag ist nicht mehr aktuell.');await openImage(id);if(current.status==='rated')await imageAction('reopen',id);crop=[...entry.result.crop];zoomed=true;draw();scheduleDraft();toast('KI-Ausschnitt übernommen. Bitte prüfen und selbst bewerten.');}
function beginKeyboardCrop(){if(!editable()||!picture)return;zoomed=false;keyboardCrop=true;if(crop[0]===0&&crop[1]===0&&crop[2]===current.width&&crop[3]===current.height)crop=[Math.round(current.width*.15),Math.round(current.height*.15),Math.round(current.width*.85),Math.round(current.height*.85)];draw();$('sourceCanvas').focus({preventScroll:true});}
function adjustKeyboardCrop(key,resize){const dx=Math.max(1,Math.round(current.width/100)),dy=Math.max(1,Math.round(current.height/100));let[x1,y1,x2,y2]=crop;if(resize){if(key==='a')x2=Math.max(x1+1,x2-dx);if(key==='d')x2=Math.min(current.width,x2+dx);if(key==='w')y2=Math.max(y1+1,y2-dy);if(key==='s')y2=Math.min(current.height,y2+dy);}else{const mx=key==='a'?-Math.min(dx,x1):key==='d'?Math.min(dx,current.width-x2):0,my=key==='w'?-Math.min(dy,y1):key==='s'?Math.min(dy,current.height-y2):0;x1+=mx;x2+=mx;y1+=my;y2+=my;}crop=[x1,y1,x2,y2];draw();scheduleDraft();}
document.querySelectorAll('.nav').forEach(b=>b.onclick=guarded(()=>tab(b.dataset.tab)));
$('scoreButtons').innerHTML=[10,20,30,40,50,60,70,80,90,0].map(v=>`<button data-score="${v}" aria-pressed="false">${v===0?'0<span>nicht bewertbar</span>':v}</button>`).join('');
$('scoreButtons').onclick=e=>{const b=e.target.closest('[data-score]');if(b)setScore(Number(b.dataset.score));};
$('fineSlider').oninput=e=>{if(score)setScore(Math.floor(score/10)*10+Number(e.target.value));};$('minus').onclick=()=>fine(-1);$('plus').onclick=()=>fine(1);$('alternatives').oninput=scheduleDraft;$('trainingEnabled').onchange=scheduleDraft;
$('captureDate').oninput=()=>{$('dateSource').value='manual';scheduleDraft();exportPreview();};$('dateSource').onchange=()=>{const date=current?.dates[$('dateSource').value];if(date)$('captureDate').value=date;scheduleDraft();exportPreview();};
$('fitSelect').onchange=guarded(async()=>{if(!current)return;const v=$('fitSelect').value;await api('/api/image-state',{image_id:current.id,action:'relevance',value:v===''?null:Number(v)});current.relevance=v===''?null:Number(v);await refreshState();});
$('showOriginal').onclick=showOriginal;$('originalThumb').onclick=showOriginal;$('fullImage').onclick=()=>{if(!editable())return;crop=[0,0,current.width,current.height];zoomed=true;draw();scheduleDraft();};
function beginCrop(event,canvas){
 if(!editable()||!picture||drawing||(event.button??0)!==0)return;
 const region=canvas===$('cropCanvas')?[...crop]:[0,0,current.width,current.height];
 cropDrag={canvas,region,previous:[...crop],start:point(event,canvas,region),pointerId:event.pointerId};
 drawing=true;keyboardCrop=false;canvas.focus({preventScroll:true});canvas.setPointerCapture(event.pointerId);
}
function moveCrop(event){
 if(!cropDrag||event.pointerId!==cropDrag.pointerId)return false;
 const end=point(event,cropDrag.canvas,cropDrag.region),start=cropDrag.start;
 if(end[0]===start[0]||end[1]===start[1])return false;
 crop=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])];
 draw();return true;
}
function finishCrop(event){
 if(!cropDrag||event.pointerId!==cropDrag.pointerId)return;
 const changed=moveCrop(event),previous=cropDrag.previous;
 drawing=false;cropDrag=null;
 if(changed){zoomed=true;draw();scheduleDraft();$('cropCanvas').focus({preventScroll:true});}
 else{crop=previous;draw();}
}
function cancelCrop(){if(!cropDrag)return;crop=cropDrag.previous;cropDrag=null;drawing=false;draw();}
for(const canvas of [$('sourceCanvas'),$('cropCanvas')]){
 canvas.onpointerdown=event=>beginCrop(event,canvas);
 canvas.onpointermove=moveCrop;canvas.onpointerup=finishCrop;canvas.onpointercancel=cancelCrop;
}
$('save').onclick=guarded(save);$('reopen').onclick=guarded(()=>imageAction('reopen'));$('ignore').onclick=guarded(()=>imageAction('ignore'));$('remove').onclick=guarded(()=>imageAction('remove'));$('restore').onclick=guarded(()=>imageAction('restore'));$('previous').onclick=guarded(()=>move(-1));$('next').onclick=guarded(()=>move(1));
$('statusFilter').onchange=guarded(async()=>{await flushDraft();await refreshRows();});$('workOrder').onchange=guarded(()=>refreshRows(current?.id));$('shuffle').onclick=guarded(async()=>{seed=crypto.randomUUID();sessionStorage.setItem('surface-shuffle',seed);$('workOrder').value='random';await refreshRows();});$('imageQueue').onclick=guarded(e=>{const b=e.target.closest('[data-image]');if(b)return loadImage(Number(b.dataset.image));});
$('openModelImage').onclick=guarded(async()=>{if(current)selected.add(current.id);updateSelection();await tab('models');$('evaluationScope').value='selection';});
$('filterForm').onsubmit=guarded(async e=>{e.preventDefault();if($('filterGap').value&&!$('filterRun').value)throw Error('Wähle einen Trainingsstand für den Abweichungsfilter.');await refreshLibrary();});$('filterForm').onreset=()=>setTimeout(guarded(refreshLibrary),0);$('libraryMore').onclick=()=>{libraryLimit+=100;renderLibrary();};$('selectVisible').onclick=()=>{libraryRows.forEach(r=>selected.add(r.id));renderLibrary();};$('clearSelection').onclick=()=>{selected.clear();renderLibrary();};$('trainSelection').onclick=guarded(()=>tab('models'));
$('libraryRows').onchange=e=>{if(e.target.dataset.select){const id=Number(e.target.dataset.select);e.target.checked?selected.add(id):selected.delete(id);updateSelection();}};
$('batchTrainingOn').onclick=guarded(()=>batch('training',{enabled:true}));$('batchTrainingOff').onclick=guarded(()=>batch('training',{enabled:false}));$('batchIgnore').onclick=guarded(()=>batch('ignore'));$('batchRemove').onclick=guarded(()=>batch('remove'));$('batchRestore').onclick=guarded(()=>batch('restore'));
$('trainForm').onsubmit=guarded(trainRun);$('chooseTraining').onclick=guarded(()=>tab('library'));$('runTask').onchange=()=>{$('temporal').disabled=$('runTask').value!=='quality';};$('evaluate').onclick=guarded(evaluate);$('evaluationRun').onchange=guarded(renderEvaluation);
for(const id of ['libraryRows','runCards','evaluationRows'])$(id).addEventListener('click',guarded(async e=>{
 const open=e.target.closest('[data-open]');if(open)return openImage(Number(open.dataset.open));const reopen=e.target.closest('[data-reopen]');if(reopen){await openImage(Number(reopen.dataset.reopen));return imageAction('reopen');}const restore=e.target.closest('[data-restore]');if(restore)return imageAction('restore',Number(restore.dataset.restore));
 const use=e.target.closest('[data-use-run]');if(use){$('evaluationRun').value=use.dataset.useRun;await renderEvaluation();$('evaluationRun').focus();return;}const images=e.target.closest('[data-run-images]');if(images){selected.clear();state.runs.find(r=>r.id===images.dataset.runImages).image_ids.forEach(id=>selected.add(id));updateSelection();return tab('library');}const filter=e.target.closest('[data-run-library]');if(filter){$('filterRun').value=filter.dataset.runLibrary;$('filterOrder').value='gap';return tab('library');}const roi=e.target.closest('[data-use-crop]');if(roi)return useCrop(Number(roi.dataset.useCrop));const exp=e.target.closest('[data-ai-export]');if(exp){const result=await api('/api/model-runs/export',{run_id:$('evaluationRun').value,image_id:Number(exp.dataset.aiExport)});toast('KI-Datei exportiert: '+result.saved+'. Menschliche Bewertung unverändert.');}
}));
$('modelCards').onclick=guarded(async e=>{const b=e.target.closest('[data-install]');if(!b)return;b.disabled=true;try{const job=await api('/api/install',{model_id:b.dataset.install,device:document.querySelector(`[data-device="${b.dataset.install}"]`).value});await followJob(job.job);}finally{await refreshState();}});
function openImport(){$('importDialog').showModal();}$('importOpen').onclick=openImport;$('emptyImport').onclick=openImport;$('closeImport').onclick=()=>$('importDialog').close();async function browse(input){const result=await api('/api/browse',{});if(result.path)$(input).value=result.path;}$('browseImport').onclick=guarded(()=>browse('importFolder'));$('browseOutput').onclick=guarded(()=>browse('outputFolder'));
$('importForm').onsubmit=guarded(async e=>{e.preventDefault();const job=await api('/api/import',{folder:$('importFolder').value,recursive:$('recursive').checked});$('importDialog').close();const r=await followJob(job.job);await refreshState();$('statusFilter').value='pending';await tab('workspace');await refreshRows();toast(`${r.imported} importiert · ${r.duplicates} bereits vorhanden · ${r.errors.length} Fehler`,!!r.errors.length);});
$('saveSettings').onclick=guarded(async()=>{await api('/api/settings',{criteria:$('criteria').value,output:$('outputFolder').value});await refreshState();exportPreview();toast('Einstellungen gespeichert.');});$('refreshErrors').onclick=guarded(async()=>{$('errorHistory').textContent=(await api('/api/errors')).map(r=>`${r.created_at} · ${r.scope}\n${r.message}`).join('\n\n')||'Keine Fehler.';});
$('showHelp').onclick=()=>$('helpDialog').showModal();$('closeHelp').onclick=()=>$('helpDialog').close();
document.addEventListener('keydown',guarded(async e=>{
 if(e.key==='F1'){e.preventDefault();if(!$('helpDialog').open)$('helpDialog').showModal();return;}
 if(document.querySelector('dialog[open]'))return;
 if(e.altKey&&/^[1-4]$/.test(e.key)){e.preventDefault();await tab(['workspace','library','models','settings'][Number(e.key)-1]);return;}
 if(e.ctrlKey||e.metaKey||e.altKey||currentTab!=='workspace'||!current||saving||loading)return;
 if(drawing){if(e.key==='Escape'){e.preventDefault();cancelCrop();}return;}
 const active=document.activeElement;
 const textField=active?.isContentEditable||['TEXTAREA','SELECT'].includes(active?.tagName)||
   (active?.tagName==='INPUT'&&!['range','checkbox','radio','button','submit'].includes(active.type));
 if(textField){
   if(e.key==='Escape'){e.preventDefault();$(zoomed?'cropCanvas':'sourceCanvas').focus({preventScroll:true});}
   return;
 }
 const key=e.key.toLowerCase();
 if(e.repeat&&['f','enter'].includes(key)){e.preventDefault();return;}
 if(e.key==='Escape'){e.preventDefault();showOriginal();return;}
 if(keyboardCrop){
   if(['w','a','s','d'].includes(key)){e.preventDefault();adjustKeyboardCrop(key,e.shiftKey);return;}
   if(key==='f'||e.key==='Enter'){
     e.preventDefault();keyboardCrop=false;zoomed=true;draw();scheduleDraft();$('cropCanvas').focus();return;
   }
 }
 if(key==='a'||key==='d'){e.preventDefault();await move(key==='a'?-1:1);return;}
 if(key==='w'||key==='s'){e.preventDefault();fine(key==='w'?1:-1);return;}
 if(/^[0-9]$/.test(e.key)){e.preventDefault();setScore(Number(e.key)*10);return;}
 if(['<','>','-','+'].includes(e.key)){e.preventDefault();fine(e.key==='<'||e.key==='-'?-1:1);return;}
 if(key==='c'){e.preventDefault();beginKeyboardCrop();return;}
 if(key==='r'&&current.status==='rated'){e.preventDefault();await imageAction('reopen');return;}
 if(key==='i'&&['pending','rated'].includes(current.status)){e.preventDefault();await imageAction('ignore');return;}
 if(e.key==='Delete'&&current.status!=='removed'){e.preventDefault();await imageAction('remove');return;}
 if(key==='f'||(e.key==='Enter'&&(active?.tagName!=='BUTTON'||active?.closest('[data-score]')||['minus','plus'].includes(active?.id)))){
   e.preventDefault();await save();
 }
}));
window.addEventListener('beforeunload',()=>{if(editable()&&crop)fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state.token},body:JSON.stringify(draftBody()),keepalive:true}).catch(()=>{});});window.addEventListener('error',e=>reportError(e.error||Error(e.message)));window.addEventListener('unhandledrejection',e=>reportError(e.reason||Error('Browserfehler')));
guarded(async()=>{await refreshState();if(!state.counts.pending&&state.counts.rated)$('statusFilter').value='rated';await refreshRows(Number(localStorage.getItem('surface-last-image'))||undefined);})();

$('rebuildExports').onclick=guarded(async()=>{const button=$('rebuildExports');button.disabled=true;try{const job=await api('/api/rebuild-exports',{}),result=await followJob(job.job);$('rebuildSummary').textContent=`${result.updated} von ${result.total} Exporten neu abgelegt. Bewertungen unver?ndert; fr?here Dateien in data/history.`;}finally{button.disabled=false;}});
