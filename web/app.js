'use strict';
const $ = id => document.getElementById(id);
// Also hide the removed profile while an older server finishes another model's download.
const removedModels = new Set(['qwen35-4b','gemma3-4b','moondream2']);
let state, rows = [], current = null, picture = null, crop = null, score = null, prediction = null;
let draftTimer, toastTimer, loadVersion = 0, drawing = false, startPoint, currentTab = 'workspace', saving = false;
const dateNames = {exif_original:'EXIF Aufnahme',exif_digitized:'EXIF Digitalisierung',exif_datetime:'EXIF Änderung',modified:'Datei geändert',created:'Datei erstellt',import:'Importdatum',manual:'Manuell'};
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(text, error=false) {clearTimeout(toastTimer); $('toast').textContent=text; $('toast').className='toast'+(error?' error':'');$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?12000:4500);}
async function api(path, body, renew=true) {const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state.token},body:JSON.stringify(body)});const value=await response.json();if(!response.ok){if(renew&&body!==undefined&&response.status===403&&value.error?.startsWith('Sitzung ungültig')){const session=await api('/api/state');state.token=session.token;return api(path,body,false);}const error=Error(value.error||'Anfrage fehlgeschlagen');error.reported=!!value.error_id;throw error;}return value;}
function reportClientError(error){if(!state?.token||error.reported)return;fetch('/api/client-error',{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state.token},body:JSON.stringify({message:error.message||String(error),details:error.stack||''}),keepalive:true}).catch(()=>{});}
const guarded = fn => async (...args) => {try {await fn(...args);} catch(error){reportClientError(error);toast(error.message,true);}};
const wait = ms => new Promise(resolve=>setTimeout(resolve,ms));
let foregroundJobs = 0;
async function followJob(job, {quiet=false}={}) {
  if(!quiet)foregroundJobs++;
  try {while(true){const result=await api('/api/jobs/'+job);if(!quiet){$('jobBar').hidden=false;$('jobTitle').textContent=result.title;$('jobMessage').textContent=result.message;}if(result.status==='error')throw Error(result.message);if(result.status==='done')return result.result;await wait(1100);}}
  finally {if(!quiet)foregroundJobs--;if(!foregroundJobs)$('jobBar').hidden=true;}
}
function tab(name){currentTab=name;document.querySelectorAll('.tab').forEach(el=>el.hidden=el.id!==name);document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.tab===name));$('pageTitle').textContent={workspace:'Oberflächen bewerten',models:'Modelle & Lernen',settings:'Projekteinstellungen'}[name];}
async function refreshState(){state=await api('/api/state');state.models=state.models.filter(m=>!removedModels.has(m.id));state.metrics=state.metrics.filter(m=>!removedModels.has(m.model_id));if(removedModels.has(state.model_id))state.model_id='manual';renderState();}
function renderState(){
  const c=state.counts;$('ratedCount').textContent=c.rated;$('pendingCount').textContent=c.pending;$('progress').max=Math.max(1,c.total);$('progress').value=c.rated+c.ignored;$('progressText').textContent=c.total?`${c.rated+c.ignored} von ${c.total} abgeschlossen · ${c.ignored} ignoriert`:'Noch keine Bilder importiert';
  $('modelSelect').innerHTML=state.models.map(m=>`<option value="${m.id}">${escapeHTML(m.name)}</option>`).join('');$('modelSelect').value=state.model_id;
  const model=state.models.find(m=>m.id===state.model_id);$('modelStatus').textContent=model.installed?(model.blocked_reason?'Installiert · RAM fehlt':model.kind==='baseline'?'Offline bereit':`Bereit · ${(model.device||'cpu').toUpperCase()}${model.quantization==='nf4'?' · 4 Bit':''}`):'Noch nicht installiert';
  $('criteria').value=state.criteria;$('outputFolder').value=state.output;renderModels();renderDiagnostics();updateControls();
}
function renderModels(){
  $('sharedDataset').textContent=`Dein gemeinsamer Datensatz: ${state.dataset.count} gespeicherte Ausschnitte mit festen Bewertungen. Beim Modellwechsel werden sie automatisch übernommen. Du musst kein Bild erneut bearbeiten oder bewerten.`;
  $('modelCards').innerHTML=state.models.map(m=>{
    const availability=m.kind==='baseline'?'Sofort bereit':m.installed?(m.blocked_reason?'Installiert · zu wenig freier Arbeitsspeicher':`Pakete & Gewichte installiert${m.quantization==='nf4'?' · GPU mit 4 Bit':''}`):m.environment?'Eigene Umgebung angelegt · Gewichte fehlen':'Umgebung noch nicht angelegt';
    const learning=m.head.up_to_date?`Alle ${m.head.available} Bewertungen übernommen`:`${m.head.count} von ${m.head.available} Bewertungen übernommen · ${m.installed?'wird bei Auswahl aktualisiert':'Übernahme nach Installation'}`;
    return `<article class="model-card card"><div><h3>${escapeHTML(m.name)}</h3><p>${escapeHTML(m.description)}</p><div class="model-meta">↓ ${escapeHTML(m.download)}</div><div class="model-meta">${escapeHTML(m.memory)}</div><div class="model-state">${availability}<br>${learning} · ${m.head.classes} Stufen</div>${m.runtime_note?`<p class="small">${escapeHTML(m.runtime_note)}</p>`:''}${m.repo?`<a class="small muted" href="https://huggingface.co/${escapeHTML(m.repo)}" target="_blank" rel="noreferrer">Modellkarte ↗</a>`:''}</div><div class="model-actions">${m.kind!=='baseline'&&!state.portable?`<select data-device="${m.id}" aria-label="Rechenart ${escapeHTML(m.name)}"><option value="cpu" ${m.device!=='cuda'?'selected':''}>CPU</option><option value="cuda" ${m.device==='cuda'?'selected':''}>NVIDIA GPU (CUDA)</option></select><button data-install="${m.id}">${m.installed?'Neu installieren':'Installieren & laden'}</button>`:''}<button data-choose="${m.id}" class="${m.id===state.model_id?'primary':''}">${m.id===state.model_id?'Ausgewählt':'Auswählen'}</button><button data-train="${m.id}" ${!m.installed||!state.dataset.count?'disabled':''}>Gespeicherte Bewertungen übernehmen</button><button data-benchmark="${m.id}" ${!m.installed||!state.dataset.count?'disabled':''}>Gespeicherte Bilder testen</button></div></article>`;
  }).join('');
  $('metrics').innerHTML='<table><thead><tr><th>Modell</th><th>Bestätigte Vorschläge</th><th>Mittlere Abweichung</th><th>Richtige Hauptstufe</th></tr></thead><tbody>'+state.metrics.map(m=>`<tr><td>${escapeHTML(state.models.find(x=>x.id===m.model_id).name)}</td><td>${m.n}</td><td>${m.mae===null?'—':m.mae+' Punkte'}</td><td>${m.stage_accuracy===null?'—':Math.round(m.stage_accuracy*100)+' %'}</td></tr>`).join('')+'</tbody></table>';
  renderBenchmark();
}
async function benchmarkModels(modelIds){const body={include_raw:!!$('benchmarkRaw')?.checked};if(modelIds)body.model_ids=modelIds;const job=await api('/api/benchmark',body);await refreshBenchmark();const report=await followJob(job.job);await refreshState();const done=report.models.filter(m=>m.status==='done').length;toast(`Test beendet: ${done} von ${report.models.length} Modellen vollständig ausgewertet. Details stehen in der Tabelle.`,done!==report.models.length);}
let benchmarkPolling=false;
async function refreshBenchmark(){if(benchmarkPolling||!state)return;benchmarkPolling=true;try{const update=await api('/api/benchmark');state.benchmark=update.report;state.benchmarkJobs=update.jobs;renderBenchmark();}finally{benchmarkPolling=false;}}
setInterval(()=>{if(currentTab==='models')refreshBenchmark().catch(()=>{});},3000);
function evaluationView(m){
  const kind=state.models.find(model=>model.id===m.model_id)?.kind;
  const mode=m.score_mode||(m.model_id==='manual'?'learned_texture':kind==='embedding'?'learned_vision':'raw_vision');
  return {mode,metric:m.primary||(mode==='raw_vision'?m.raw:m.cv),key:mode==='raw_vision'?'raw_score':'cv_score',label:mode==='raw_vision'?'KI-Rohbewertung · nicht auf deine Skala angelernt':mode==='learned_texture'?'Angelernt mit Farb-/Texturmerkmalen':'Angelernt mit eigenen Vision-Merkmalen'};
}
function renderBenchmark(){
  if(!$('benchmarkPanel')){
    const panel=document.createElement('div');panel.id='benchmarkPanel';panel.className='card learning-info';
    panel.innerHTML='<h2>Test auf deinen gespeicherten Bildern</h2><p>Verglichen werden deine feste Bewertung und die auf den übrigen Bildern angelernte Vorhersage. Das jeweilige Testbild und identische Originalkopien werden zurückgehalten. Modelle ohne angelerntes Vision-Modell sind als Rohbewertung gekennzeichnet.</p><label class="checkbox"><input type="checkbox" id="benchmarkRaw"> Zusätzlich unangepasste KI-Rohbewertungen prüfen (langsamer)</label><button id="benchmarkAll" class="primary">Installierte Modelle testen</button><p id="benchmarkProgress" role="status"></p><div id="benchmarkResults"></div>';
    $('models').appendChild(panel);$('benchmarkAll').onclick=guarded(()=>benchmarkModels());
  }
  const jobs=state.benchmarkJobs||[],busy=jobs.length>0;
  $('benchmarkAll').disabled=busy||!state.dataset.count;
  $('benchmarkRaw').disabled=busy;
  $('benchmarkProgress').textContent=busy?jobs[0].message:'';
  document.querySelectorAll('[data-benchmark]').forEach(button=>{const m=state.models.find(m=>m.id===button.dataset.benchmark);button.disabled=busy||!m?.installed||!state.dataset.count;});
  const report=state.benchmark;if(!report){$('benchmarkResults').textContent='Noch kein gespeicherter Vergleich.';return;}
  const names={done:'Abgeschlossen',partial:'Teilweise getestet',blocked:'Nicht ausgeführt',insufficient_data:'Zu wenig unabhängige Bilder',not_installed:'Nicht installiert',error:'Fehler',running:'Läuft'};
  const metric=(m,key)=>m?.[key]==null?'—':key==='mae'?m[key]+' Punkte':Math.round(m[key]*100)+' %';
  const integrity=value=>value===true?'Ja':report.complete?'Nein':'Prüfung läuft';
  const visible=report.models.filter(m=>!removedModels.has(m.model_id));
  const rows=visible.map(m=>{const view=evaluationView(m),notRun=view.mode==='raw_vision'&&!view.metric?.n&&m.status!=='running';return `<tr><td>${escapeHTML(m.name)}</td><td>${escapeHTML(view.label)}</td><td>${escapeHTML(notRun?'Nicht ausgeführt':names[m.status]||m.status)}${m.message?'<br>'+escapeHTML(m.message):''}</td><td>${view.metric?.n||0} / ${report.dataset.count}</td><td>${metric(view.metric,'mae')}</td><td>${metric(view.metric,'stage_accuracy')}</td></tr>`;}).join('');
  const details=visible.filter(m=>m.rows?.length).map(m=>{const view=evaluationView(m);return `<details><summary>${escapeHTML(m.name)}: Einzelwerte ansehen</summary><div class="table-scroll"><table><thead><tr><th>Bild</th><th>Deine Bewertung</th><th>${view.mode==='raw_vision'?'Rohbewertung':'Angelernt, Testbild zurückgehalten'}</th><th>Zusätzliche Rohbewertung</th></tr></thead><tbody>${m.rows.map(r=>`<tr><td>${escapeHTML(r.file)}</td><td>${r.actual}</td><td>${r[view.key]??'—'}</td><td>${view.mode==='raw_vision'?'—':r.raw_score??'nicht angefordert'}</td></tr>`).join('')}</tbody></table></div></details>`;}).join('');
  const legacy=!report.schema_version?'<p class="date-warning">Älterer Test: Die früheren Textur-Lernkopfwerte von Qwen und Moondream sind hier nicht als deren Modellleistung aufgeführt. Bitte den korrigierten Test neu ausführen.</p>':'';
  $('benchmarkResults').innerHTML=`<p>${report.dataset.count} gespeicherte Ausschnitte · Bewertungen ${report.score_range[0]}–${report.score_range[1]} · ${escapeHTML(report.created_at.slice(0,19).replace('T',' '))}</p>${legacy}<div class="table-scroll"><table><thead><tr><th>Modell</th><th>Bewertungsgrundlage</th><th>Status</th><th>Getestete Bilder</th><th>Mittlere Abweichung</th><th>Richtige Hauptstufe</th></tr></thead><tbody>${rows}</tbody></table></div>${details}<p class="muted small">${escapeHTML(report.note)}</p><p class="small">Nutzerbewertungen unverändert: ${integrity(report.labels_unchanged)} · Bildexporte unverändert: ${integrity(report.exports_unchanged)}</p>`;
}
function renderDiagnostics(){
  if(!$('errorHistory')){const details=document.createElement('details');details.innerHTML='<summary>Lokales Fehlerprotokoll</summary><pre id="errorHistory"></pre>';$('settings').appendChild(details);}
  $('errorHistory').textContent=(state.errors||[]).map(e=>`${e.created_at} · ${e.scope}\n${e.message}`).join('\n\n')||'Keine Fehler protokolliert. Frühere Pop-ups wurden noch nicht dauerhaft erfasst.';
}
async function refreshRows(preferId){rows=await api('/api/images?status='+$('statusFilter').value);renderQueue();if(rows.length)await loadImage(rows.some(r=>r.id===preferId)?preferId:rows[0].id);else clearImage();}
function renderQueue(){
  $('queueCount').textContent=rows.length+' Bilder';
  const selectedIndex=rows.findIndex(r=>r.id===current?.id), start=Math.max(0,selectedIndex-8), visible=rows.slice(start,start+40);
  $('imageQueue').innerHTML=visible.map(r=>`<button class="queue-item ${r.id===current?.id?'selected':''}" data-image="${r.id}" title="${escapeHTML(r.relative_name)}"><img loading="lazy" src="/api/photo?id=${r.id}&size=160" alt=""><span>${escapeHTML(r.relative_name)}${r.score?' · '+r.score:''}</span></button>`).join('');
  $('previous').disabled=selectedIndex<=0;$('next').disabled=selectedIndex<0||selectedIndex>=rows.length-1;
}
function clearImage(){loadVersion++;current=null;picture=null;crop=null;score=null;prediction=null;$('empty').hidden=false;$('imageWork').hidden=true;$('cropCanvas').style.display='none';$('cropPlaceholder').hidden=false;$('comparison').hidden=true;$('captureDate').value='';$('dateSource').innerHTML='';$('dateWarning').textContent='';$('allDates').textContent='';$('savedNotice').hidden=true;renderScore();renderPrediction();renderQueue();updateControls();}
async function loadImage(id){
  if(saving)return;
  await flushDraft();const version=++loadVersion;
  const row=await api('/api/image?id='+id);if(version!==loadVersion)return;
  current=row;picture=null;prediction=null;const draft=row.draft||{};
  localStorage.setItem('surface-last-image',String(id));
  crop=row.label?JSON.parse(row.label.crop):(draft.crop||[0,0,row.width,row.height]);score=row.label?row.label.score:(draft.score??null);
  $('empty').hidden=true;$('imageWork').hidden=false;$('imageName').textContent=row.relative_name;$('dimensions').textContent=`${row.width} × ${row.height} px`;
  $('dateSource').innerHTML=Object.entries(row.dates).filter(([key])=>dateNames[key]).map(([key])=>`<option value="${key}">${dateNames[key]}</option>`).join('')+'<option value="manual">Manuell</option>';
  $('captureDate').value=draft.date||row.chosen_date;$('dateSource').value=draft.date_source||row.date_source;$('dateWarning').textContent=row.warning||'';$('allDates').textContent=JSON.stringify(row.dates,null,2);
  $('savedNotice').hidden=row.status==='pending';$('savedNotice').textContent=row.label?`Deine feste Bewertung: ${row.label.score} Punkte. Ausschnitt und Bewertung sind für alle Modelle gespeichert. Datei: ${row.label.output_path}`:'Dieses Bild wird nicht im Training verwendet.';
  renderScore();renderPrediction();renderQueue();renderComparison();updateControls();
  const image=new Image();image.onload=()=>{if(version!==loadVersion)return;picture=image;draw();updateControls();};image.onerror=()=>toast('Bild konnte nicht geladen werden.',true);image.src='/api/photo?id='+row.id+'&size=2400';
}
function draw(){
  if(!picture||!crop||!current)return;
  const canvas=$('sourceCanvas');canvas.width=picture.width;canvas.height=picture.height;const ctx=canvas.getContext('2d');ctx.drawImage(picture,0,0);
  const scale=picture.width/current.width,x=crop[0]*scale,y=crop[1]*scale,w=(crop[2]-crop[0])*scale,h=(crop[3]-crop[1])*scale;
  ctx.fillStyle='rgba(16,40,32,.35)';ctx.beginPath();ctx.rect(0,0,canvas.width,canvas.height);ctx.rect(x,y,w,h);ctx.fill('evenodd');ctx.strokeStyle='#b9ef9e';ctx.lineWidth=Math.max(2,canvas.width/600);ctx.strokeRect(x,y,w,h);
  const preview=$('cropCanvas');const ratio=Math.min(1,900/w,600/h);preview.width=Math.max(1,Math.round(w*ratio));preview.height=Math.max(1,Math.round(h*ratio));preview.getContext('2d').drawImage(picture,x,y,w,h,0,0,preview.width,preview.height);preview.style.display='block';$('cropPlaceholder').hidden=true;$('cropInfo').textContent=`Ausschnitt: ${crop[2]-crop[0]} × ${crop[3]-crop[1]} px`;
}
function point(event){const rect=$('sourceCanvas').getBoundingClientRect();return [Math.max(0,Math.min(current.width,Math.round((event.clientX-rect.left)/rect.width*current.width))),Math.max(0,Math.min(current.height,Math.round((event.clientY-rect.top)/rect.height*current.height)))];}
function clearPrediction(){prediction=null;renderPrediction();}
function renderScore(){
  const main=score===null?null:Math.min(100,Math.floor(score/10)*10);
  document.querySelectorAll('[data-score]').forEach(button=>{button.classList.toggle('selected',Number(button.dataset.score)===main);button.disabled=!current||current.status!=='pending';});
  $('scoreValue').textContent=score??'—';$('fineSlider').value=score===null?0:score-main;$('fineSlider').disabled=score===null||main===100||current?.status!=='pending';$('minus').disabled=score===null||score<=10||current?.status!=='pending';$('plus').disabled=score===null||score>=100||current?.status!=='pending';updateControls();
}
function setScore(value){if(saving||!current||current.status!=='pending')return;score=Math.min(100,Math.max(10,Math.round(value)));renderScore();scheduleDraft();}
function updateControls(){const editable=!saving&&!!current&&current.status==='pending';const model=state?.models.find(m=>m.id===state.model_id);const comparable=!saving&&current&&current.status!=='ignored';$('save').disabled=!editable||score===null||!picture;$('ignore').disabled=saving||!current||current.status==='rated';$('ignore').textContent=current?.status==='ignored'?'Bild wieder aufnehmen':'Bild ignorieren';$('predict').disabled=!comparable||!picture||!model?.installed||model.can_infer===false;$('predict').title=model?.blocked_reason||(!model?.installed?'Modellinstallation noch nicht abgeschlossen.':'');$('predict').textContent=current?.status==='rated'?'Mit Bewertung vergleichen ↗':'Analysieren ↗';$('fullImage').disabled=!editable||!picture;$('captureDate').disabled=!editable;$('dateSource').disabled=!editable;$('acceptPrediction').disabled=!editable||prediction?.score==null;$('modelSelect').disabled=saving;}
function renderPrediction(){$('aiScore').textContent=prediction?.score??'—';const model=state?.models.find(m=>m.id===state.model_id);$('aiReason').textContent=prediction?.reason||model?.blocked_reason||(model&&!model.installed?'Dieses Modell wird noch installiert. Nach Abschluss die Seite aktualisieren.':state?.model_id==='manual'?'Deine Bewertung trainiert ein kleines lokales Basislernmodell.':current?.status==='rated'?'Du kannst diesen gespeicherten Ausschnitt mit dem Modell vergleichen. Deine feste Bewertung bleibt erhalten.':'Klicke auf Analysieren für einen Vorschlag zum Ausschnitt.');$('uncertainty').textContent=prediction?.uncertainty!==undefined?`Unsicherheit: ${Math.round(prediction.uncertainty*100)} %`:'';updateControls();}
function renderComparison(){if(!current||!crop)return;const latest=new Map();for(const p of current.predictions||[]){if(JSON.stringify(JSON.parse(p.crop))===JSON.stringify(crop)&&!latest.has(p.model_id))latest.set(p.model_id,p);}$('comparison').hidden=!latest.size;$('comparisonRows').innerHTML=[...latest.values()].map(p=>{const model=state.models.find(m=>m.id===p.model_id);let saved={};try{saved=p.result?JSON.parse(p.result):{};}catch{}const source=p.score_source||saved.score_source;const label=source==='raw_vision'?' · Rohbewertung':source?.startsWith('learned_')?' · angelernt':model?.kind==='vlm'?' · frühere Rohbewertung':'';return `<div class="compare-item">${escapeHTML((model?.name||p.model_id)+label)}<b>${p.score??'—'}</b></div>`;}).join('');}
function draftBody(){return {image_id:current.id,draft:{crop:[...crop],score,date:$('captureDate').value,date_source:$('dateSource').value}};}
function scheduleDraft(){clearTimeout(draftTimer);if(!saving&&current?.status==='pending')draftTimer=setTimeout(guarded(flushDraft),450);}
async function flushDraft(){clearTimeout(draftTimer);if(!saving&&current?.status==='pending'&&crop)await api('/api/draft',draftBody());}
async function chooseModel(id){
  await flushDraft();
  const selected=await api('/api/settings',{model_id:id});
  clearPrediction();await refreshState();renderPrediction();
  try {
    if(selected.sync_job){
      const result=await followJob(selected.sync_job);
      toast(result.waiting_for_install?result.message:`${result.count} gespeicherte Bewertungen für dieses Modell übernommen.`);
    }
  } finally {await refreshState();}
}
async function move(delta){if(!current)return;const index=rows.findIndex(r=>r.id===current.id), target=rows[index+delta];if(target)await loadImage(target.id);}
async function save(){if(saving||$('save').disabled)return;saving=true;updateControls();clearTimeout(draftTimer);const nextId=rows[rows.findIndex(r=>r.id===current.id)+1]?.id;try{const result=await api('/api/save',{image_id:current.id,crop,score,date:$('captureDate').value,date_source:$('dateSource').value,model_id:state.model_id,prediction_id:prediction?.prediction_id||null});current.status='rated';toast('Ausschnitt und Bewertung für alle Modelle gespeichert. Lernmodell wird aktualisiert.');await refreshState();saving=false;await refreshRows(nextId);followJob(result.job,{quiet:true}).then(refreshState).catch(error=>toast('Bild gespeichert, aber Training fehlgeschlagen: '+error.message,true));}finally{saving=false;updateControls();}}

