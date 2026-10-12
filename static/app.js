const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {papers: [], topics: []}, exam = null, answers = {}, result = null, uploading = false;
const labels = {overview:'Overview', papers:'Past-paper library', topics:'Topic insights', exam:'Mock exam studio'};
function view(name) {
  document.querySelectorAll('.view').forEach(el => el.hidden = el.id !== name);
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  $('#breadcrumb').textContent = labels[name];
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
  $('#paper-total').textContent = `${state.papers.length} paper${state.papers.length === 1 ? '' : 's'}`;
  $('#paper-list').innerHTML = state.papers.length ? state.papers.map(p => `<div class="paper-row"><span class="file-icon">PDF</span><div class="paper-info"><strong>${esc(p.name)}</strong><small>${p.pages} pages${p.ocr_pages ? ` · OCR: ${p.ocr_pages} pages` : ""} · ${p.topics} topics · ${p.matched_passages} matching passages</small></div><button class="delete" data-delete="${p.id}" aria-label="Delete ${esc(p.name)}">Remove</button></div>`).join('') : empty('▤', 'Your library starts here', 'Upload a past paper to begin building your study map.');
  document.querySelectorAll('[data-delete]').forEach(b => b.onclick = async () => {
    if (!confirm('Remove this paper and its topic evidence?')) return;
    b.disabled = true;
    try { await api('/papers/' + b.dataset.delete, {method:'DELETE'}); await refresh(); notify('Paper removed.', true); } catch(e) { notify(e.message); b.disabled = false; }
  });
  $('#top-topics').innerHTML = state.topics.length ? state.topics.slice(0,4).map((t,i) => `<div class="mini-topic"><div><span class="rank">0${i+1}</span><strong>${esc(t.topic)}</strong><small>${t.paper_count} / ${state.papers.length} papers</small></div><div class="bar"><i style="width:${t.coverage}%"></i></div></div>`).join('') : empty('◎','Your patterns are waiting', state.papers.length ? 'No supported medical topics detected. Try papers covering core clinical subjects.' : 'Add past papers to discover the topics worth a closer look.', '<button class="text-button" id="empty-upload">Add your first paper ↗</button>');
  if ($('#empty-upload')) $('#empty-upload').onclick = selectUpload;
  $('#topic-choices').innerHTML = state.topics.length ? state.topics.map(t => `<label><input type="checkbox" value="${esc(t.topic)}">${esc(t.topic)}</label>`).join('') : '<p class="muted">Upload papers to choose focus topics, or try the demo to explore the practice bank.</p>';
  $('#generate').disabled = !state.topics.length;
  $('#high-yield-list').innerHTML = state.high_yield_questions.length ? state.high_yield_questions.slice(0,10).map(q => `<div class="source"><span class="badge">${q.paper_count > 1 ? 'Recurring passage · ' + q.paper_count + ' papers' : 'Topic found in ' + q.topic_recurrence + ' paper(s)'}</span><p>${esc(q.passage)}</p><small>${q.sources.map(s => esc(s.paper) + ' · page ' + s.page).join(' / ')}</small></div>`).join('') : empty('▤', 'No matching question passages yet', 'Upload your papers to explore questions connected to your recurring topics.');
  renderTopics();
}
function renderTopics() {
  const query = $('#topic-search').value.toLowerCase();
  const topics = state.topics.filter(t => (t.topic + t.subject).toLowerCase().includes(query));
  $('#topic-total').textContent = `${topics.length} topics`;
  $('#topic-list').innerHTML = topics.length ? topics.map((t,i) => `<article class="topic-card"><div class="topic-heading"><span class="rank">${String(i+1).padStart(2,'0')}</span><div class="topic-title"><h3>${esc(t.topic)}</h3><small>${esc(t.subject)}</small></div><span class="badge">${t.paper_count > 1 ? 'Recurring topic' : 'Found in one paper'}</span></div><div class="topic-meta"><span>${t.paper_count} of ${state.papers.length} papers</span><span>${t.mentions} matching passages</span><span>${t.practice_count} practice questions available</span></div><div class="bar"><i style="width:${t.coverage}%"></i></div><details><summary>Explore source evidence (${t.sources.length} excerpts)</summary>${t.sources.map(s => `<div class="source"><strong>${esc(s.paper)} · page ${s.page}</strong><p>${esc(s.excerpt)}</p></div>`).join('')}</details></article>`).join('') : empty('◎',query ? 'No topics match your search' : 'No topic insights yet', query ? 'Try a different topic or subject.' : 'Upload readable medical papers to see recurring topics and the passages behind them.');
}
$('#topic-search').oninput = renderTopics;
function selectUpload(){view('papers'); $('#pdf-input').click();}
$('#top-upload').onclick = selectUpload;
$('#hero-upload').onclick = selectUpload;
$('#pdf-input').onchange = async e => { await uploadFiles([...e.target.files]); e.target.value = ''; };
const drop = $('#dropzone');
['dragenter','dragover'].forEach(event => drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('drag');}));
['dragleave','drop'].forEach(event => drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('drag');}));
drop.addEventListener('drop', e => uploadFiles([...e.dataTransfer.files]));
async function uploadFiles(files) {
  if (uploading || !files.length) return;
  uploading = true; $('#pdf-input').disabled = true;
  const errors = []; let added = 0;
  for (const [i,file] of files.entries()) {
    notify(`Reading ${file.name} (${i+1} of ${files.length})… Scanned pages are recognized automatically; this can take a few minutes.`);
    if (file.size > 20 * 1024 * 1024) { errors.push(`${file.name}: exceeds 20 MB.`); continue; }
    const form = new FormData(); form.append('file',file);
    try { await api('/papers',{method:'POST',body:form}); added++; } catch(e) { errors.push(`${file.name}: ${e.message}`); }
  }
  try { await refresh(); } catch(e) { errors.push(e.message); }
  notify(`${added} paper${added===1?'':'s'} added.${errors.length ? ' ' + errors.join(' ') : ' Your topic insights are ready.'}`, !errors.length);
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
    exam = await api('/exams', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:Number($('#question-count').value),topics:demo?[]:[...document.querySelectorAll('#topic-choices input:checked')].map(e=>e.value),demo})});
    answers={};result=null;$('#exam-setup').hidden=true;renderExam();
    notify(`${exam.demo?'Demo practice. ':''}${exam.actual} unique questions generated.${exam.actual<exam.requested ? ' The selected topics have fewer questions than requested; all available items are included.' : ''}`,true);
  } catch(e) { notify(e.message); }
  finally { $('#generate').disabled=!state.topics.length;$('#demo').disabled=false; }
}
$('#generate').onclick = () => generate();
$('#demo').onclick = () => generate(true);
function renderExam() {
  const questions = result ? result.results : exam.questions;
  $('#exam-body').innerHTML = `${result ? `<div class="result-summary"><span class="pill">PRACTICE COMPLETE</span><p><strong>${result.percent}%</strong> · ${result.score} of ${result.total} correct</p><p>Review the reasoning below, then return to your source papers.</p></div>` : ''}<div class="exam-bar"><div><strong>${exam.demo?'Demo practice':'Your focused mock exam'}</strong><p class="exam-tag" id="exam-progress">${Object.keys(answers).length} of ${questions.length} answered</p></div><div><button class="text-button" id="new-exam">New exam</button>${result ? '<button class="secondary" id="download-exam">↓ Export answers</button> <button class="secondary" id="print-exam">Print / save PDF</button>' : ''}</div></div>${questions.map((q,i)=>`<article class="panel question"><div class="question-top"><span>QUESTION ${String(i+1).padStart(2,'0')}</span><span>${esc(q.topic)}</span></div><h3>${esc(q.stem)}</h3><div class="options">${q.options.map((o,j)=>`<label class="option ${result ? j===q.answer ? 'right' : j===q.selected ? 'wrong' : '' : ''}"><input type="radio" name="${q.id}" value="${j}" ${answers[q.id]===j?'checked':''} ${result?'disabled':''}><span>${String.fromCharCode(65+j)}.</span><span>${esc(o)}</span>${result && j===q.answer ? '<span style="margin-left:auto">✓</span>' : ''}</label>`).join('')}</div>${result ? `<div class="explanation"><strong>${q.correct?'Correct':q.selected===null?'Unanswered':'Review this one'} · Answer ${String.fromCharCode(65+q.answer)}</strong><br>${esc(q.explanation)}</div>` : ''}</article>`).join('')}${result ? '' : '<button class="primary" id="submit-exam">Finish exam & review answers →</button>'}`;
  document.querySelectorAll('.question input').forEach(input => input.onchange=()=>{answers[input.name]=Number(input.value);$('#exam-progress').textContent=`${Object.keys(answers).length} of ${questions.length} answered`;});
  $('#new-exam').onclick=()=>{
    if (!result && !confirm('End this practice session? Your answers will be cleared.')) return;
    exam=null;answers={};result=null;$('#exam-body').innerHTML='';$('#exam-setup').hidden=false;
  };
  if ($('#submit-exam')) $('#submit-exam').onclick = async () => {
    if (Object.keys(answers).length < questions.length && !confirm('Some questions are unanswered. Submit anyway?')) return;
    $('#submit-exam').disabled=true;
    try { result=await api('/exams/'+exam.id+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})});renderExam();$('#exam').scrollIntoView({behavior:'smooth'}); }
    catch(e) {notify(e.message);$('#submit-exam').disabled=false;}
  };
  if ($('#download-exam')) $('#download-exam').onclick = ()=>download('examgen-mock-exam.json',{...exam,...result});
  if ($('#print-exam')) $('#print-exam').onclick=()=>window.print();
}
refresh().catch(e => notify('Could not load your workspace: ' + e.message));
