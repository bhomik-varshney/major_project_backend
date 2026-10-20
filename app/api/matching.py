from typing import Literal
from fastapi import APIRouter,Depends,Query,Request
from sqlalchemy import select,update,delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session,undefer

from app.api.dependencies import current_user,get_db
from app.core.errors import AppError
from app.db.models import MatchingRun,MatchingResult,SavedOpportunity,User,now
from app.schemas.matching import MatchingStart,MatchingRunResponse,MatchingPage,RankedMatch,ACTIVE_MATCHING,MatchingReadiness,MatchingAudit,MatchingDetail,MatchingExplanation,SavedMatch
from app.services.match_normalization import fingerprint,canonical_skill
from app.services.matching import readiness,run_response,filtered_snapshot

router=APIRouter(prefix='/matching',tags=['Hybrid matching'])


def owned(db,user_id,id):
    run=db.scalar(select(MatchingRun).where(MatchingRun.id==id,MatchingRun.user_id==user_id))
    if not run:raise AppError(404,'matching_not_found','This matching run was not found in your workspace.')
    return run


@router.get('/readiness',response_model=MatchingReadiness)
def get_readiness(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return readiness(db,user.id)[0]


@router.post('/runs',response_model=MatchingRunResponse,status_code=202)
def start_run(payload:MatchingStart,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    ready,facts=readiness(db,user.id)
    if not ready['can_match']:raise AppError(409,'confirmed_profile_required',ready['message'])
    if db.scalar(select(MatchingRun.id).where(MatchingRun.active_user==user.id)):
        raise AppError(409,'matching_in_progress','A matching run is already active. Wait for it to finish or cancel it.')
    snapshot=request.app.state.job_provider.snapshot()
    filters=payload.filters.model_copy(update={'page':1,'page_size':50})
    jobs=filtered_snapshot(snapshot,filters)
    settings=request.app.state.settings;minimum=settings.matching_minimum_score if payload.minimum_score is None else payload.minimum_score
    config={'weights':settings.matching_weights,'critical_penalty_max':settings.matching_critical_penalty,'unknown_ceiling_base':settings.matching_unknown_ceiling_base,
        'baseline':'tfidf-word-bigram-logtf-smoothidf-v1','version':'hybrid-v1','engine':'crewai.Flow','ai_top_k':settings.matching_ai_top_k}
    processor=request.app.state.matching_processor;processor.reserve()
    run=MatchingRun(user_id=user.id,profile_revision=ready['profile_revision'],candidate_hash=fingerprint(facts),active_user=user.id,
        candidate_snapshot={'confirmed_facts':facts},dataset_version=snapshot.version,dataset_reference_date=str(snapshot.reference_date),total_jobs=len(snapshot.jobs),
        request={'filters':filters.model_dump(mode='json'),'minimum_score':minimum,'use_ai':payload.use_ai,'allow_external_processing':payload.allow_external_processing},
        config=config,warnings=list(snapshot.warnings))
    try:db.add(run);db.commit();db.refresh(run)
    except IntegrityError:
        db.rollback();processor.capacity.release()
        raise AppError(409,'matching_in_progress','Another matching run started. Refresh to see it.') from None
    except Exception:
        db.rollback();processor.capacity.release();raise
    processor.submit(run.id,jobs)
    return run_response(db,run)


@router.get('/runs',response_model=list[MatchingRunResponse])
def list_runs(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [run_response(db,r) for r in db.scalars(select(MatchingRun).where(MatchingRun.user_id==user.id).order_by(MatchingRun.created_at.desc()).limit(20))]


@router.get('/runs/{run_id}',response_model=MatchingRunResponse)
def status(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    return run_response(db,owned(db,user.id,run_id))


@router.get('/runs/{run_id}/results',response_model=MatchingPage)
def results(run_id:str,page:int=Query(1,ge=1,le=10000),page_size:int=Query(6,ge=1,le=50),keyword:str=Query('',max_length=120),
    category:str=Query('',max_length=80),work_mode:Literal['remote','hybrid','onsite']|None=None,country:str=Query('',max_length=80),
    skill:list[str]=Query(default=[],max_length=20),minimum_score:float|None=Query(None,ge=0,le=100),sort:Literal['hybrid','baseline']='hybrid',saved_only:bool=False,
    user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    if run.status not in {'completed','partial'}:raise AppError(409,'matching_not_ready','Ranked results are not ready. Check the run status or retry.')
    threshold=max(run.request['minimum_score'],minimum_score or 0)
    rows=db.scalars(select(MatchingResult).where(MatchingResult.run_id==run_id,MatchingResult.hybrid_score>=threshold).options(undefer(MatchingResult.data))).all()
    saved=set(db.scalars(select(SavedOpportunity.job_id).where(SavedOpportunity.user_id==user.id))) if saved_only else set()
    selected=[];wanted={canonical_skill(s) for s in skill if s.strip()}
    for row in rows:
        job=row.data['job']
        if keyword.strip().casefold() not in ' '.join([job['title'],job['company'],job['description']]).casefold():continue
        if category and job['category'].casefold()!=category.casefold():continue
        if country and job['country'].casefold()!=country.casefold():continue
        if work_mode and job['work_mode']!=work_mode:continue
        if not wanted.issubset({canonical_skill(s) for s in job['required_skills']+job['preferred_skills']}):continue
        if saved_only and row.job_id not in saved:continue
        selected.append(row)
    selected.sort(key=(lambda r:(-r.hybrid_score,-r.baseline_score,r.job_id)) if sort=='hybrid' else (lambda r:(-r.baseline_score,-r.hybrid_score,r.job_id)))
    start=(page-1)*page_size
    return MatchingPage(run=run_response(db,run),items=[RankedMatch.model_validate(r.data) for r in selected[start:start+page_size]],total=len(selected),page=page,page_size=page_size)


@router.get('/runs/{run_id}/audit',response_model=MatchingAudit)
def audit(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    return {'run':run_response(db,run),'candidate_snapshot':run.candidate_snapshot,'profile_source':'confirmed_fields_only','draft_data_used':False}


@router.get('/runs/{run_id}/results/{job_id}',response_model=MatchingDetail)
def detail(run_id:str,job_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    row=db.scalar(select(MatchingResult).where(MatchingResult.run_id==run_id,MatchingResult.job_id==job_id))
    if not row:raise AppError(404,'recommendation_not_found','This opportunity is not in the saved matching run.')
    return {'run':run_response(db,run),'recommendation':RankedMatch.model_validate(row.data),'normalized_job':row.normalized_job,'candidate_snapshot':run.candidate_snapshot}


@router.get('/runs/{run_id}/results/{job_id}/explanation',response_model=MatchingExplanation)
def explanation(run_id:str,job_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    data=detail(run_id,job_id,user,db);row=data['recommendation']
    return {'job_id':job_id,'score':row.hybrid_score,'text':row.explanation,'method':row.explanation_method,'reasons':row.reasons,'highlighted_reason_ids':row.highlighted_reason_ids,'stale':data['run'].stale}


@router.post('/runs/{run_id}/cancel',response_model=MatchingRunResponse)
def cancel(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    changed=db.execute(update(MatchingRun).where(MatchingRun.id==run.id,MatchingRun.status.in_(ACTIVE_MATCHING)).values(status='cancelled',stage='cancelled',active_user=None,completed_at=now()))
    if changed.rowcount!=1:raise AppError(409,'matching_not_active','This run has already finished.')
    db.commit();db.expire_all();return run_response(db,db.get(MatchingRun,run_id))


@router.post('/runs/{run_id}/retry',response_model=MatchingRunResponse,status_code=202)
def retry(run_id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    if run.status in ACTIVE_MATCHING:raise AppError(409,'matching_in_progress','Cancel or finish the current run first.')
    # Current confirmed facts are reloaded; stale private snapshots never override them.
    return start_run(MatchingStart.model_validate(run.request),request,user,db)


@router.delete('/runs/{run_id}',status_code=204)
def remove_run(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    if run.status in ACTIVE_MATCHING:raise AppError(409,'matching_in_progress','Cancel the active run before removing its history.')
    db.delete(run);db.commit()


@router.get('/bookmarks',response_model=list[SavedMatch])
def bookmarks(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [{'job':row.job_snapshot,'saved_at':row.created_at} for row in db.scalars(select(SavedOpportunity).where(SavedOpportunity.user_id==user.id).order_by(SavedOpportunity.created_at.desc()))]


@router.put('/bookmarks/{job_id}')
def bookmark(job_id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    job=next((j for j in request.app.state.job_provider.snapshot().jobs if j.id==job_id),None)
    if not job:raise AppError(404,'opportunity_not_found','This seeded opportunity is not available.')
    if not db.scalar(select(SavedOpportunity.id).where(SavedOpportunity.user_id==user.id,SavedOpportunity.job_id==job_id)):
        db.add(SavedOpportunity(user_id=user.id,job_id=job_id,job_snapshot=job.model_dump(mode='json')))
        try:db.commit()
        except IntegrityError:db.rollback()
    return {'job_id':job_id,'saved':True}


@router.delete('/bookmarks/{job_id}',status_code=204)
def unbookmark(job_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    db.execute(delete(SavedOpportunity).where(SavedOpportunity.user_id==user.id,SavedOpportunity.job_id==job_id));db.commit()
