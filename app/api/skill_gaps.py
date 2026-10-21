from collections import Counter
from fastapi import APIRouter,Depends,Request,Query
from sqlalchemy import select,update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session,undefer
from app.api.dependencies import current_user,get_db
from app.core.errors import AppError
from app.db.models import SkillGapRun,SkillGapReview,MatchingRun,MatchingResult,User,now
from app.schemas.skill_gaps import GapStart,GapRunResponse,GapReviewInput,ACTIVE_GAPS
from app.services.skill_gaps import candidate_source,gap_run_response
from app.services.jobs import filter_jobs_with_audit
from app.services.gap_taxonomy import vocabulary
from app.services.skill_taxonomy import canonical_skill
from app.services.match_normalization import fingerprint,deduplicate
from app.schemas.jobs import Job

router=APIRouter(prefix='/skill-gaps',tags=['Skill Gap and Career Guidance'])

def owned(db,user_id,run_id):
    run=db.scalar(select(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.user_id==user_id))
    if not run:raise AppError(404,'skill_gap_not_found','Skill analysis was not found in your workspace.')
    return run

def ready_result(run):
    if run.status not in {'completed','partial','needs_review'} or not run.data:raise AppError(409,'skill_gap_not_ready','Skill analysis is not ready. Refresh its status.')

@router.get('/readiness')
def readiness(user:User=Depends(current_user),db:Session=Depends(get_db)):
    profile,source=candidate_source(db,user.id)
    return {'can_analyse':any(bool(v) for v in source['facts'].values()),'profile_revision':profile.revision if profile else None,'confirmed_fields':list(source['facts']),'message':'Only confirmed facts are used. Missing skill evidence remains unknown.'}

