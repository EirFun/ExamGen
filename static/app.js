const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {papers: [], books:[], syllabi:[], topics: []}, exam = null, answers = {}, subjectiveAnswers={}, selfMarks={}, result = null, uploading = false;
const labels = {overview:'Overview', papers:'PDF library', topics:'Topic insights', vision:'Diagram lab', exam:'Mock exam studio'};
function view(name) {
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== name);
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  $('#breadcrumb').textContent = labels[name];
  if (name === 'vision' && typeof refreshVision === 'function') refreshVision();
  window.scrollTo({top:0, behavior:'smooth'});
}
document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => view(b.dataset.view));
document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => view(b.dataset.go));
function notify(message, success=false) {
  const el = $('#notice'); el.hidden = false; el.textContent = message; el.className = success ? 'success' : '';
}
async function api(path, options={}) {
  const response = await fetch('/api' + path, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Please check your input and try again.');
  return body;
}
const empty = (symbol, title, text, action='') => `<div class="empty"><div class="empty-symbol">${symbol}</div><strong>${title}</strong><p>${text}</p>${action}</div>`;
async function refresh() {
  state = await api('/analysis');
  $('#stat-papers').textContent = state.papers.length;
  $('#stat-topics').textContent = state.topics.length;
  $('#stat-recurring').textContent = state.recurring_topics;
  $('#stat-passages').textContent = state.matched_passages;
  $('#nav-count').textContent = state.papers.length;
  renderLibrary();
  for(const id of ['exam-syllabus','vision-syllabus']){const el=$('#'+id),previous=el.value;el.innerHTML='<option value="">No syllabus restriction</option>'+(state.syllabi||[]).map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');el.value=previous;}
  $('#top-topics').innerHTML = state.topics.length ? state.topics.slice(0,4).map((t,i) => `<div class="mini-topic"><div><span class="rank">0${i+1}</span><strong>${esc(t.topic)}</strong><small>${t.paper_count} / ${state.papers.length} papers</small></div><div class="bar"><i style="width:${t.coverage}%"></i></div></div>`).join('') : empty('◎','Your patterns are waiting', state.papers.length ? 'No supported medical topics detected. Try papers covering core clinical subjects.' : 'Add past papers to discover the topics worth a closer look.', '<button class="text-button" id="empty-upload">Add your first paper ↗</button>');
  if ($('#empty-upload')) $('#empty-upload').onclick = selectUpload;
  $('#topic-choices').innerHTML = state.topics.length ? state.topics.map(t => `<label><input type="checkbox" value="${esc(t.topic)}">${esc(t.topic)}</label>`).join('') : '<p class="muted">Upload papers to choose focus topics, or try the demo to explore the practice bank.</p>';
  $('#generate').disabled = !state.topics.length && !$('#include-visual').checked;
  $('#high-yield-list').innerHTML = state.high_yield_questions.length ? state.high_yield_questions.slice(0,10).map(q => `<div class="source"><span class="badge">${q.paper_count > 1 ? 'Recurring passage · ' + q.paper_count + ' papers' : 'Topic found in ' + q.topic_recurrence + ' paper(s)'}</span><p>${esc(q.passage)}</p><small>${q.sources.map(s => esc(s.paper) + ' · page ' + s.page).join(' / ')}</small></div>`).join('') : empty('▤', 'No matching question passages yet', 'Upload your papers to explore questions connected to your recurring topics.');
  document.querySelectorAll('[data-vision-paper]').forEach(b => b.onclick=()=>openDiagramLab(b.dataset.visionPaper));
  if (typeof refreshVisionPapers === 'function') refreshVisionPapers();
  renderTopics();
}
function renderTopics() {
  const query = $('#topic-search').value.toLowerCase();
  const topics = state.topics.filter(t => (t.topic + t.subject).toLowerCase().includes(query));
  $('#topic-total').textContent = `${topics.length} topics`;
  $('#topic-list').innerHTML = topics.length ? topics.map((t,i) => `<article class="topic-card"><div class="topic-heading"><span class="rank">${String(i+1).padStart(2,'0')}</span><div class="topic-title"><h3>${esc(t.topic)}</h3><small>${esc(t.subject)}</small></div><span class="badge">${t.paper_count > 1 ? 'Recurring topic' : 'Found in one paper'}</span></div><div class="topic-meta"><span>${t.paper_count} of ${state.papers.length} papers</span><span>${t.mentions} matching passages</span><span>${t.practice_count} practice questions available</span></div><div class="bar"><i style="width:${t.coverage}%"></i></div><details><summary>Explore source evidence (${t.sources.length} excerpts)</summary>${t.sources.map(s => `<div class="source"><strong>${esc(s.paper)} · page ${s.page}${s.origin ? ` · ${esc(s.origin)}` : ""}</strong><p>${esc(s.excerpt)}</p></div>`).join('')}</details></article>`).join('') : empty('◎',query ? 'No topics match your search' : 'No topic insights yet', query ? 'Try a different topic or subject.' : 'Upload readable medical papers to see recurring topics and the passages behind them.');
}
$('#topic-search').oninput = renderTopics;
function selectUpload(){setLibraryKind('exam');view('papers'); $('#pdf-input').click();}
$('#top-upload').onclick = selectUpload;
$('#hero-upload').onclick = selectUpload;
$('#pdf-input').onchange = async e => { await uploadFiles([...e.target.files]); e.target.value = ''; };
const drop = $('#dropzone');
['dragenter','dragover'].forEach(event => drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('drag');}));
['dragleave','drop'].forEach(event => drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('drag');}));
drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
async function uploadFiles(files) {
  if (uploading || !files.length) return;
  const uploadKind=libraryKind;
  uploading = true; $('#pdf-input').disabled = true;
  const errors = []; let added = 0;
  for (const [i,file] of files.entries()) {
    notify(`Reading ${file.name} (${i+1} of ${files.length})… Scanned pages are recognized automatically; this can take a few minutes.`);
    if (file.size > (uploadKind==='book'?60:20) * 1024 * 1024) { errors.push(`${file.name}: exceeds ${uploadKind==='book'?60:20} MB.`); continue; }
    const form = new FormData(); form.append('file',file);form.append('kind',uploadKind);
    try { await api('/papers',{method:'POST',body:form}); added++; } catch(e) { errors.push(`${file.name}: ${e.message}`); }
  }
  try { await refresh(); } catch(e) { errors.push(e.message); }
  notify(`${added} paper${added===1?'':'s'} added.${errors.length ? ' ' + errors.join(' ') : ' Your paper library and diagram sources are ready.'}`, !errors.length);
  uploading=false; $('#pdf-input').disabled=false;
}
function download(name, value) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value,null,2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('#export-analysis').onclick = () => download('examgen-topic-analysis.json', state);
async function generate(demo=false) {
  if (exam && !result && !confirm('Replace your current exam? Your answers will be cleared.')) return;
  $('#generate').disabled=true; $('#demo').disabled=true;
  try {
    exam = await api('/exams', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:Number($('#question-count').value),topics:demo?[]:[...document.querySelectorAll('#topic-choices input:checked')].map(e=>e.value),include_visual:!demo && ($('#include-visual').checked||$('#exam-question-type').value==='subjective'),question_type:demo?'mcq':$('#exam-question-type').value,syllabus_id:demo?null:$('#exam-syllabus').value||null,demo})});
    answers={};subjectiveAnswers={};selfMarks={};result=null;$('#exam-setup').hidden=true;renderExam();
    notify(`${exam.demo?'Demo practice. ':''}${exam.actual} unique questions generated.${exam.actual<exam.requested ? ' The selected topics have fewer questions than requested; all available items are included.' : ''}`,true);
  } catch(e) { notify(e.message); }
  finally { $('#generate').disabled=!state.topics.length && !$('#include-visual').checked;$('#demo').disabled=false; }
}
$('#include-visual').onchange=()=>{$('#generate').disabled=!state.topics.length && !$('#include-visual').checked;};
$('#generate').onclick = () => generate();
$('#demo').onclick = () => generate(true);
function bookSourcesHtml(sources,opened=false){return sources?.length?`<details class="book-evidence" ${opened?'open':''}><summary>Book answer references (${sources.length})</summary>${sources.map(s=>`<div class="source"><a href="/api/papers/${encodeURIComponent(s.book_id)}/source.pdf#page=${s.page}" target="_blank" rel="noopener">${esc(s.book)} · PDF page ${s.page}</a><p>“${esc(s.quote)}”</p></div>`).join('')}</details>`:'';}
function markingHtml(q){return `<p class="vision-answer">${esc(q.answer_text)}</p><ul>${q.marking_points.map(p=>`<li>${esc(p)}</li>`).join('')}</ul>`;}
async function submitExam(){result=await api('/exams/'+exam.id+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers,subjective_answers:subjectiveAnswers,self_marks:selfMarks})});renderExam();}
function renderExam() {
 const questions=result?result.results:exam.questions;
 const answered=Object.keys(answers).length+Object.values(subjectiveAnswers).filter(v=>v.trim()).length;
 $('#exam-body').innerHTML=`${result?`<div class="result-summary"><span class="pill">${result.self_assessment_pending?'MODEL ANSWERS · SELF-ASSESSMENT':'PRACTICE COMPLETE'}</span><p>${result.percent==null?'Compare your practical answers with the marking points.':`<strong>${result.percent}%</strong> · ${result.score} of ${result.total} correct${questions.some(q=>q.type==='subjective')?' · self-assessed':''}`}</p></div>`:''}<div class="exam-bar"><div><strong>Your focused mock exam</strong><p id="exam-progress">${answered} of ${questions.length} answered</p></div><div><button class="text-button" id="new-exam">New exam</button>${result?'<button class="secondary" id="download-exam">Export answers</button><button class="secondary" id="print-exam">Print / save PDF</button>':''}</div></div>${questions.map((q,i)=>`<article class="panel question"><div class="question-top"><span>QUESTION ${i+1} · ${q.type==='subjective'?'SUBJECTIVE':'MCQ'}</span><span>${esc(q.topic)}</span></div><h3>${esc(q.stem)}</h3>${q.image?`<img class="exam-source-image" src="${esc(q.image)}" alt="Source diagram"><p class="muted">${esc(q.source)} · ${esc(q.origin)}</p>`:''}${q.type==='subjective'?`<textarea data-subjective="${q.id}" rows="4" maxlength="10000" placeholder="Write your practical answer…" ${result?'disabled':''}>${esc(subjectiveAnswers[q.id]||'')}</textarea>${result?`<div class="explanation"><strong>Model answer & marking points</strong>${markingHtml(q)}<p>${esc(q.explanation)}</p>${bookSourcesHtml(q.book_sources)}<p class="muted">Self-assessment: ${q.correct===null?'not marked':q.correct?'correct':'needs review'}</p><button class="secondary" data-self-mark="${q.id}" data-correct="true">I met the marking points</button><button class="text-button" data-self-mark="${q.id}" data-correct="false">Needs review</button></div>`:''}`:`<div class="options">${q.options.map((o,j)=>`<label class="option ${result?(j===q.answer?'right':j===q.selected?'wrong':''):''}"><input type="radio" name="${q.id}" value="${j}" ${answers[q.id]===j?'checked':''} ${result?'disabled':''}><span>${String.fromCharCode(65+j)}.</span><span class="option-text">${esc(o)}</span>${result&&j===q.answer?`<span class="answer-status">✓ ${j===q.selected?'Your answer · correct':'Correct answer'}</span>`:result&&j===q.selected?'<span class="answer-status">✕ Your answer · incorrect</span>':''}</label>`).join('')}</div>${result?`<div class="explanation"><strong>${q.correct?'Correct':'Review this one'} · Answer ${String.fromCharCode(65+q.answer)}: ${esc(q.options[q.answer])}</strong><p>${esc(q.explanation)}</p>${bookSourcesHtml(q.book_sources)}</div>`:''}`}</article>`).join('')}${result?'':'<button class="primary" id="submit-exam">Finish exam & review answers →</button>'}`;
 const updateCount=()=>{$('#exam-progress').textContent=`${Object.keys(answers).length+Object.values(subjectiveAnswers).filter(v=>v.trim()).length} of ${questions.length} answered`;};
 document.querySelectorAll('.question input').forEach(input=>input.onchange=()=>{answers[input.name]=Number(input.value);updateCount();});
 document.querySelectorAll('[data-subjective]').forEach(input=>input.oninput=()=>{subjectiveAnswers[input.dataset.subjective]=input.value;updateCount();});
 document.querySelectorAll('[data-self-mark]').forEach(b=>b.onclick=async()=>{selfMarks[b.dataset.selfMark]=b.dataset.correct==='true';try{await submitExam();}catch(e){notify(e.message);}});
 $('#new-exam').onclick=()=>{if(!result&&!confirm('End this practice session?'))return;exam=null;answers={};subjectiveAnswers={};selfMarks={};result=null;$('#exam-body').innerHTML='';$('#exam-setup').hidden=false;};
 if($('#submit-exam'))$('#submit-exam').onclick=async()=>{if(Object.keys(answers).length+Object.values(subjectiveAnswers).filter(v=>v.trim()).length<questions.length&&!confirm('Some questions are unanswered. Submit anyway?'))return;try{await submitExam();}catch(e){notify(e.message);}};
 if($('#download-exam'))$('#download-exam').onclick=()=>download('examgen-mock-exam.json',{...exam,...result});
 if($('#print-exam'))$('#print-exam').onclick=()=>window.print();
}

