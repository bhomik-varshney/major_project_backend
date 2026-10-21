"""Reuse the existing cancellable, isolated CrewAI worker and key leases."""
from contextlib import redirect_stdout,redirect_stderr
import logging,os
from app.orchestration.matching import execute_matching

def _cover_child(payload,settings,manager,owner,deadline,scratch,output):
    logging.disable(logging.CRITICAL)
    os.environ['CREWAI_STORAGE_DIR']=scratch
    with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
        try:
            from app.flows.cover_letters import CoverLetterFlow
            result=CoverLetterFlow(payload,settings,manager,owner,deadline,lambda kind,data:output.put((kind,data))).kickoff()
            output.put(('result',result))
        except Exception:output.put(('error',{'code':'cover_flow_failed'}))

def execute_cover(payload,settings,manager,progress):
    return execute_matching(payload,settings.model_copy(update={'matching_timeout_seconds':settings.cover_letter_timeout_seconds}),manager,progress,child_target=_cover_child)
