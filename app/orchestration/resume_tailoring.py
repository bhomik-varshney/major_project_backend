"""Reuse cancellable worker isolation and existing shared Groq leases."""
from contextlib import redirect_stdout,redirect_stderr
import logging,os
from app.orchestration.matching import execute_matching

def _resume_child(payload,settings,manager,owner,deadline,scratch,output):
    logging.disable(logging.CRITICAL);os.environ['CREWAI_STORAGE_DIR']=scratch
    with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
        try:
            from app.flows.resume_tailoring import ResumeTailoringFlow
            output.put(('result',ResumeTailoringFlow(payload,settings,manager,owner,deadline,lambda kind,data:output.put((kind,data))).kickoff()))
        except Exception:output.put(('error',{'code':'resume_tailoring_flow_failed'}))

def execute_resume(payload,settings,manager,progress):
    return execute_matching(payload,settings.model_copy(update={'matching_timeout_seconds':settings.cover_letter_timeout_seconds}),manager,progress,child_target=_resume_child)
