"""Private matching runs, confirmed-field snapshots and bounded worker ownership."""
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import time

from sqlalchemy import select,update

from app.core.errors import AppError
from app.db.models import MatchingRun,MatchingResult,now
from app.repositories.profiles import find_profile
from app.schemas.jobs import JobFilters
from app.schemas.matching import ACTIVE_MATCHING,MatchingRunResponse,RankedMatch
from app.services.match_normalization import matching_facts,fingerprint
from app.services.match_scoring import validate_ranking
from app.services.profiles import confirmed_fields
from app.services.jobs import filter_jobs
from app.orchestration.matching import execute_matching


def readiness(db,user_id):
    profile=find_profile(db,user_id)
    confirmed=confirmed_fields(db,profile) if profile else {}
    facts=matching_facts(confirmed)
    useful=any(bool(value) for value in facts.values())
    return {'profile_exists':profile is not None,'profile_revision':profile.revision if profile else 0,'can_match':useful,
        'confirmed_fields':sorted(field for field,value in facts.items() if value),'excluded_unconfirmed_fields':sorted(field for field,value in (profile.facts.items() if profile else []) if field in matching_facts({field:value}) and value and field not in confirmed),
        'missing_fields':[field for field in ('skills','preferred_roles','preferred_locations','education','experience_level') if not facts.get(field)],
        'message':'Only confirmed fields will be used. Incomplete fields stay unknown.' if useful else 'Review and confirm profile facts before finding matching jobs.'},facts


def run_response(db,run):
    profile=find_profile(db,run.user_id)
    facts=matching_facts(confirmed_fields(db,profile)) if profile else {}
    stale=profile is None or profile.revision!=run.profile_revision or fingerprint(facts)!=run.candidate_hash
    validation=dict(run.validation)
    for key in ('retrieval','filter_audit'):
        if key in validation:
            validation[key]={k:v for k,v in validation[key].items() if k not in {'candidates','excluded_jobs'}}
    return MatchingRunResponse(id=run.id,status=run.status,stage=run.stage,profile_revision=run.profile_revision,stale=stale,
        dataset_version=run.dataset_version,dataset_reference_date=run.dataset_reference_date,total_jobs=run.total_jobs,
        eligible_jobs=run.eligible_jobs,result_count=run.result_count,minimum_score=run.request['minimum_score'],filters=run.request['filters'],
        config=run.config,provider_metadata=run.provider_metadata,tasks=run.tasks,warnings=run.warnings,validation=validation,
        error_code=run.error_code,created_at=run.created_at,completed_at=run.completed_at,elapsed_ms=run.elapsed_ms)


def filtered_snapshot(snapshot,filters):
    return filter_jobs(snapshot,filters)[:filters.max_results]


class MatchingStopped(Exception):pass


