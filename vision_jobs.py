"""Small in-process job queue; interpretations themselves persist in SQLite."""
import threading
import uuid

_jobs={}
_lock=threading.Lock()
_busy=threading.Lock()


def get(job_id):
    with _lock:
        job=_jobs.get(job_id)
        return dict(job) if job else None

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
        _jobs[job_id]={'id':job_id,'kind':kind,'state':'running','message':'Starting…','completed':0,'total':0,'cancelled':False,'errors':[]}
    def run():
        try:
            worker(job_id)
            update(job_id,state='cancelled' if get(job_id)['cancelled'] else 'completed')
        except Exception as error:
            update(job_id,state='failed',message=str(error)[:600])
        finally:_busy.release()
    threading.Thread(target=run,daemon=True).start()
    return get(job_id)


def cancel(job_id):
    with _lock:
        job=_jobs.get(job_id)
        if not job:return None
        job['cancelled']=True
        job['message']='Stopping after the current page or download chunk…'
        return dict(job)