refresh().catch(e => notify('Could not load your workspace: ' + e.message));

let libraryKind='exam';
function setLibraryKind(kind){libraryKind=kind;renderLibrary();}
function renderLibrary(){
 const list=libraryKind==='book'?(state.books||[]):libraryKind==='syllabus'?(state.syllabi||[]):state.papers;
 $('#pdf-input').accept=libraryKind==='syllabus'?'.pdf,.png,.jpg,.jpeg,.webp':'application/pdf,.pdf';
 $('#library-heading').textContent=libraryKind==='book'?'Your PDF books':libraryKind==='syllabus'?'Your syllabi · optional scope guides':'Your exam PDFs';
 $('#upload-title').textContent=libraryKind==='book'?'Drop your PDF books here':libraryKind==='syllabus'?'Drop a syllabus PDF or photo here':'Drop your exam PDFs here';
 $('#upload-limits').textContent=`or browse files · up to ${libraryKind==='book'?60:20} MB each`;
 $('#paper-total').textContent=list.length+' PDFs';
 $('#library-exams').classList.toggle('primary',libraryKind==='exam');$('#library-books').classList.toggle('primary',libraryKind==='book');
 $('#paper-list').innerHTML=list.length?list.map(p=>`<div class="paper-row"><span class="file-icon">PDF</span><div class="paper-info"><strong>${esc(p.name)}</strong><small>${p.pages} pages · ${libraryKind==='book'?'Answer reference book':libraryKind==='syllabus'?'Optional topic scope guide':`${p.topics} topics · ${p.matched_passages} matching passages`}</small></div><select data-kind="${p.id}" aria-label="PDF category"><option value="exam" ${libraryKind==='exam'?'selected':''}>Exam</option><option value="book" ${libraryKind==='book'?'selected':''}>Book</option><option value="syllabus" ${libraryKind==='syllabus'?'selected':''}>Syllabus</option></select><button class="text-button" data-vision-paper="${p.id}">Diagrams ↗</button><button class="delete" data-delete="${p.id}">Remove</button></div>`).join(''):empty('▤','No '+(libraryKind==='book'?'books':libraryKind==='syllabus'?'syllabi':'exams')+' yet','Upload PDFs into this category.');
 document.querySelectorAll('[data-kind]').forEach(b=>b.onchange=async()=>{try{await api('/papers/'+b.dataset.kind+'/kind',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:b.value})});await refresh();}catch(e){notify(e.message);}});
 document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Remove this PDF and its source evidence?'))return;try{await api('/papers/'+b.dataset.delete,{method:'DELETE'});await refresh();}catch(e){notify(e.message);}});
 document.querySelectorAll('[data-vision-paper]').forEach(b=>b.onclick=()=>openDiagramLab(b.dataset.visionPaper));
}
$('#library-exams').onclick=()=>setLibraryKind('exam');$('#library-books').onclick=()=>setLibraryKind('book');

$('#library-syllabi').onclick=()=>setLibraryKind('syllabus');