@router.post('/runs',response_model=GapRunResponse,status_code=202)
def start(payload:GapStart,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    profile,source=candidate_source(db,user.id)
    if not profile or not any(bool(v) for v in source['facts'].values()):raise AppError(409,'confirmed_profile_required','Review and confirm profile facts before analysing skill gaps.')
    if db.scalar(select(SkillGapRun.id).where(SkillGapRun.active_user==user.id)):raise AppError(409,'skill_gap_in_progress','A skill analysis is already active. Finish or cancel it first.')
    snapshot=request.app.state.job_provider.snapshot();saved_scores={};selection={'mode':payload.mode,'dataset_version':snapshot.version,'reference_date':str(snapshot.reference_date),'corpus_scope':'all dates, deduplicated mock fixture'}
    if payload.mode=='top_ranked' or payload.matching_run_id:
        parent=db.scalar(select(MatchingRun).where(MatchingRun.user_id==user.id,*([MatchingRun.id==payload.matching_run_id] if payload.matching_run_id else [MatchingRun.status.in_(['completed','partial'])])).order_by(MatchingRun.created_at.desc()).limit(1))
        if not parent:raise AppError(409,'matching_required','Complete a matching run first or analyse a target role.')
        if parent.status not in {'completed','partial'}:raise AppError(409,'matching_not_ready','The selected matching run is not ready.')
        query=select(MatchingResult).where(MatchingResult.run_id==parent.id).options(undefer(MatchingResult.data)).order_by(MatchingResult.rank)
        if payload.mode=='top_ranked':query=query.where(MatchingResult.hybrid_score>=parent.request['minimum_score']).limit(payload.top_k)
        else:query=query.where(MatchingResult.job_id.in_(payload.job_ids))
        ranked=db.scalars(query).all();jobs=[Job.model_validate(r.data['job']) for r in ranked];saved_scores={r.job_id:r.hybrid_score for r in ranked}
        if payload.job_ids and len(jobs)!=len(payload.job_ids):raise AppError(404,'job_not_in_matching_run','Some selected jobs are not in this saved matching run.')
        selection.update(matching_run_id=parent.id,matching_profile_revision=parent.profile_revision,source='saved ranked snapshots',saved_score_scope='historical source run; analysis scores are recomputed with current facts')
    elif payload.mode in {'single','selected'}:
        by_id={j.id:j for j in snapshot.jobs}
        if any(id not in by_id for id in payload.job_ids):raise AppError(404,'job_not_found','Some selected mock jobs are unavailable.')
        jobs=[by_id[id] for id in payload.job_ids];selection['source']='explicit fixture selection'
    else:
        jobs,trace=filter_jobs_with_audit(snapshot,payload.filters)
        jobs=jobs[:payload.filters.max_results];selection.update(source='target filters, existing local hybrid reranking; top_k sample',filter_audit=trace,eligible_count=len(jobs))
    jobs,duplicates=deduplicate(jobs);selection['duplicate_ids_excluded']=duplicates
    corpus,corpus_duplicates=deduplicate(snapshot.jobs);required=Counter();preferred=Counter()
    for job in corpus:
        a={canonical_skill(s) for s in job.required_skills};b={canonical_skill(s) for s in job.preferred_skills}-a
        required.update(a);preferred.update(b)
    counts={s:{'required':required[s],'preferred':preferred[s]} for s in set(required)|set(preferred)}
    settings=request.app.state.settings;processor=request.app.state.skill_gap_processor
    processor.reserve()
    run=SkillGapRun(user_id=user.id,active_user=user.id,profile_revision=profile.revision,candidate_hash=fingerprint(source['facts']),candidate_snapshot=source,
        job_snapshot=[j.model_dump(mode='json') for j in jobs],selection=selection,request=payload.model_dump(mode='json'),config={'version':'skill-gap-v1','engine':'crewai.Flow','priority_weights':settings.skill_gap_priority_weights,'matching_weights':settings.matching_weights,'critical_penalty':settings.matching_critical_penalty,'simulation':'fixed text features, raw 0–100 score; no profile mutation'},job_count=min(len(jobs),payload.top_k) if payload.mode=='target' else len(jobs))
    try:db.add(run);db.commit();db.refresh(run)
    except IntegrityError:
        db.rollback();processor.capacity.release();raise AppError(409,'skill_gap_in_progress','Another analysis started; refresh its status.') from None
    except Exception:db.rollback();processor.capacity.release();raise
    processor.submit(run.id,{'corpus_counts':counts,'corpus_count':len(corpus),'vocabulary':vocabulary(corpus),'as_of':str(snapshot.reference_date),'saved_scores':saved_scores})
    return gap_run_response(db,run)

@router.get('/runs',response_model=list[GapRunResponse])
def runs(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [gap_run_response(db,r) for r in db.scalars(select(SkillGapRun).where(SkillGapRun.user_id==user.id).order_by(SkillGapRun.created_at.desc()).limit(20))]

@router.get('/runs/{run_id}',response_model=GapRunResponse)
def status(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):return gap_run_response(db,owned(db,user.id,run_id))

@router.get('/runs/{run_id}/results')
def results(run_id:str,priority:str='',page:int=Query(1,ge=1,le=10000),page_size:int=Query(20,ge=1,le=100),user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id);ready_result(run)
    gaps=[g for g in run.data['gaps'] if not priority or g['priority']==priority]
    reviews={r.gap_id:{'decision':r.decision,'note':r.note,'updated_at':r.updated_at.isoformat()} for r in db.scalars(select(SkillGapReview).where(SkillGapReview.run_id==run_id))}
    return {'run':gap_run_response(db,run),'inventory':run.data['inventory'],'jobs':[{k:v for k,v in j.items() if k!='components' and k!='calculation'} for j in run.data['jobs']],
        'items':gaps[(page-1)*page_size:page*page_size],'total':len(gaps),'page':page,'page_size':page_size,'reviews':reviews,'validation':run.data['validation'],'conflicts':run.data['conflicts'],'selection':run.data['selection']}

@router.get('/runs/{run_id}/audit')
def audit(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id);ready_result(run)
    return {'run':gap_run_response(db,run),'result':run.data,'candidate_snapshot':run.candidate_snapshot,'job_snapshots':[j['job'] for j in run.data['jobs']],'candidate_pool_size':len(run.job_snapshot),'profile_mutated':False}

@router.get('/runs/{run_id}/gaps/{gap_id}')
def evidence(run_id:str,gap_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id);ready_result(run)
    gap=next((g for g in run.data['gaps'] if g['id']==gap_id),None)
    if not gap:raise AppError(404,'gap_not_found','This skill gap is not in the saved analysis.')
    return {'gap':gap,'comparisons':[c for c in run.data['comparisons'] if c['normalized']==gap['normalized']],'profile_revision':run.profile_revision}

@router.put('/runs/{run_id}/gaps/{gap_id}/review')
def review(run_id:str,gap_id:str,payload:GapReviewInput,user:User=Depends(current_user),db:Session=Depends(get_db)):
    evidence(run_id,gap_id,user,db)
    row=db.scalar(select(SkillGapReview).where(SkillGapReview.run_id==run_id,SkillGapReview.gap_id==gap_id))
    if not row:row=SkillGapReview(run_id=run_id,gap_id=gap_id);db.add(row)
    row.decision=payload.decision;row.note=payload.note;row.updated_at=now();db.commit()
    return {'decision':row.decision,'note':row.note,'profile_modified':False}

@router.post('/runs/{run_id}/cancel',response_model=GapRunResponse)
def cancel(run_id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    changed=db.execute(update(SkillGapRun).where(SkillGapRun.id==run_id,SkillGapRun.status.in_(ACTIVE_GAPS)).values(status='cancelled',stage='cancelled',active_user=None,completed_at=now()))
    if changed.rowcount!=1:raise AppError(409,'skill_gap_not_active','This analysis is no longer active.')
    db.commit();db.expire_all();return gap_run_response(db,db.get(SkillGapRun,run_id))

@router.post('/runs/{run_id}/retry',response_model=GapRunResponse,status_code=202)
def retry(run_id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    run=owned(db,user.id,run_id)
    if run.status in ACTIVE_GAPS:raise AppError(409,'skill_gap_in_progress','Cancel or finish the current analysis first.')
    return start(GapStart.model_validate(run.request),request,user,db)
