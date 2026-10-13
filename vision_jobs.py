"""Small in-process job queue; interpretations themselves persist in SQLite."""
import threading
import uuid
import time

_jobs={}
_lock=threading.Lock()
_busy=threading.Lock()


def get(job_id):
    with _lock:
        job=_jobs.get(job_id)
        return {**job,'elapsed_seconds':round(job.get('ended',time.monotonic())-job['started'],1)} if job else None

def update(job_id,**fields):
    with _lock:
        _jobs[job_id].update(fields)


def start(kind,worker):
    if not _busy.acquire(blocking=False):
        raise RuntimeError('A local vision task is already running. Wait for it to finish or cancel it first.')
    job_id=uuid.uuid4().hex
    with _lock:
        if len(_jobs)>=50:
            for old in list(_jobs):
                if _jobs[old]['state'] not in ('running','queued'):
                    del _jobs[old]
                    if len(_jobs)<40:break
        _jobs[job_id]={'started':time.monotonic(),'id':job_id,'kind':kind,'state':'running','message':'Starting…','completed':0,'total':0,'cancelled':False,'errors':[]}
    def run():
        try:
            worker(job_id)
            update(job_id,state='cancelled' if get(job_id)['cancelled'] else 'completed')
            if get(job_id)['cancelled']:update(job_id,message='Task cancelled after the current response.')
        except Exception as error:
            update(job_id,state='failed',message=str(error)[:600])
        finally:
            update(job_id,ended=time.monotonic());_busy.release()
    threading.Thread(target=run,daemon=True).start()
    return get(job_id)


def cancel(job_id):
    with _lock:
        job=_jobs.get(job_id)
        if not job:return None
        job['cancelled']=True
        job['message']='Stopping after the current page or download chunk…'
        return dict(job)


def configure(operation):
    if not _busy.acquire(blocking=False):raise RuntimeError('Wait for the current local model task before changing models.')
    try:return operation()
    finally:_busy.release()