document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>tab(button.dataset.tab)));
$('scoreButtons').innerHTML=Array.from({length:10},(_,i)=>`<button data-score="${(i+1)*10}" title="Hauptstufe ${(i+1)*10}">${(i+1)*10}</button>`).join('');
$('scoreButtons').addEventListener('click',event=>{const button=event.target.closest('[data-score]');if(button)setScore(Number(button.dataset.score));});
$('fineSlider').addEventListener('input',event=>{if(score!==null)setScore(Math.floor(score/10)*10+Number(event.target.value));});$('minus').onclick=()=>setScore(score-1);$('plus').onclick=()=>setScore(score+1);
$('captureDate').oninput=()=>{$('dateSource').value='manual';scheduleDraft();};$('dateSource').onchange=()=>{const value=current?.dates[$('dateSource').value];if(value)$('captureDate').value=value;scheduleDraft();};
$('modelSelect').onchange=guarded(event=>chooseModel(event.target.value));
$('fullImage').onclick=()=>{crop=[0,0,current.width,current.height];clearPrediction();draw();renderComparison();scheduleDraft();};
$('sourceCanvas').onpointerdown=event=>{if(saving||!picture||current?.status!=='pending')return;drawing=true;startPoint=point(event);$('sourceCanvas').setPointerCapture(event.pointerId);};
$('sourceCanvas').onpointermove=event=>{if(!drawing)return;const p=point(event);if(p[0]===startPoint[0]||p[1]===startPoint[1])return;crop=[Math.min(startPoint[0],p[0]),Math.min(startPoint[1],p[1]),Math.max(startPoint[0],p[0]),Math.max(startPoint[1],p[1])];draw();};
function finishCrop(){if(!drawing)return;drawing=false;clearPrediction();renderComparison();scheduleDraft();}
$('sourceCanvas').onpointerup=finishCrop;$('sourceCanvas').onpointercancel=finishCrop;
$('predict').onclick=guarded(async()=>{if($('predict').disabled)return;await flushDraft();const request={image_id:current.id,model_id:state.model_id,crop:[...crop]};$('predict').disabled=true;try{const task=await api('/api/predict',request);const result=await followJob(task.job);if(current?.id===request.image_id&&state.model_id===request.model_id&&JSON.stringify(crop)===JSON.stringify(request.crop)){prediction=result;current.predictions.unshift({id:result.prediction_id,model_id:request.model_id,crop:JSON.stringify(request.crop),score:result.score,score_source:result.score_source});renderPrediction();renderComparison();}}finally{updateControls();}});
$('acceptPrediction').onclick=()=>{if(prediction?.score!=null)setScore(prediction.score);};$('save').onclick=guarded(save);
$('ignore').onclick=guarded(async()=>{if(!current)return;const restore=current.status==='ignored',id=current.id;clearTimeout(draftTimer);await api('/api/ignore',{image_id:id,restore});current.status=restore?'pending':'ignored';await refreshState();await refreshRows();toast(restore?'Bild wieder aufgenommen.':'Bild ignoriert und vom Training ausgeschlossen.');});
$('previous').onclick=guarded(()=>move(-1));$('next').onclick=guarded(()=>move(1));
$('imageQueue').onclick=guarded(async event=>{const button=event.target.closest('[data-image]');if(button)await loadImage(Number(button.dataset.image));});
$('statusFilter').onchange=guarded(async()=>{await flushDraft();current=null;await refreshRows();});
$('activeLearning').onclick=guarded(async()=>{const task=await api('/api/active',{model_id:state.model_id});const order=await followJob(task.job);await flushDraft();$('statusFilter').value='pending';rows=await api('/api/images?status=pending');const ranking=new Map(order.map((r,i)=>[r.id,i]));rows.sort((a,b)=>(ranking.get(a.id)??Infinity)-(ranking.get(b.id)??Infinity));if(rows.length)await loadImage(rows[0].id);renderQueue();toast('Offene Bilder nach Unsicherheit sortiert. Unmarkierte Bilder werden als Ganzes beurteilt.');});
function openImport(){ $('importDialog').showModal(); }
$('importOpen').onclick=openImport;$('emptyImport').onclick=openImport;$('closeImport').onclick=()=>$('importDialog').close();
async function browse(input){const result=await api('/api/browse',{});if(result.path)$(input).value=result.path;}
$('browseImport').onclick=guarded(()=>browse('importFolder'));$('browseOutput').onclick=guarded(()=>browse('outputFolder'));
$('importForm').onsubmit=guarded(async event=>{event.preventDefault();const task=await api('/api/import',{folder:$('importFolder').value,recursive:$('recursive').checked});$('importDialog').close();const result=await followJob(task.job);await refreshState();$('statusFilter').value='pending';await refreshRows(current?.id);toast(`${result.imported} Bilder importiert · ${result.duplicates} bereits vorhanden${result.errors.length?'\n'+result.errors.length+' Fehler: '+result.errors.slice(0,5).map(e=>e.file+': '+e.error).join('\n'):''}`,!!result.errors.length);});
$('saveSettings').onclick=guarded(async()=>{await api('/api/settings',{criteria:$('criteria').value,output:$('outputFolder').value});clearPrediction();await refreshState();toast('Einstellungen gespeichert.');});
$('modelCards').onclick=guarded(async event=>{const install=event.target.closest('[data-install]'),choose=event.target.closest('[data-choose]'),train=event.target.closest('[data-train]'),benchmark=event.target.closest('[data-benchmark]');if(benchmark)await benchmarkModels([benchmark.dataset.benchmark]);if(choose)await chooseModel(choose.dataset.choose);if(train){train.disabled=true;const task=await api('/api/train',{model_id:train.dataset.train});await followJob(task.job);await refreshState();toast('Lernmodell aktualisiert.');}if(install){const id=install.dataset.install,device=document.querySelector(`[data-device="${id}"]`).value;install.disabled=true;const task=await api('/api/install',{model_id:id,device});try{await followJob(task.job);toast('Modell lokal installiert.');}finally{await refreshState();}}});
window.addEventListener('error',event=>reportClientError(event.error||Error(event.message)));
window.addEventListener('unhandledrejection',event=>reportClientError(event.reason||Error('Unbehandelter Browserfehler')));
document.addEventListener('keydown',guarded(async event=>{if(['INPUT','TEXTAREA','SELECT','BUTTON'].includes(document.activeElement.tagName)||$('importDialog').open||currentTab!=='workspace')return;if(!current||current.status!=='pending')return;if(/^[0-9]$/.test(event.key)){event.preventDefault();setScore(event.key==='0'?100:Number(event.key)*10);}if(event.key==='ArrowRight'&&score!==null){event.preventDefault();setScore(score+1);}if(event.key==='ArrowLeft'&&score!==null){event.preventDefault();setScore(score-1);}if(event.key==='Enter'){event.preventDefault();await save();}if(event.key.toLowerCase()==='i')await $('ignore').onclick();}));
window.addEventListener('beforeunload',()=>{if(current?.status==='pending'&&crop)fetch('/api/draft',{method:'POST',headers:{'Content-Type':'application/json','X-Surface-Token':state.token},body:JSON.stringify(draftBody()),keepalive:true}).catch(()=>{});});
guarded(async()=>{await refreshState();if(!state.counts.pending&&state.counts.rated)$('statusFilter').value='rated';await refreshRows(Number(localStorage.getItem('surface-last-image'))||undefined);})();
