"""Owned application workflow, tracking, declarative surfaces and replayable AG-UI."""
import asyncio
import json
from fastapi import APIRouter,Depends,Query,Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func,select,or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.dependencies import current_user,get_db
from app.core.errors import AppError
from app.db.models import ApplicationRecord,ApplicationEvent,MatchingRun,MatchingResult,SavedOpportunity,User,new_id
from app.schemas.applications import ApplicationStart,ApplicationAction,ApplicationResponse,TrackingInput,AppToolInput,TaskCompletion
from app.schemas.jobs import Job
from app.services import applications as service
from app.services import application_records as records
from app.db.models import ApplicationNote,InterviewEvent,FollowUpTask,MCPAction,ApplicationStatusHistory,now
from app.services.application_preparation import surface,validate_draft
from app.schemas.applications import ApplicationDraft,DocumentChoice
from app.services.match_normalization import fingerprint
from app.integrations.application_mcp import call,MCPUnavailable

router=APIRouter(prefix='/applications',tags=['Application preparation and tracking'])

@router.get('/readiness')
def readiness(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    try:
        profile,facts=service.source(db,user.id);ready=bool(facts);revision=profile.revision
    except AppError:ready=False;revision=None
    return {'can_prepare':ready,'profile_revision':revision,'providers':[
        {'id':'demo','label':'Local demo MCP app','is_demo':True},
        *([{'id':'configured','label':'Operator-configured stdio adapter','is_demo':False}] if request.app.state.settings.application_mcp_command else [])],
        'documents':service.document_options(db,user.id),
        'message':'Select a saved or recommended job. Every populated field and attachment requires review; submission is always your action.'}

@router.post('',response_model=ApplicationResponse,status_code=201)
def create(payload:ApplicationStart,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    profile,facts=service.source(db,user.id)
    if payload.provider=='configured' and not request.app.state.settings.application_mcp_command:
        raise AppError(409,'application_adapter_unavailable','No external application adapter is configured.')
    job=None;match_context={}
    if payload.matching_run_id:
        parent=db.scalar(select(MatchingRun).where(MatchingRun.user_id==user.id,MatchingRun.id==payload.matching_run_id))
        row=db.scalar(select(MatchingResult).where(MatchingResult.run_id==parent.id,MatchingResult.job_id==payload.job_id)) if parent and parent.status in {'completed','partial'} else None
        if row:
            job=Job.model_validate(row.data['job']);match_context={key:value for key,value in row.data.items() if key in {'scores','hybrid_score','match_percentage','missing_required','missing_preferred','matched_required','missing_skills','matched_skills','score_breakdown'}}
    else:
        saved=db.scalar(select(SavedOpportunity).where(SavedOpportunity.user_id==user.id,SavedOpportunity.job_id==payload.job_id))
        if saved:job=Job.model_validate(saved.job_snapshot)
    if job is None:raise AppError(404,'application_job_unavailable','Select a job from your saved opportunities or an owned completed matching run.')
    if not match_context:match_context={'status':'unavailable','message':'No matching-run score was supplied for this saved opportunity.'}
    identity=fingerprint([job.id,job.source])
    if db.scalar(select(ApplicationRecord.id).where(ApplicationRecord.user_id==user.id,((ApplicationRecord.job_identity==identity)|(ApplicationRecord.job_id==job.id)))):
        raise AppError(409,'duplicate_application','An application for this job already exists. Open it from your applications list.')
    record=ApplicationRecord(user_id=user.id,job_id=job.id,job_identity=identity,job_snapshot=job.model_dump(mode='json'),
        profile_revision=profile.revision,candidate_hash=fingerprint(facts),candidate_snapshot=facts,provider=payload.provider,request=payload.model_dump())
    db.add(record)
    try:
        db.flush();records.workspace(db,record).context=match_context;service.event(db,record,'STATE_SNAPSHOT',message='Job selected. No external action performed.');db.commit()
    except IntegrityError:
        db.rollback();raise AppError(409,'duplicate_application','An application for this job already exists.') from None
    return service.response(db,record)

@router.get('')
def listing(page:int=Query(1,ge=1),page_size:int=Query(12,ge=1,le=50),status:str=Query('',max_length=40),q:str=Query('',max_length=160),sort:str=Query('updated',pattern='^(updated|deadline)$'),user:User=Depends(current_user),db:Session=Depends(get_db)):
    query=select(ApplicationRecord).where(ApplicationRecord.user_id==user.id)
    aliases={'saved':'job_selected','preparation_started':'preparing','user_review':'needs_user_review','ready_to_prefill':'approved_for_prefill','awaiting_submission':'needs_submission_approval'}
    if status:query=query.where(ApplicationRecord.status==aliases.get(status,status))
    if q.strip():query=query.where(or_(ApplicationRecord.job_snapshot['title'].as_string().ilike('%'+q.strip()+'%'),ApplicationRecord.job_snapshot['company'].as_string().ilike('%'+q.strip()+'%')))
    deadline=select(func.min(FollowUpTask.due_at)).where(FollowUpTask.application_id==ApplicationRecord.id,FollowUpTask.completed.is_(False)).correlate(ApplicationRecord).scalar_subquery()
    count=db.scalar(select(func.count()).select_from(query.subquery()))
    rows=db.scalars(query.order_by(*([deadline.is_(None),deadline] if sort=='deadline' else []),ApplicationRecord.updated_at.desc(),ApplicationRecord.id).offset((page-1)*page_size).limit(page_size))
    return {'items':[service.response(db,row) for row in rows],'total':count,'page':page,'page_size':page_size}

@router.get('/schedule')
def schedule(user:User=Depends(current_user),db:Session=Depends(get_db)):
    rows=db.execute(select(FollowUpTask,ApplicationRecord).join(ApplicationRecord,FollowUpTask.application_id==ApplicationRecord.id).where(ApplicationRecord.user_id==user.id).order_by(FollowUpTask.due_at).limit(200)).all()
    return [{'id':task.id,'application_id':record.id,'title':record.job_snapshot.get('title'),'kind':task.kind,'text':task.text,'due_at':records.iso(task.due_at),'completed':task.completed} for task,record in rows]

@router.get('/{id}',response_model=ApplicationResponse)
def get(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):return service.response(db,service.owned(db,user.id,id))

@router.post('/{id}/actions',response_model=ApplicationResponse)
def action(id:str,payload:ApplicationAction,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    if record.revision!=payload.expected_revision:raise AppError(409,'application_revision_conflict','Application changed. Refresh and review the current revision.')
    if payload.action not in service.response(db,record).allowed_actions:raise AppError(409,'application_transition_invalid','This action is not available in the current application state.')
    processor=request.app.state.application_processor
    detail=records.workspace(db,record)
    if payload.action in {'prepare','retry','resume'}:
        if detail.recovery.get('reconcile_required'):raise AppError(409,'application_reconcile_required','Reconcile the previous prefill before starting again.')
        if payload.action=='resume' and not payload.confirmed:raise AppError(422,'application_confirmation_required','Confirm you completed the external step before reconnecting.')
        profile,facts=service.source(db,user.id);operation=new_id();processor.reserve()
        try:
            records.revoke(db,record)
            detail.milestones={key:value for key,value in detail.milestones.items() if key not in {'prepared_at','prefill_completed_at'}}
            detail.recovery={**detail.recovery,'pending':'prepare','user_completed_step':payload.action=='resume' and payload.confirmed,'resume_status':record.status}
            service.change(db,record,payload.expected_revision,status='preparing',stage='preparing',active_operation=operation,
                candidate_snapshot=facts,candidate_hash=fingerprint(facts),profile_revision=profile.revision,
                draft={},approval={},receipt={},tasks=[],warnings=[],error_code=None)
            service.event(db,record,'RUN_STARTED',operation='preparation');db.commit()
        except Exception:db.rollback();processor.capacity.release();raise
        processor.submit(id,operation)
    elif payload.action=='generate_preview':
        operation=new_id();processor.reserve()
        try:
            detail.recovery={**detail.recovery,'pending':'preview'}
            service.change(db,record,payload.expected_revision,active_operation=operation,stage='Generating application preview')
            service.event(db,record,'PREVIEW_GENERATING');db.commit()
        except Exception:db.rollback();processor.capacity.release();raise
        processor.submit(id,operation)
    elif payload.action=='review':
        if detail.recovery.get('reconcile_required'):raise AppError(409,'application_reconcile_required','Reconcile before editing the pending action snapshot.')
        draft=service.reviewed_draft(db,record,payload)
        if record.receipt.get('applied_field_ids') or record.receipt.get('attached_documents'):
            raise AppError(409,'application_external_changes_exist','Completed prefill is preserved. Retry skipped work or prepare a fresh reviewed draft before editing.')
        status=record.status if draft.connection.get('status')!='connected' or record.status in {'waiting_for_user','external_site_unavailable','unsupported_form'} else 'needs_user_review'
        service.change(db,record,payload.expected_revision,status=status,stage=status,draft=draft.model_dump(),approval={},receipt={})
        from app.integrations.application_mcp import preview
        record.draft={**record.draft,'preview':preview(request.app.state.settings,draft,record.candidate_snapshot,record.job_snapshot).model_dump()}
        records.revoke(db,record);records.projection(db,record)
        service.event(db,record,'FORM_SUBMITTED',message='Application review saved locally. Approval is still required.');db.commit()
    elif payload.action=='approve_prefill':
        if not payload.confirmed:raise AppError(422,'application_confirmation_required','Explicitly approve the reviewed fields and selected documents.')
        validation=validate_draft(ApplicationDraft.model_validate(record.draft))
        if not validation['ready_for_prefill']:raise AppError(409,'application_review_incomplete','Complete required fields, fix invalid values and approve every populated field first.')
        service.attachment_packet(db,record)
        service.change(db,record,payload.expected_revision,status='approved_for_prefill',stage='approved_for_prefill',
            approval={'digest':service.approval_hash(record),'approved_revision':record.revision,'scope':'prefill only; no submission','provider':record.provider})
        records.approve(db,record,'prefill')
        service.event(db,record,'TRANSACTION_STATE',message='User approved this exact prefill snapshot.');db.commit()
    elif payload.action in {'prefill','retry_prefill'}:
        if not payload.confirmed:raise AppError(422,'application_confirmation_required','Explicitly start the approved prefill operation.')
        if record.approval.get('digest')!=service.approval_hash(record):raise AppError(409,'application_approval_stale','Draft changed; approve its current revision again.')
        if detail.recovery.get('reconcile_required'):raise AppError(409,'application_reconcile_required','Reconcile before retrying an uncertain external action.')
        if payload.action=='retry_prefill' and any(not item.get('retryable') for item in record.receipt.get('skipped_fields',[])+record.receipt.get('failed_documents',[])):
            raise AppError(409,'application_manual_step_required','A skipped control requires a manual step.')
        service.attachment_packet(db,record);operation=new_id();processor.reserve()
        try:
            detail.recovery={**detail.recovery,'pending':'prefill','last_operation':operation}
            service.change(db,record,payload.expected_revision,status='prefilling',stage='prefilling',active_operation=operation)
            service.event(db,record,'RETRY' if payload.action=='retry_prefill' else 'RUN_STARTED',operation='prefill')
            service.event(db,record,'TOOL_CALL_START',tool='prefill_application');db.commit()
        except Exception:db.rollback();processor.capacity.release();raise
        processor.submit(id,operation)
    elif payload.action in {'open_external','reconcile'}:
        if not payload.confirmed:raise AppError(422,'application_confirmation_required','Confirm this application action.')
        operation=new_id();processor.reserve()
        try:
            detail.recovery={**detail.recovery,'pending':'open' if payload.action=='open_external' else 'reconcile','resume_status':record.status}
            service.change(db,record,payload.expected_revision,active_operation=operation,stage='Connecting to external form' if payload.action=='open_external' else 'Reconciling previous action')
            service.event(db,record,'RUN_STARTED',operation=payload.action);db.commit()
        except Exception:db.rollback();processor.capacity.release();raise
        processor.submit(id,operation)
    elif payload.action in {'approve_submission','continue_manually'}:
        if not payload.confirmed:raise AppError(422,'application_confirmation_required','Confirm you will review the external form and submit it yourself.')
        service.change(db,record,payload.expected_revision,status='needs_submission_approval',stage='needs_submission_approval')
        records.approve(db,record,'manual_review' if payload.action=='continue_manually' else 'submission_review')
        service.event(db,record,'USER_APPROVAL_REQUEST',message='Awaiting user-performed submission; no submit tool exists.');db.commit()
    elif payload.action=='record_submission':
        if not payload.confirmed:raise AppError(422,'application_confirmation_required','Explicitly confirm that you performed the submission, or completed the demo.')
        service.change(db,record,payload.expected_revision,status='submitted_by_user',stage='submitted_by_user',
            receipt={**record.receipt,'submission':{'source':'user_reported','reference':payload.reference,'is_demo':record.provider=='demo'}})
        records.milestone(db,record,'submitted_by_user_at');records.approve(db,record,'submission_report')
        service.event(db,record,'TRANSACTION_STATE',message='Demo completion reported by user.' if record.provider=='demo' else 'External submission reported by user; not independently verified.',reference=payload.reference)
        db.commit()
    elif payload.action=='start_tracking':
        service.change(db,record,payload.expected_revision,status='tracking_active',stage='tracking_active')
        records.milestone(db,record,'tracking_started_at')
        service.event(db,record,'TRANSACTION_STATE',message='Application tracking activated.');db.commit()
    elif payload.action in {'cancel','pause'}:
        pending=detail.recovery.get('pending');operation=record.active_operation
        status='cancelled' if payload.action=='cancel' else 'waiting_for_user'
        detail.recovery={**detail.recovery,'pending':None,'resume_status':record.status,
            'last_operation':operation if pending=='prefill' else detail.recovery.get('last_operation'),
            'reconcile_required':pending=='prefill' or detail.recovery.get('reconcile_required',False)}
        service.change(db,record,payload.expected_revision,status=status,stage=status,active_operation=None)
        attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation)) if operation else None
        if attempt:attempt.status=status;attempt.completed_at=now()
        service.event(db,record,'RUN_FINISHED',message='Workflow paused/cancelled. Existing external work is preserved. Reconcile any uncertain prefill before retrying.');db.commit()
    db.expire(record);db.refresh(record)
    return service.response(db,record)

@router.post('/{id}/tracking',response_model=ApplicationResponse)
def tracking(id:str,payload:TrackingInput,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    if record.active_operation:raise AppError(409,'application_in_progress','Wait for preparation/prefill to finish before updating tracking.')
    if payload.kind=='status':
        if payload.status not in service.TRANSITIONS.get(record.status,set()):raise AppError(409,'application_transition_invalid','This status transition is not supported.')
        values={'status':payload.status,'stage':payload.status}
    else:values={}
    if payload.kind=='interview' and record.status not in {'tracking_active','recruiter_review','assessment','interview','offer'}:raise AppError(409,'application_not_tracking','Activate tracking before recording interviews.')
    if payload.kind=='interview' and record.status in {'tracking_active','recruiter_review','assessment'}:values={'status':'interview','stage':'interview'}
    service.change(db,record,payload.expected_revision,**values)
    if payload.kind=='note':db.add(ApplicationNote(application_id=id,text=payload.text))
    elif payload.kind=='interview':db.add(InterviewEvent(application_id=id,stage=payload.text or 'Interview',scheduled_at=payload.due_at))
    elif payload.kind in {'deadline','follow_up'}:db.add(FollowUpTask(application_id=id,kind=payload.kind,text=payload.text,due_at=payload.due_at))
    elif payload.kind=='status' and payload.text:
        detail=records.workspace(db,record);detail.context={**detail.context,'outcome_details':payload.text}
    service.event(db,record,'TRANSACTION_STATE',kind=payload.kind,text=payload.text,due_at=payload.due_at.isoformat() if payload.due_at else None)
    db.commit();return service.response(db,record)

@router.get('/{id}/audit')
def audit(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    data=records.audit(db,record)
    data['status_history']=[{'previous':r.previous,'status':r.status,'revision':r.revision,'created_at':records.iso(r.created_at)} for r in db.scalars(select(ApplicationStatusHistory).where(ApplicationStatusHistory.application_id==id).order_by(ApplicationStatusHistory.revision))]
    return data

@router.post('/{id}/tasks/{task_id}/complete',response_model=ApplicationResponse)
def complete_task(id:str,task_id:str,payload:TaskCompletion,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    if record.active_operation:raise AppError(409,'application_in_progress','Wait for the operation before updating tasks.')
    task=db.scalar(select(FollowUpTask).where(FollowUpTask.id==task_id,FollowUpTask.application_id==id))
    if not task:raise AppError(404,'application_task_unavailable','Follow-up task was not found.')
    service.change(db,record,payload.expected_revision);task.completed=True
    service.event(db,record,'TRANSACTION_STATE',kind='task_completed');db.commit()
    return service.response(db,record)

@router.get('/{id}/timeline')
def timeline(id:str,after:int=Query(0,ge=0),user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    return [{'seq':e.seq,'type':e.type,'payload':e.payload,'created_at':e.created_at} for e in db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id==record.id,ApplicationEvent.seq>after).order_by(ApplicationEvent.seq).limit(1000))]

@router.get('/{id}/surface')
def declarative_surface(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    result=surface(record);result['preview']=service.response(db,record).draft.preview.model_dump() if record.draft else None
    return result

@router.get('/{id}/mcp/resource')
def resource(id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    try:return call(request.app.state.settings,record.provider,'resource')
    except MCPUnavailable:raise AppError(503,'application_app_unavailable','The MCP interface is unavailable. Your local review form remains available.') from None

@router.get('/{id}/mcp/tools')
def tools(id:str,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    try:
        result=call(request.app.state.settings,record.provider,'discover')
        # Tools advertised by an adapter are not automatically callable by a frame.
        return {**result,'host_allowed_tools':['application_state','review_application'],'submission_supported':False}
    except MCPUnavailable:raise AppError(503,'application_mcp_unavailable','Application tools could not be discovered. Retry when ready.') from None

@router.post('/{id}/mcp/call')
def relay(id:str,payload:AppToolInput,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    record=service.owned(db,user.id,id)
    if payload.name=='review_application':
        # No arbitrary pass-through. Approvals/prefill/submission are host buttons.
        documents=[DocumentChoice(document_id=d['document_id'],role=d['role'],approved=True) for d in record.draft.get('documents',[])]
        action(id,ApplicationAction(action='review',expected_revision=payload.expected_revision,fields=payload.fields,documents=documents),request,user,db)
        record=service.owned(db,user.id,id)
    result=surface(record,approved_only=record.provider!='demo')
    result['preview']=service.response(db,record).draft.preview.model_dump() if record.draft else None
    return {'content':[{'type':'text','text':json.dumps(result)}],'isError':False}

@router.get('/{id}/events')
async def events(id:str,request:Request,after:int=Query(0,ge=0),follow:bool=True,user:User=Depends(current_user),db:Session=Depends(get_db)):
    service.owned(db,user.id,id)
    factory=request.app.state.session_factory
    async def stream():
        cursor=after;end=asyncio.get_running_loop().time()+25
        while True:
            with factory() as connection:
                rows=connection.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id==id,ApplicationEvent.seq>cursor).order_by(ApplicationEvent.seq).limit(1000)).all()
            for e in rows:
                cursor=e.seq
                wire={'type':e.type,'run_id':id,'seq':e.seq,'payload':e.payload,'timestamp':e.created_at.isoformat()}
                yield f'id: {e.seq}\nevent: {e.type}\ndata: {json.dumps(wire)}\n\n'
            if not follow or await request.is_disconnected() or asyncio.get_running_loop().time()>=end:break
            yield ': keepalive\n\n'
            await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
