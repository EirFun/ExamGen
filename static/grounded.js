let groundedJob=sessionStorage.getItem('examgen-grounded-job'),groundedTimer=null;
$('#grounded-generate').onclick=async()=>{
 if(exam&&!result&&!confirm('Replace your current exam?'))return;
 try{
  const job=await api('/exams/grounded',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:Number($('#question-count').value),topics:[...document.querySelectorAll('#topic-choices input:checked')].map(e=>e.value),question_type:$('#exam-question-type').value,syllabus_id:$('#exam-syllabus')?.value||null})});
  groundedJob=job.id;sessionStorage.setItem('examgen-grounded-job',job.id);$('#grounded-draft').innerHTML='';pollGrounded();
 }catch(e){notify(e.message);}
};
async function pollGrounded(){
 clearTimeout(groundedTimer);
 if(!groundedJob)return;
 try{
  const job=await api('/vision/jobs/'+groundedJob);$('#grounded-progress').hidden=false;
  $('#grounded-progress').innerHTML=`<strong>${esc(job.message)}</strong><p>${Math.round(job.elapsed_seconds||0)}s elapsed · ${job.completed} of ${job.total} questions drafted${job.response_chars?` · ${job.response_chars} response characters`:''}</p><progress max="${job.total||1}" value="${job.completed}"></progress>${job.state==='running'?'<button class="text-button" id="grounded-cancel">Cancel</button>':''}<p>${esc((job.errors||[]).join(' '))}</p>`;
  $('#grounded-generate').disabled=job.state==='running';
  if($('#grounded-cancel'))$('#grounded-cancel').onclick=()=>api('/vision/jobs/'+groundedJob+'/cancel',{method:'POST'});
  if(job.state==='completed'&&job.draft){
   $('#grounded-draft').innerHTML=`<div class="panel"><h3>Review the source-grounded draft</h3><p>${job.draft.questions.length} of ${job.draft.requested} requested questions. Unsupported, duplicate or invalidly cited questions are omitted.</p><p>Check each answer against the quoted book passage. Exam wording guides style; book quotations support answers. Quotations are checked against the uploaded text; reasoning still needs your review.</p>${job.draft.questions.map((q,i)=>`<div class="vision-question-card"><h4>${i+1}. ${esc(q.stem)}</h4>${q.image?`<img class="exam-source-image" src="${esc(q.image)}" alt="Practical diagram">`:''}${q.type==='subjective'?markingHtml(q):`<ol type="A">${q.options.map(o=>`<li>${esc(o)}</li>`).join('')}</ol><p>Answer: ${String.fromCharCode(65+q.answer)}</p>`}<p>${esc(q.explanation)}</p>${bookSourcesHtml(q.book_sources,true)}<p class="muted">Exam style: ${q.exam_sources.map(s=>esc(s.exam)+' · page '+s.page).join(', ')}</p>${q.syllabus_source?`<p class="muted">Syllabus scope: “${esc(q.syllabus_source.quote)}”</p>`:''}</div>`).join('')}<button class="primary" id="grounded-accept">I reviewed the answers · start exam</button></div>`;
   $('#grounded-accept').onclick=async()=>{try{exam=await api('/exams/grounded/'+groundedJob+'/accept',{method:'POST'});answers={};subjectiveAnswers={};selfMarks={};result=null;$('#exam-setup').hidden=true;$('#grounded-draft').innerHTML='';$('#grounded-progress').hidden=true;sessionStorage.removeItem('examgen-grounded-job');groundedJob=null;renderExam();}catch(e){notify(e.message);}};
   return;
  }
  if(['failed','cancelled'].includes(job.state)){notify(job.message);sessionStorage.removeItem('examgen-grounded-job');groundedJob=null;return;}
  groundedTimer=setTimeout(pollGrounded,1200);
 }catch(e){$('#grounded-generate').disabled=false;notify(e.message);sessionStorage.removeItem('examgen-grounded-job');groundedJob=null;}
}
if(groundedJob)pollGrounded();
$('#exam-question-type').onchange=()=>{if($('#exam-question-type').value==='subjective')$('#include-visual').checked=true;$('#generate').disabled=!state.topics.length&&!$('#include-visual').checked;};