class MatchingProcessor:
    def __init__(self,factory,settings,key_manager):
        self.factory,self.settings,self.manager=factory,settings,key_manager
        self.capacity=BoundedSemaphore(4)
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='matching')

    def recover_interrupted(self):
        with self.factory() as db:
            db.execute(update(MatchingRun).where(MatchingRun.status.in_(ACTIVE_MATCHING)).values(status='failed',stage='failed',active_user=None,
                error_code='matching_interrupted',completed_at=now(),warnings=['Matching was interrupted by a restart. Retry using your current confirmed profile.']))
            db.commit()

    def reserve(self):
        if not self.capacity.acquire(blocking=False):raise AppError(429,'matching_busy','The matching queue is full. Retry after another run finishes.')

    def submit(self,run_id,jobs):
        try:self.executor.submit(self._run,run_id,jobs)
        except RuntimeError:
            self.capacity.release()
            with self.factory() as db:
                db.execute(update(MatchingRun).where(MatchingRun.id==run_id,MatchingRun.status.in_(ACTIVE_MATCHING)).values(status='failed',stage='failed',active_user=None,error_code='matching_unavailable',completed_at=now()));db.commit()
            raise AppError(503,'matching_unavailable','The matching worker is shutting down. Retry after restarting.') from None

    def _run(self,run_id,jobs):
        started=time.monotonic()
        try:
            with self.factory() as db:
                run=db.get(MatchingRun,run_id)
                if not run or run.status not in ACTIVE_MATCHING:return
                payload={'filters':run.request['filters'],'candidate':run.candidate_snapshot['confirmed_facts'],'jobs':[j.model_dump(mode='json') for j in jobs],
                    'retrieval_pool_size':run.config.get('retrieval_pool_size',self.settings.matching_retrieval_pool_size),'minimum_score':run.request['minimum_score'],'use_ai':run.request['use_ai'],'as_of':run.request['filters']['as_of'],'user_scope':run.user_id}
            def progress(data):
                with self.factory() as db:
                    if data is None:
                        status=db.scalar(select(MatchingRun.status).where(MatchingRun.id==run_id))
                        if status not in ACTIVE_MATCHING:raise MatchingStopped()
                    else:
                        changed=db.execute(update(MatchingRun).where(MatchingRun.id==run_id,MatchingRun.status.in_(ACTIVE_MATCHING)).values(status=data['stage'],stage=data['stage'],tasks=data['tasks']))
                        if changed.rowcount!=1:raise MatchingStopped()
                        db.commit()
            result=execute_matching(payload,self.settings,self.manager,progress)
            progress(None)
            rows=[RankedMatch.model_validate(row) for row in result['results']]
            original={job.id:job.model_dump(mode='json') for job in jobs}
            for row in rows:
                if row.job.id not in original or fingerprint(row.job.model_dump(mode='json'))!=fingerprint(original[row.job.id]):
                    raise ValueError('job_snapshot_changed')
            validation=validate_ranking(rows,result['candidate'],0,result['normalized_jobs'])
            warnings=list(dict.fromkeys(run.warnings+result['warnings']))
            with self.factory() as db:
                changed=db.execute(update(MatchingRun).where(MatchingRun.id==run_id,MatchingRun.status.in_(ACTIVE_MATCHING)).values(
                    status=result['status'],stage=result['status'],active_user=None,candidate_snapshot={'confirmed_facts':payload['candidate'],'normalized':result['candidate']},
                    tasks=result['tasks'],provider_metadata=result['provider_metadata'],warnings=warnings,
                    validation={**run.validation,**validation,'retrieval':result['validation'].get('retrieval',{}),'calibration':result['validation'].get('calibration',{}),'eligible_before_retrieval':len(jobs)-len(result['duplicate_ids']),'outside_retrieval_pool':len(jobs)-len(result['duplicate_ids'])-len(rows),'duplicate_ids':result['duplicate_ids'],'scored_jobs':len(rows),'below_threshold':sum(r.hybrid_score<payload['minimum_score'] for r in rows),'filtered_out':run.total_jobs-len(jobs)},
                    eligible_jobs=len(rows),result_count=sum(r.hybrid_score>=payload['minimum_score'] for r in rows),completed_at=now(),elapsed_ms=round((time.monotonic()-started)*1000)))
                if changed.rowcount!=1:raise MatchingStopped()
                for row in rows:
                    db.add(MatchingResult(run_id=run_id,job_id=row.job.id,rank=row.rank,hybrid_score=row.hybrid_score,baseline_score=row.baseline_score,
                        normalized_job=result['normalized_jobs'][row.job.id],data=row.model_dump(mode='json')))
                db.commit()
        except MatchingStopped:pass
        except Exception as exc:
            code=exc.code if hasattr(exc,'code') and isinstance(exc.code,str) else 'matching_failed'
            with self.factory() as db:
                db.execute(update(MatchingRun).where(MatchingRun.id==run_id,MatchingRun.status.in_(ACTIVE_MATCHING)).values(
                    status='failed',stage='failed',active_user=None,error_code=code,completed_at=now(),elapsed_ms=round((time.monotonic()-started)*1000),
                    warnings=['Matching could not complete. Your profile is unchanged; retry using the current confirmed profile.']))
                db.commit()
        finally:self.capacity.release()

    def shutdown(self):self.executor.shutdown(wait=True,cancel_futures=False)
