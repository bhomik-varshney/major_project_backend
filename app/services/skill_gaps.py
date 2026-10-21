"""Owned immutable run snapshots and publication; candidate facts are read-only."""
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import time
from sqlalchemy import select,update
from app.core.errors import AppError
from app.db.models import SkillGapRun,VerificationRecord,now
from app.repositories.profiles import find_profile
from app.schemas.skill_gaps import ACTIVE_GAPS,GapRunResponse,GapResults
from app.services.profiles import confirmed_fields,canonical
from app.services.skill_gap_analysis import gap_facts
from app.services.match_normalization import fingerprint
from app.orchestration.skill_gaps import execute_skill_gap

def candidate_source(db,user_id):
    profile=find_profile(db,user_id)
    confirmed=confirmed_fields(db,profile) if profile else {}
    facts=gap_facts(confirmed)
    proofs={}
    if profile:
        reviews=db.scalars(select(VerificationRecord).where(VerificationRecord.profile_id==profile.id).order_by(VerificationRecord.created_at)).all()
        for review in reviews:
            valid={field for field,value in review.confirmed_values.items() if field in facts and canonical(field,value)==canonical(field,facts[field])}
            for path,proof in review.source_evidence.items():
                if path.split('.')[0] in valid:
                    proofs[path]={**proof,'source_available':review.document_id is not None,'verification_record_id':review.id}
    return profile,{'facts':facts,'proofs':proofs,'profile_source':'confirmed_fields_only','draft_data_used':False,'unknown_proficiency':True}

def gap_run_response(db,run):
    profile,source=candidate_source(db,run.user_id)
    return GapRunResponse(id=run.id,status=run.status,stage=run.stage,profile_revision=run.profile_revision,
        stale=not profile or profile.revision!=run.profile_revision or fingerprint(source['facts'])!=run.candidate_hash,
        request=run.request,config=run.config,job_count=run.job_count,gap_count=run.gap_count,tasks=run.tasks,warnings=run.warnings,
        provider_metadata=run.provider_metadata,error_code=run.error_code,created_at=run.created_at,completed_at=run.completed_at,elapsed_ms=run.elapsed_ms)

class GapStopped(Exception):pass

class SkillGapProcessor:
    def __init__(self,factory,settings,manager):
        self.factory,self.settings,self.manager=factory,settings,manager
        self.capacity=BoundedSemaphore(4);self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='skill-gaps')

    def recover_interrupted(self):
        with self.factory() as db:
            db.execute(update(SkillGapRun).where(SkillGapRun.status.in_(ACTIVE_GAPS)).values(status='failed',stage='failed',active_user=None,error_code='skill_gap_interrupted',completed_at=now(),warnings=['Skill analysis was interrupted by restart. Retry with current confirmed facts.']))
            db.commit()

    def reserve(self):
        if not self.capacity.acquire(blocking=False):raise AppError(429,'skill_gap_busy','Skill analysis queue is full. Retry after another run finishes.')

    def submit(self,run_id,context):
        try:self.executor.submit(self._run,run_id,context)
        except RuntimeError:
            self.capacity.release()
            with self.factory() as db:
                db.execute(update(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.status.in_(ACTIVE_GAPS)).values(status='failed',stage='failed',active_user=None,error_code='skill_gap_unavailable',completed_at=now()));db.commit()
            raise AppError(503,'skill_gap_unavailable','Skill analysis worker is shutting down.') from None

    def _run(self,run_id,context):
        started=time.monotonic()
        try:
            with self.factory() as db:
                run=db.get(SkillGapRun,run_id)
                if not run or run.status not in ACTIVE_GAPS:return
                payload={**context,'candidate_snapshot':run.candidate_snapshot,'jobs':run.job_snapshot,'selection':run.selection,
                    'mode':run.request['mode'],'top_k':run.request['top_k'],'use_ai':run.request['use_ai']}
            def progress(data):
                with self.factory() as db:
                    if data is None:
                        if db.scalar(select(SkillGapRun.status).where(SkillGapRun.id==run_id)) not in ACTIVE_GAPS:raise GapStopped()
                    else:
                        changed=db.execute(update(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.status.in_(ACTIVE_GAPS)).values(status=data['stage'],stage=data['stage'],tasks=data['tasks']))
                        if changed.rowcount!=1:raise GapStopped()
                        db.commit()
            result=GapResults.model_validate(execute_skill_gap(payload,self.settings,self.manager,progress))
            progress(None)
            # Checkpoint publication still requires the frozen input snapshots.
            if result.candidate_snapshot!=payload['candidate_snapshot']:raise ValueError('candidate_snapshot_changed')
            original={j['id']:j for j in payload['jobs']}
            for entry in result.jobs:
                if entry['job']!=original.get(entry['job']['id']):raise ValueError('job_snapshot_changed')
            status='needs_review' if result.conflicts else result.status
            with self.factory() as db:
                changed=db.execute(update(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.status.in_(ACTIVE_GAPS)).values(status=status,stage=status,active_user=None,data=result.model_dump(mode='json'),
                    job_count=len(result.jobs),gap_count=len(result.gaps),tasks=result.tasks,warnings=result.warnings,provider_metadata=result.provider_metadata,completed_at=now(),elapsed_ms=round((time.monotonic()-started)*1000)))
                if changed.rowcount!=1:raise GapStopped()
                db.commit()
        except GapStopped:pass
        except Exception as exc:
            code=exc.code if hasattr(exc,'code') and isinstance(exc.code,str) else 'skill_gap_failed'
            with self.factory() as db:
                db.execute(update(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.status.in_(ACTIVE_GAPS)).values(status='failed',stage='failed',active_user=None,error_code=code,completed_at=now(),elapsed_ms=round((time.monotonic()-started)*1000),warnings=['Skill analysis could not finish. Your profile is unchanged; retry using current confirmed facts.']))
                db.commit()
        finally:self.capacity.release()

    def shutdown(self):self.executor.shutdown(wait=True,cancel_futures=False)
