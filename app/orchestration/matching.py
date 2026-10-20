"""Isolate CrewAI orchestration; cancellation/timeout retain only safe checkpoints."""
from contextlib import redirect_stdout,redirect_stderr
from pathlib import Path
from queue import Empty
from tempfile import TemporaryDirectory
import logging
import multiprocessing
import os
import secrets
import time

from app.providers.groq import ProviderUnavailable


def _matching_child(payload,settings,manager,owner,deadline,scratch,output):
    logging.disable(logging.CRITICAL)
    os.environ['CREWAI_STORAGE_DIR']=scratch
    os.environ['OTEL_SDK_DISABLED']='true';os.environ['CREWAI_DISABLE_TELEMETRY']='true';os.environ['CREWAI_TRACING_ENABLED']='false'
    with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
        try:
            from app.db.connection import make_engine,session_factory
            from app.flows.matching import CareerMatchingFlow
            engine=make_engine(settings.database_url)
            try:
                flow=CareerMatchingFlow(payload,settings,session_factory(engine),manager,owner,deadline,lambda kind,data:output.put((kind,data)))
                output.put(('result',flow.kickoff()))
            finally:engine.dispose()
        except Exception:
            output.put(('error',{'code':'matching_flow_failed','message':'Matching orchestration could not finish. Retry when ready.'}))


def execute_matching(payload,settings,manager,progress=lambda stage:None,child_target=_matching_child):
    context=multiprocessing.get_context('spawn');output=context.Queue();owner=secrets.randbits(62) or 1
    deadline=time.monotonic()+settings.matching_timeout_seconds
    settings.document_storage_path.mkdir(parents=True,exist_ok=True,mode=0o700)
    process=None;checkpoint=None
    def partial(code):
        if checkpoint is None:raise ProviderUnavailable(code,'Matching could not finish before results were ready. Retry when ready.')
        checkpoint['status']='partial';checkpoint['warnings'].append('Matching review was interrupted ('+code+'); validated deterministic results were retained.')
        checkpoint['provider_metadata']['ai_review']={'status':'unavailable','error_code':code,'model':None}
        for task in checkpoint['tasks']:
            if task['status']=='processing':task.update(status='partial',error_code=code)
        return checkpoint
    try:
        with TemporaryDirectory(prefix='matching-',dir=settings.document_storage_path) as scratch:
            process=context.Process(target=child_target,args=(payload,settings,manager,owner,deadline,str(Path(scratch).resolve()),output))
            process.start()
            try:
                while time.monotonic()<deadline:
                    progress(None)
                    try:kind,data=output.get(timeout=min(.2,max(.01,deadline-time.monotonic())))
                    except Empty:
                        if not process.is_alive():return partial('matching_worker_stopped')
                        continue
                    if kind=='stage':progress(data)
                    elif kind=='checkpoint':checkpoint=data
                    elif kind=='error':return partial(data['code'])
                    elif kind=='result':return data
                return partial('matching_timeout')
            finally:
                process.join(timeout=.3)
                if process.is_alive():process.terminate()
                process.join(timeout=2)
                if process.is_alive():process.kill();process.join(timeout=2)
    finally:
        manager.release_owner(owner);output.close();output.cancel_join_thread()
