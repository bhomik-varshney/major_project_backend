"""Reuse matching's bounded subprocess, key leases, checkpoints and cancellation."""
from contextlib import redirect_stdout,redirect_stderr
import logging,os
from app.orchestration.matching import execute_matching

def _skill_gap_child(payload,settings,manager,owner,deadline,scratch,output):
    logging.disable(logging.CRITICAL)
    os.environ['CREWAI_STORAGE_DIR']=scratch
    os.environ['OTEL_SDK_DISABLED']='true';os.environ['CREWAI_DISABLE_TELEMETRY']='true';os.environ['CREWAI_TRACING_ENABLED']='false'
    with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
        try:
            from app.flows.skill_gaps import SkillGapFlow
            flow=SkillGapFlow(payload,settings,manager,owner,deadline,lambda kind,data:output.put((kind,data)))
            output.put(('result',flow.kickoff()))
        except Exception:output.put(('error',{'code':'skill_gap_flow_failed','message':'Skill analysis could not finish. Retry when ready.'}))

def execute_skill_gap(payload,settings,manager,progress=lambda stage:None):
    return execute_matching(payload,settings.model_copy(update={'matching_timeout_seconds':settings.skill_gap_timeout_seconds}),manager,progress,child_target=_skill_gap_child)
