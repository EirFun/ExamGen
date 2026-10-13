let visionBookDefaultApplied=false;
let visionPaper=null, visionData=null, visionReady=false, visionRegion=null, visionBusy=false, visionPoll=null;
let visionActive=sessionStorage.getItem('examgen-vision-job');
function openDiagramLab(id){visionPaper=id;view('vision');}
function refreshVisionPapers(){
  if(state.books?.length&&!visionBookDefaultApplied){$('#vision-use-books').checked=true;visionBookDefaultApplied=true;}
  const select=$('#vision-paper');
  if (![...state.papers,...(state.books||[])].some(p=>p.id===visionPaper)) visionPaper=[...state.papers,...(state.books||[])][0]?.id||null;
  select.innerHTML=[...state.papers,...(state.books||[])].length?[...state.papers,...(state.books||[])].map(p=>`<option value="${p.id}" ${p.id===visionPaper?'selected':''}>${esc(p.name)}</option>`).join(''):'<option>No papers yet</option>';
}
async function refreshVision(){
  refreshVisionPapers();
  await Promise.all([checkVision(),loadVisionPaper()]);
  if (visionActive && !visionPoll) pollVision(visionActive);
}
async function checkVision(){
  try{
    const info=await api('/vision/status');visionReady=info.ready;$('#vision-model').value=info.model;$('#vision-model').disabled=visionBusy;
    let hardware=info.loaded?(info.gpu_bytes>0?` ${info.processor}${info.gpu_percent==null?'':` · ${info.gpu_percent}% GPU memory residency`} · ${(info.gpu_bytes/1024**3).toFixed(1)} GB VRAM.`:' Running on CPU; check your NVIDIA driver and Ollama settings.'):'';
    $('#vision-status').textContent=info.message+hardware;
    $('#vision-download').disabled=!info.connected||info.installed||visionBusy;
  }catch(e){visionReady=false;$('#vision-status').textContent=e.message;}
  updateVisionButtons();
}
function updateVisionButtons(){
  const paper=[...state.papers,...(state.books||[])].find(p=>p.id===visionPaper);
  const similar=$('#vision-mode').value==='similar';
  $('#vision-analyze').disabled=visionBusy||!visionReady||!paper?.has_images||(similar&&!visionRegion);
  $('#vision-analyze').textContent=similar?'Create similar questions':'Analyze this page';
  $('#vision-similar-fields').hidden=!similar;
  $('#vision-batch').disabled=visionBusy||!visionReady||!paper?.has_images||similar;
}
async function loadVisionPaper(){
  if(!visionPaper){$('#vision-image').hidden=true;$('#vision-image-empty').hidden=false;updateVisionButtons();return;}
  try{
    visionData=await api('/papers/'+visionPaper+'/visuals');
    $('#vision-page').innerHTML=Array.from({length:visionData.pages},(_,i)=>`<option value="${i+1}">Page ${i+1}</option>`).join('');
    $('#vision-source-warning').hidden=visionData.has_images;
    loadVisionPage();
  }catch(e){notify(e.message);}
}
function resetCrop(){visionRegion=null;$('#vision-crop').hidden=true;$('#crop-hint').textContent='Whole page selected. Drag around the diagram to create similar questions.';updateVisionButtons();}
function loadVisionPage(){
  resetCrop();const page=Number($('#vision-page').value);
  const saved=visionData?.results.find(r=>r.page===page);
  $('#vision-verified-label').value=saved?.verified_label||'';
  $('#vision-original-question').value=saved?.original_question||'';
  $('#vision-image').hidden=!visionData?.has_images;
  $('#vision-image-empty').hidden=!!visionData?.has_images;
  if(visionData?.has_images)$('#vision-image').src=`/api/papers/${visionPaper}/pages/${page}/image`;
  renderVisionResult();updateVisionButtons();
}
function renderVisionResult(){
  const detail=visionData?.results.find(r=>r.page===Number($('#vision-page').value));
  if(!detail){$('#vision-result').innerHTML='<div class="empty">Analyze this page to see its interpretation and practice questions.</div>';return;}
  $('#vision-result').innerHTML=`<span class="badge">${detail.reviewed?'Reviewed for practice':'Local AI draft'} · ${esc(detail.model)}</span><div class="vision-draft">Check the identification, labels and answers against your course materials. A small vision model can misread medical diagrams.</div><h4>What the model sees</h4><div class="vision-answer">${esc(detail.description)}</div>${detail.identified_items.length?`<ul>${detail.identified_items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<h4>Reasoning</h4><div class="vision-answer">${esc(detail.reasoning)}</div>${detail.uncertainties.length?`<h4>Uncertainties</h4><ul>${detail.uncertainties.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}<p class="muted">Topics: ${esc(detail.topics.join(', ')||'No topic inferred')}</p>${detail.region?'<p class="muted">This result used a diagram crop.</p>':''}${detail.mode==='similar'?'<h4>Similar mock-exam questions</h4><p class="muted">These new questions reuse your cropped PDF diagram.</p>':''}${detail.performance?`<p class="muted">Generation: ${detail.performance.elapsed_seconds}s · load ${detail.performance.load_seconds||0}s · read/prefill ${detail.performance.prompt_seconds||0}s${detail.performance.tokens_per_second?` · ${detail.performance.tokens_per_second} tokens/sec`:''}</p>`:''}${detail.verified_label?`<p class="muted">Student-confirmed structure/topic: ${esc(detail.verified_label)}</p>`:''}${detail.questions.map(q=>`<div class="vision-question-card"><strong>${esc(q.stem)}</strong>${q.type==='subjective'?markingHtml(q):`<ol type="A">${q.options.map(o=>`<li>${esc(o)}</li>`).join('')}</ol>`}<details><summary>${q.type==='subjective'?'Explanation':'Answer & explanation'}</summary><p>${q.type==='subjective'?'':String.fromCharCode(65+q.answer)+'. '+esc(q.options[q.answer])+': '}${esc(q.explanation)}</p></details>${bookSourcesHtml(q.book_sources)}</div>`).join('')}<button class="secondary" id="vision-review">${detail.reviewed?'Remove from reviewed practice':'Mark reviewed & use for practice'}</button>`;
  $('#vision-review').onclick=async()=>{
    try{await api(`/papers/${visionPaper}/visuals/${detail.page}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({reviewed:!detail.reviewed})});detail.reviewed=!detail.reviewed;renderVisionResult();await refresh();notify(detail.reviewed?'Reviewed topics and questions are available in exam practice.':'Interpretation returned to draft.',true);}catch(e){notify(e.message);}
  };
}
async function startVision(pages){
  try{
    const job=await api(`/papers/${visionPaper}/vision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pages,syllabus_id:$('#vision-syllabus').value||null,verified_label:$('#vision-verified-label').value,use_books:$('#vision-use-books').checked,question_type:$('#vision-question-type').value,question_count:Number($('#vision-question-count').value),speed:$('#vision-speed').value,mode:$('#vision-mode').value,original_question:$('#vision-original-question').value,question:$('#vision-question').value,region:pages.length===1?visionRegion:null})});
    sessionStorage.setItem('examgen-vision-paper',visionPaper);beginVisionJob(job);
  }catch(e){notify(e.message);}
}
function beginVisionJob(job){visionActive=job.id;sessionStorage.setItem('examgen-vision-job',job.id);visionBusy=true;updateVisionButtons();$('#vision-download').disabled=true;$('#vision-job').hidden=false;pollVision(job.id);}
async function pollVision(id){
  if(visionPoll)clearTimeout(visionPoll);visionPoll=null;
  try{
    const job=await api('/vision/jobs/'+id);
    $('#vision-job').hidden=false;$('#vision-job-message').textContent=job.message+` · ${Math.round(job.elapsed_seconds||0)}s elapsed${job.response_chars?` · ${job.response_chars} characters written`:''}`;
    $('#vision-job-count').textContent=job.kind==='analysis'?`${job.completed} of ${job.total} pages finished`:job.percent==null?'':`${job.percent}% of current download layer`;
    const percent=job.kind==='analysis'?(job.total?job.completed/job.total*100:0):job.percent;
    if(percent==null)$('#vision-progress').removeAttribute('value');else $('#vision-progress').value=percent;
    $('#vision-job-errors').textContent=(job.errors||[]).join(' ');
    if(['completed','failed','cancelled'].includes(job.state)){
      visionBusy=false;visionActive=null;sessionStorage.removeItem('examgen-vision-job');$('#vision-cancel').hidden=true;
      await checkVision();
      if(visionPaper){visionData=await api('/papers/'+visionPaper+'/visuals');renderVisionResult();}
      if(job.state==='failed')notify(job.message);else notify(job.message,job.state==='completed'&&!job.errors.length);
      return;
    }
    visionBusy=true;$('#vision-cancel').hidden=false;updateVisionButtons();visionPoll=setTimeout(()=>pollVision(id),1500);
  }catch(e){visionBusy=false;visionActive=null;sessionStorage.removeItem('examgen-vision-job');updateVisionButtons();notify(e.message);}
}
$('#vision-use-books').onchange=()=>{visionBookDefaultApplied=true;};
$('#vision-model').onchange=async()=>{try{await api('/vision/model',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:$('#vision-model').value})});await checkVision();}catch(e){notify(e.message);await checkVision();}};
$('#vision-mode').onchange=updateVisionButtons;
$('#vision-refresh').onclick=refreshVision;
$('#vision-paper').onchange=()=>{visionPaper=$('#vision-paper').value;loadVisionPaper();};
$('#vision-page').onchange=loadVisionPage;
$('#vision-reset-crop').onclick=resetCrop;
$('#vision-analyze').onclick=()=>startVision([Number($('#vision-page').value)]);
$('#vision-batch').onclick=()=>{
  const input=prompt('Which pages? Enter a range such as 1-5 or a list such as 1,3,7. Up to 20 pages per task.',`1-${Math.min(visionData.pages,20)}`);
  if(!input)return;let pages=[];
  for(const part of input.split(',')){
    const match=part.trim().match(/^(\d+)(?:-(\d+))?$/);if(!match){notify('Enter page numbers or ranges such as 1-5,7.');return;}
    const first=Number(match[1]),last=Number(match[2]||match[1]);
    if(first<1||last<first||last>visionData.pages||last-first>20){notify('Choose valid pages from this paper.');return;}
    for(let p=first;p<=last;p++)pages.push(p);
  }
  pages=[...new Set(pages)];if(pages.length>20){notify('Choose at most 20 pages per task.');return;}startVision(pages);
};
$('#vision-download').onclick=async()=>{try{beginVisionJob(await api('/vision/download',{method:'POST'}));}catch(e){notify(e.message);}};
$('#vision-cancel').onclick=async()=>{if(visionActive)try{await api('/vision/jobs/'+visionActive+'/cancel',{method:'POST'});}catch(e){notify(e.message);}};
let cropStart=null;
const wrap=$('#vision-image-wrap');
function relativePoint(e){const box=$('#vision-image').getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-box.left)/box.width)),Math.max(0,Math.min(1,(e.clientY-box.top)/box.height))];}
wrap.onpointerdown=e=>{if($('#vision-image').hidden)return;cropStart=relativePoint(e);wrap.setPointerCapture(e.pointerId);e.preventDefault();};
wrap.onpointermove=e=>{
  if(!cropStart)return;const point=relativePoint(e);const x=Math.min(cropStart[0],point[0]),y=Math.min(cropStart[1],point[1]),w=Math.abs(point[0]-cropStart[0]),h=Math.abs(point[1]-cropStart[1]);
  visionRegion=[x,y,w,h];const box=$('#vision-crop');box.hidden=false;Object.assign(box.style,{left:`${x*100}%`,top:`${y*100}%`,width:`${w*100}%`,height:`${h*100}%`});
};
wrap.onpointerup=()=>{cropStart=null;if(!visionRegion||visionRegion[2]<.02||visionRegion[3]<.02)resetCrop();else $('#crop-hint').textContent='Diagram crop selected. New questions will reuse this image. Keep original answers outside the crop.';updateVisionButtons();};
wrap.onpointercancel=()=>{cropStart=null;resetCrop();};
$('#vision-image').onerror=()=>{notify('Could not show this source page. Try restoring the PDF by uploading it again.');};
if(visionActive){visionPaper=sessionStorage.getItem('examgen-vision-paper');visionBusy=true;}
