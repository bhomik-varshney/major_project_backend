"""One canonical application state machine. No path performs external submission."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from threading import BoundedSemaphore
from sqlalchemy import func,select,update
from app.core.errors import AppError
from app.db.models import (ApplicationRecord,ApplicationEvent,Document,VerificationRecord,new_id,now,
    ApplicationWorkspace,ApplicationStatusHistory,ExternalApplicationSession,MCPAction,CoverLetterVersion,TailoredResumeVersion)
from app.services import application_records as records
from app.repositories.profiles import find_profile
from app.schemas.applications import ApplicationDraft,ApplicationResponse
from app.services.profiles import confirmed_fields
from app.services.match_normalization import fingerprint
from app.services.application_preparation import valid_value,validate_draft
from app.orchestration.applications import execute_application

EDITABLE={'draft_ready','needs_user_review','approved_for_prefill','prefill_complete','needs_submission_approval','partially_completed','waiting_for_user','external_site_unavailable','unsupported_form'}
TRACKING={'recruiter_review','assessment','submitted_by_user','tracking_active','interview','offer','accepted','rejected','withdrawn','closed'}
TRANSITIONS={'submitted_by_user':{'recruiter_review','assessment','interview','rejected','withdrawn','closed'},
    'tracking_active':{'recruiter_review','assessment','interview','offer','rejected','withdrawn','closed'},
    'recruiter_review':{'assessment','interview','offer','rejected','withdrawn','closed'},'assessment':{'assessment','interview','offer','rejected','withdrawn','closed'},
    'interview':{'assessment','interview','offer','rejected','withdrawn','closed'},'offer':{'accepted','rejected','withdrawn','closed'},
    'accepted':{'closed'},'rejected':{'closed'},'withdrawn':{'closed'},'closed':set()}


def source(db,user_id):
    profile=find_profile(db,user_id)
    facts=confirmed_fields(db,profile) if profile else {}
    if not profile or not any(bool(v) for v in facts.values()):raise AppError(409,'confirmed_profile_required','Confirm your profile facts before preparing an application.')
    return profile,facts


def document_options(db,user_id):
    confirmed=set(db.scalars(select(VerificationRecord.document_id).where(VerificationRecord.user_id==user_id,VerificationRecord.document_id.is_not(None))))
    generated={c.document_id:c for c in db.scalars(select(CoverLetterVersion).where(CoverLetterVersion.user_id==user_id,CoverLetterVersion.document_id.is_not(None)))}
    tailored={r.document_id:r for r in db.scalars(select(TailoredResumeVersion).where(TailoredResumeVersion.user_id==user_id,TailoredResumeVersion.document_id.is_not(None)))}
    return [{'id':d.id,'filename':d.filename,'sha256':d.sha256,'content_type':d.content_type,'size_bytes':d.size_bytes,
        'confirmed_resume':d.id in confirmed or d.id in tailored and tailored[d.id].status in {'approved','attached'},'automatic_attachment':False,
        **({'tailored_resume_id':tailored[d.id].id,'tailored_application_id':tailored[d.id].application_id,'tailored_status':tailored[d.id].status} if d.id in tailored else {}),
        **({'cover_letter_id':generated[d.id].id,'cover_application_id':generated[d.id].application_id,'cover_job_id':generated[d.id].job_snapshot['id'],
            'cover_status':generated[d.id].status} if d.id in generated else {})}
        for d in db.scalars(select(Document).where(Document.user_id==user_id).order_by(Document.created_at.desc()).limit(100))]


def owned(db,user_id,id):
    record=db.scalar(select(ApplicationRecord).where(ApplicationRecord.id==id,ApplicationRecord.user_id==user_id))
    if not record:raise AppError(404,'application_not_found','This application was not found in your workspace.')
    return record


def is_stale(db,record):
    profile=find_profile(db,record.user_id)
    return not profile or profile.revision!=record.profile_revision or fingerprint(confirmed_fields(db,profile))!=record.candidate_hash


def response(db,record):
    stale=is_stale(db,record)
    actions=[]
    detail=db.get(ApplicationWorkspace,record.id)
    recovery=detail.recovery if detail else {}
    connected=record.draft.get('connection',{}).get('status','connected')=='connected'
    if record.active_operation:actions=['pause','cancel']
    elif record.status=='job_selected':actions=['prepare','cancel']
    elif record.status in {'failed','cancelled'}:
        actions=['retry']
        if recovery.get('reconcile_required'):actions=['reconcile','cancel']
        elif record.draft:actions+=['review','continue_manually']
    elif record.status=='duplicate_application':actions=['cancel']
    elif record.status in EDITABLE:
        actions=['review','prepare','cancel']
        if record.status in {'waiting_for_user','external_site_unavailable','unsupported_form'}:actions+=['resume','continue_manually']
        if record.status=='partially_completed':actions+=['continue_manually']
        if recovery.get('reconcile_required'):actions+=['reconcile']
        if record.draft:actions+=['open_external','pause']
        if record.receipt.get('applied_field_ids') or record.receipt.get('attached_documents') or recovery.get('reconcile_required'):
            actions=[a for a in actions if a not in ({'review','prepare','resume'} if recovery.get('reconcile_required') or record.status=='partially_completed' else {'review'})]
        if not stale:
            if record.status in {'draft_ready','needs_user_review'} and connected:actions+=['approve_prefill']
            if record.status=='approved_for_prefill':actions+=['prefill']
            if record.status=='partially_completed' and not recovery.get('reconcile_required') and record.approval.get('digest')==approval_hash(record) and all(item.get('retryable') for item in record.receipt.get('skipped_fields',[])+record.receipt.get('failed_documents',[])):actions+=['retry_prefill']
        if record.status=='prefill_complete':actions+=['approve_submission']
        if record.status=='needs_submission_approval':actions+=['record_submission']
    elif record.status=='submitted_by_user':actions=['start_tracking']
    if record.draft and record.draft.get('presentation_version')!='1.0':actions=[a for a in actions if a not in {'approve_prefill','prefill','retry_prefill'}]
    if record.draft and not record.active_operation and not recovery.get('reconcile_required'):actions+=['generate_preview']
    presented=ApplicationDraft.model_validate(record.draft) if record.draft else None
    if presented:
        from app.services.application_transform import build_preview
        if not presented.preview:presented.preview=build_preview(presented,record.candidate_snapshot,record.job_snapshot,'legacy_local')
        else:presented.preview=presented.preview.model_copy(deep=True)
        applied=set(record.receipt.get('applied_field_ids',[]));skipped={item['field_id'] for item in record.receipt.get('skipped_fields',[])}
        for section in presented.preview.sections:
            for field in section.fields:
                if field.field_id in applied:field.status='completed'
                elif field.field_id in skipped:field.status='failed';field.reason='Prefill was skipped. Review recovery details before retrying.'
                elif record.status=='prefilling' and field.approved:field.status='prefilling'
        by_id={f.field_id:f for section in presented.preview.sections for f in section.fields}
        for mapping in presented.preview.mappings:mapping.status=by_id[mapping.field_id].status
        presented.preview.content_hash=fingerprint(presented.preview.model_dump(exclude={'content_hash'}))
    return ApplicationResponse(id=record.id,revision=record.revision,job_id=record.job_id,job=record.job_snapshot,
        status=record.status,stage=record.stage,provider=record.provider,is_demo=record.provider=='demo',
        profile_revision=record.profile_revision,stale=stale,draft=presented,
        approval=record.approval,receipt=record.receipt,tasks=record.tasks,warnings=record.warnings,error_code=record.error_code,
        allowed_actions=actions,created_at=record.created_at,updated_at=record.updated_at,
        milestones=detail.milestones if detail else {},recovery=recovery,match_context=detail.context if detail else {},
        tracking=records.tracking_snapshot(db,record),operation_active=bool(record.active_operation),lifecycle_status=lifecycle(record.status))


def event(db,record,type,**payload):
    seq=(db.scalar(select(func.max(ApplicationEvent.seq)).where(ApplicationEvent.application_id==record.id)) or 0)+1
    db.add(ApplicationEvent(application_id=record.id,seq=seq,type=type,
        payload={'status':record.status,'revision':record.revision,**payload}))


def change(db,record,expected,**values):
    previous=record.status
    changed=db.execute(update(ApplicationRecord).where(ApplicationRecord.id==record.id,ApplicationRecord.revision==expected)
        .values(**values,revision=expected+1,updated_at=now()).execution_options(synchronize_session=False))
    if changed.rowcount!=1:raise AppError(409,'application_revision_conflict','Application changed. Refresh and review the current revision.')
    db.expire(record);db.refresh(record)
    if previous!=record.status:db.add(ApplicationStatusHistory(application_id=record.id,previous=previous,status=record.status,revision=record.revision))


def reviewed_draft(db,record,payload):
    draft=ApplicationDraft.model_validate(record.draft);known={f.id:f for f in draft.form.fields}
    if set(payload.fields)-set(known):raise AppError(422,'application_field_unknown','The form contains an unsupported field ID.')
    for field in draft.fields:
        if field.id not in payload.fields:continue
        spec=known[field.id];review=payload.fields[field.id]
        if spec.type=='file':raise AppError(422,'application_file_field','Use the approved document selector for attachments.')
        if review.value is not None and review.value!='' and review.value!=[] and not valid_value(spec,review.value):raise AppError(422,'application_field_invalid','A supplied value is incompatible with the application field.')
        if field.value!=review.value:
            field.source='user_input';field.evidence='Entered explicitly for this application; not added to your profile.'
            field.confidence='unknown';field.edited_by_user=True
            field.warning='User-supplied application value. Review that it is current and appropriate before prefill.'
            field.auto_filled=False
        field.value=deepcopy(review.value);field.approved=review.approved
        if field.edited_by_user:field.classification='user_confirmation_required'
    options={d['id']:d for d in document_options(db,record.user_id)};seen=set();attachments=[]
    for choice in payload.documents:
        document=options.get(choice.document_id)
        if not document or choice.document_id in seen:raise AppError(422,'application_document_invalid','Select an available document once.')
        if document.get('tailored_resume_id'):
            tailored=db.get(TailoredResumeVersion,document['tailored_resume_id'])
            if tailored.application_id!=record.id or tailored.status not in {'approved','attached'} or choice.role!='resume' or tailored.candidate_hash!=record.candidate_hash or tailored.job_hash!=fingerprint(record.job_snapshot):
                raise AppError(409,'application_tailored_resume_mismatch','Choose the current approved tailored resume for this exact application and profile, with the resume role.')
        if document.get('cover_letter_id') and (document['cover_application_id']!=record.id or document['cover_status'] not in {'approved','attached'} or choice.role!='cover_letter'):
            raise AppError(409,'application_cover_mismatch','Select the current approved cover letter for this exact application, with the cover-letter role.')
        if choice.role=='resume' and not document['confirmed_resume']:raise AppError(409,'application_resume_unconfirmed','Confirm the selected resume extraction before using it as your resume.')
        seen.add(choice.document_id)
        attachments.append({**document,'document_id':document['id'],'job_id':record.job_id,'role':choice.role,'approved':True})
    draft.documents=attachments;draft.document_options=list(options.values());draft.presentation_version="1.0"
    draft.validation=validate_draft(draft);draft.missing_inputs=draft.validation['missing_required']
    return draft


def approval_hash(record):return fingerprint({'draft':{key:value for key,value in record.draft.items() if key!='preview'},'job_id':record.job_id,'candidate_hash':record.candidate_hash,'provider':record.provider})


def attachment_packet(db,record):
    documents=[]
    if sum(d['size_bytes'] for d in record.draft['documents'])>5*1024*1024:
        raise AppError(422,'application_attachment_limit','Approved attachments must total at most 5 MB for this stdio adapter.')
    options={d['id']:d for d in document_options(db,record.user_id)}
    for selected in record.draft['documents']:
        current=options.get(selected['document_id'])
        if current and current.get('tailored_resume_id'):
            tailored=db.get(TailoredResumeVersion,current['tailored_resume_id'])
            if tailored.application_id!=record.id or tailored.status not in {'approved','attached'} or selected['role']!='resume' or tailored.candidate_hash!=record.candidate_hash or tailored.job_hash!=fingerprint(record.job_snapshot):
                raise AppError(409,'application_tailored_resume_mismatch','The tailored resume is stale, replaced, or belongs to another application.')
        if current and current.get('cover_letter_id'):
            cover=db.get(CoverLetterVersion,current['cover_letter_id'])
            if cover.application_id!=record.id or cover.status not in {'approved','attached'} or selected['role']!='cover_letter' or cover.candidate_hash!=record.candidate_hash:
                raise AppError(409,'application_cover_mismatch','Cover letter is stale, replaced, or belongs to another application. Review current documents again.')
        if (not selected['approved'] or selected['job_id']!=record.job_id or not current or
            current['sha256']!=selected['sha256'] or selected['role']=='resume' and not current['confirmed_resume']):
            raise AppError(409,'application_attachment_changed','An approved document is no longer available or confirmed. Review documents again.')
        document=db.get(Document,selected['document_id'])
        if sha256(document.content).hexdigest()!=document.sha256:raise AppError(409,'application_document_invalid','Document integrity changed. Select the original again.')
        documents.append({'document_id':document.id,'role':selected['role'],'filename':document.filename,
            'sha256':document.sha256,'content_type':document.content_type,'content_base64':base64.b64encode(document.content).decode()})
    return documents


def receipt_valid(record,packet,receipt,expected_digest=None):
    expected_fields=set(packet['values'])|{f['field_id'] for f in packet.get('completed_fields',[])}
    expected_docs={d['document_id']:d['sha256'] for d in packet['documents']+packet.get('completed_documents',[])}
    applied=receipt.get('applied_field_ids',[]);skipped=receipt.get('skipped_fields',[])
    attached=receipt.get('attached_documents',[]);failed=receipt.get('failed_documents',[])
    skipped_ids=[s.get('field_id') for s in skipped];doc_ids=[d.get('document_id') for d in attached+failed]
    return (receipt.get('accepted') is True and receipt.get('submitted') is False and receipt.get('is_demo')==(record.provider=='demo') and
        receipt.get('job_id')==record.job_id and receipt.get('operation_id')==packet['operation_id'] and receipt.get('packet_digest')==(expected_digest or fingerprint(packet)) and
        len(applied)==len(set(applied)) and len(skipped_ids)==len(set(skipped_ids)) and not set(applied)&set(skipped_ids) and
        set(applied)|set(skipped_ids)==expected_fields and len(doc_ids)==len(set(doc_ids)) and set(doc_ids)==set(expected_docs) and
        all(d.get('sha256')==expected_docs.get(d.get('document_id')) for d in attached+failed) and
        all(s.get('code') in {'temporary_control_unavailable','upload_failed','interrupted','unsupported_field','manual_step_required'} and isinstance(s.get('retryable'),bool) for s in skipped+failed) and
        (not skipped and not failed or receipt.get('outcome')=='partial'))


def clean_receipt(receipt):
    # Adapter prose/unknown keys are never persisted or echoed to a browser.
    keys={'accepted','submitted','is_demo','job_id','operation_id','packet_digest','outcome','applied_field_ids','attached_documents','skipped_fields','failed_documents'}
    result={k:v for k,v in receipt.items() if k in keys}
    result['attached_documents']=[{k:d[k] for k in ('document_id','sha256')} for d in receipt.get('attached_documents',[])]
    result['skipped_fields']=[{k:d[k] for k in ('field_id','code','retryable')} for d in receipt.get('skipped_fields',[])]
    result['failed_documents']=[{k:d[k] for k in ('document_id','sha256','code','retryable')} for d in receipt.get('failed_documents',[])]
    return result


def failure_status(code):
    if code in {'authentication_required','external_session_expired'}:return 'waiting_for_user'
    if code in {'external_page_unavailable','navigation_failed'}:return 'external_site_unavailable'
    if code in {'unsupported_form','form_schema_changed','application_response_invalid'}:return 'unsupported_form'
    if code=='duplicate_application':return 'duplicate_application'
    return 'failed'


class ApplicationStopped(Exception):pass


class ApplicationProcessor:
    def __init__(self,factory,settings,manager):
        self.factory,self.settings,self.manager=factory,settings,manager
        self.capacity=BoundedSemaphore(4);self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='applications')
    def reserve(self):
        if not self.capacity.acquire(blocking=False):raise AppError(429,'application_busy','Application queue is full. Try again shortly.')
    def submit(self,id,operation):
        try:self.executor.submit(self._run,id,operation)
        except RuntimeError:
            self.capacity.release();self.fail(id,operation,'application_worker_unavailable')
            raise AppError(503,'application_worker_unavailable','Application worker is shutting down.') from None
    def fail(self,id,operation,code):
        with self.factory() as db:
            record=db.get(ApplicationRecord,id)
            if not record or record.active_operation!=operation:return
            detail=records.workspace(db,record);unknown=detail.recovery.get('pending')=='prefill' and code!='form_schema_changed'
            detail.recovery={**detail.recovery,'pending':None,'last_operation':operation,
                'reconcile_required':unknown,'error_code':code}
            attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation))
            if attempt:attempt.status='failed';attempt.error_code=code;attempt.completed_at=now()
            status=failure_status(code)
            change(db,record,record.revision,status=status,stage=status,active_operation=None,error_code=code,
                approval=record.approval if unknown and status!='unsupported_form' and code not in {'application_receipt_invalid','application_reconcile_invalid'} else {},
                warnings=['External action could not complete. Known receipts are preserved. Reconcile an unknown prefill outcome before retrying; no submission is assumed.'])
            event(db,record,'RUN_ERROR',code=code);db.commit()
    def recover_interrupted(self):
        with self.factory() as db:
            pending=[(r.id,r.active_operation) for r in db.scalars(select(ApplicationRecord).where(ApplicationRecord.active_operation.is_not(None)))]
        for id,operation in pending:self.fail(id,operation,'application_interrupted')
    def _run(self,id,operation):
        try:
            with self.factory() as db:
                record=db.get(ApplicationRecord,id)
                if not record or record.active_operation!=operation:return
                detail=records.workspace(db,record)
                kind=detail.recovery.get('pending') or ('prepare' if record.status=='preparing' else 'prefill')
                preparing=kind=='prepare'
                if kind=='preview':
                    payload={'operation':'preview','draft':deepcopy(record.draft),'facts':deepcopy(record.candidate_snapshot),'job':deepcopy(record.job_snapshot)}
                elif preparing:
                    payload={'facts':deepcopy(record.candidate_snapshot),'job':deepcopy(record.job_snapshot),
                        'documents':document_options(db,record.user_id),'provider':record.provider,'use_ai':record.request.get('use_ai',False),
                        'application_id':id,'scenario':record.request.get('demo_scenario','standard'),
                        'user_completed_step':detail.recovery.get('user_completed_step',False)}
                elif kind in {'open','reconcile'}:
                    target=detail.recovery.get('last_operation','')
                    payload={'operation':kind,'provider':record.provider,'application_id':id,'job_id':record.job_id,'operation_id':target}
                else:
                    if is_stale(db,record) or record.approval.get('digest')!=approval_hash(record):raise ValueError('approval_stale')
                    approved={f['id']:f['value'] for f in record.draft['fields'] if f['approved'] and f['value'] is not None and f['value']!='' and f['value']!=[]}
                    completed=set(record.receipt.get('applied_field_ids',[]));uploaded={d['document_id']:d['sha256'] for d in record.receipt.get('attached_documents',[])}
                    documents=attachment_packet(db,record)
                    packet={'application_id':id,'operation_id':operation,'job_id':record.job_id,
                        'form_id':record.draft['form']['id'],'form_version':record.draft['form']['version'],
                        'form_fingerprint':fingerprint(record.draft['form']),
                        'approved_revision':record.approval['approved_revision'],'profile_revision':record.profile_revision,
                        'scenario':record.request.get('demo_scenario','standard'),
                        'values':{key:value for key,value in approved.items() if key not in completed},
                        'completed_fields':[{'field_id':key,'value_hash':fingerprint(approved[key])} for key in sorted(completed) if key in approved],
                        'documents':[d for d in documents if uploaded.get(d['document_id'])!=d['sha256']],
                        'completed_documents':[{'document_id':d['document_id'],'sha256':d['sha256']} for d in documents if uploaded.get(d['document_id'])==d['sha256']]}
                    payload={'operation':'prefill','provider':record.provider,'packet':packet}
                    evidence={'fields':[{'field_id':key,'value_hash':fingerprint(value)} for key,value in approved.items()],
                        'documents':[{'document_id':d['document_id'],'sha256':d['sha256']} for d in documents]}
                    db.add(MCPAction(application_id=id,operation_id=operation,tool='prefill_application',packet_digest=fingerprint(packet),request_evidence=evidence))
                if kind=='preview':db.add(MCPAction(application_id=id,operation_id=operation,tool='build_application_preview'))
                if preparing:db.add(MCPAction(application_id=id,operation_id=operation,tool='inspect_application'))
                if kind in {'open','reconcile'}:db.add(MCPAction(application_id=id,operation_id=operation,tool='open_application' if kind=='open' else 'application_status'))
                db.commit()
            def progress(data):
                with self.factory() as db:
                    record=db.get(ApplicationRecord,id)
                    if not record or record.active_operation!=operation:raise ApplicationStopped()
                    if data is not None:
                        change(db,record,record.revision,stage=data['stage'],tasks=data.get('tasks',record.tasks))
                        allowed={'MCP_CONNECTION','FORM_DISCOVERED','FIELD_PREFILL_PROGRESS','UPLOAD_PROGRESS','RETRY','WARNING','PREVIEW_GENERATING','PREVIEW_READY'}
                        type=data.get('event_type') if data.get('event_type') in allowed else 'AGENT_STATE'
                        event(db,record,type,stage=data['stage'],tasks=data.get('tasks',record.tasks),**data.get('payload',{}));db.commit()
            result=execute_application(payload,self.settings,self.manager,progress)
            progress(None)
            with self.factory() as db:
                record=db.get(ApplicationRecord,id)
                if not record or record.active_operation!=operation:raise ApplicationStopped()
                detail=records.workspace(db,record)
                if kind=='preview':
                    from app.schemas.application_preview import ApplicationPreview
                    from app.services.application_transform import build_preview
                    preview=ApplicationPreview.model_validate(result['preview'])
                    expected=build_preview(ApplicationDraft.model_validate(record.draft),record.candidate_snapshot,record.job_snapshot,preview.engine)
                    if preview!=expected:raise ValueError('application_preview_invalid')
                    change(db,record,record.revision,draft={**record.draft,'preview':preview.model_dump()},active_operation=None,stage='Application preview ready')
                    # Presentation-only regeneration does not change write approval material.
                    if record.approval:record.approval={**record.approval,'digest':approval_hash(record)}
                    attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation));attempt.status='completed';attempt.completed_at=now();attempt.receipt={'content_hash':preview.content_hash}
                    records.milestone(db,record,'preview_generated_at');event(db,record,'PREVIEW_READY')
                elif preparing:
                    draft=ApplicationDraft.model_validate(result['draft'])
                    draft.warnings+=result.get('warnings',[])
                    if draft.form.provider!=record.provider or draft.form.is_demo!=(record.provider=='demo'):raise ValueError('provider_changed')
                    # Independent deterministic re-grounding prevents a model/worker
                    # from introducing values or silently marking them approved.
                    from app.services.application_preparation import map_fields
                    baseline={f.id:f for f in map_fields(draft.form,payload['facts'],payload['job'])}
                    if validate_draft(draft)['invalid_fields']:raise ValueError('invalid_field_set')
                    for field in draft.fields:
                        if field.value!=baseline[field.id].value or field.source!=baseline[field.id].source or field.approved:raise ValueError('unsupported_prefill')
                    if draft.documents:raise ValueError('unapproved_documents')
                    from app.services.application_transform import build_preview
                    if draft.preview and draft.preview!=build_preview(draft,payload['facts'],payload['job'],draft.preview.engine):raise ValueError('application_preview_invalid')
                    change(db,record,record.revision,status='draft_ready',stage='draft_ready',draft=draft.model_dump(),tasks=result['tasks'],warnings=draft.warnings,active_operation=None)
                    attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation))
                    attempt.status='completed' if draft.connection['status']=='connected' else 'unavailable';attempt.error_code=draft.connection.get('error_code');attempt.completed_at=now()
                    attempt.receipt={'form_id':draft.form.id,'form_version':draft.form.version,'form_source':draft.connection.get('form_source','mcp')}
                    event(db,record,'FORM_OPEN',field_count=len(draft.fields))
                    status='unsupported_form' if any(f.type=='unsupported' and f.required for f in draft.form.fields) else failure_status(draft.connection['error_code']) if draft.connection.get('status')!='connected' else 'needs_user_review'
                    change(db,record,record.revision,status=status,stage=status,error_code=draft.connection.get('error_code'))
                    records.milestone(db,record,'prepared_at');records.milestone(db,record,'preview_generated_at');records.projection(db,record)
                    session=db.get(ExternalApplicationSession,id)
                    if not session:session=ExternalApplicationSession(application_id=id,provider=record.provider);db.add(session)
                    session.status=draft.connection['status'];session.form_id=draft.form.id;session.form_version=draft.form.version
                    event(db,record,'USER_APPROVAL_REQUEST',scope='Review fields and documents before prefill.')
                    event(db,record,'STATE_SNAPSHOT')
                elif kind=='prefill':
                    receipt=result['result'];packet=payload['packet']
                    if not receipt_valid(record,packet,receipt):
                        from app.integrations.application_mcp import MCPUnavailable
                        raise MCPUnavailable('application_receipt_invalid')
                    receipt=clean_receipt(receipt)
                    status='partially_completed' if receipt.get('skipped_fields') or receipt.get('failed_documents') else 'prefill_complete'
                    change(db,record,record.revision,status=status,stage=status,active_operation=None,error_code=None,
                        receipt=receipt,tasks=[{'name':'MCP Application Interaction Agent','status':'partial' if status=='partially_completed' else 'completed','method':'MCP stdio'}])
                    attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation));attempt.status=status;attempt.receipt=receipt;attempt.completed_at=now()
                    if status=='prefill_complete':records.milestone(db,record,'prefill_completed_at')
                    detail.recovery={**detail.recovery,'last_operation':operation,'reconcile_required':False}
                    event(db,record,'TOOL_CALL_END',tool='prefill_application',submitted=False)
                elif kind=='open':
                    opened=result['result']
                    from urllib.parse import urlparse
                    url=opened.get('external_url','');parsed=urlparse(url)
                    if (opened.get('opened') is not True or opened.get('is_demo')!=(record.provider=='demo') or opened.get('job_id')!=record.job_id or opened.get('application_id')!=id or
                        not (record.provider=='demo' and url=='ui://ascendra/application' or parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)):
                        raise ValueError('application_open_receipt_invalid')
                    opened={key:opened[key] for key in ('opened','is_demo','job_id','application_id','external_url')}
                    session=db.get(ExternalApplicationSession,id)
                    if not session:session=ExternalApplicationSession(application_id=id,provider=record.provider);db.add(session)
                    session.status='opened';session.external_url=url;session.opened_at=now()
                    status=detail.recovery.get('resume_status','needs_user_review')
                    change(db,record,record.revision,status=status,stage='External form opened',active_operation=None)
                    records.milestone(db,record,'external_form_opened_at',external_url=url)
                    attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation));attempt.status='completed';attempt.receipt=opened;attempt.completed_at=now()
                    event(db,record,'EXTERNAL_FORM_OPENED',is_demo=record.provider=='demo')
                elif kind=='reconcile':
                    target=db.scalar(select(MCPAction).where(MCPAction.operation_id==payload['operation_id'],MCPAction.application_id==id))
                    receipt=result['result'].get('receipt')
                    if target and receipt:
                        shadow={'operation_id':target.operation_id,'values':{},'documents':[],
                            'completed_fields':target.request_evidence.get('fields',[]),'completed_documents':target.request_evidence.get('documents',[])}
                        if not receipt_valid(record,shadow,receipt,target.packet_digest):
                            from app.integrations.application_mcp import MCPUnavailable
                            raise MCPUnavailable('application_reconcile_invalid')
                        receipt=clean_receipt(receipt)
                        status='partially_completed' if receipt.get('skipped_fields') or receipt.get('failed_documents') else 'prefill_complete'
                        change(db,record,record.revision,status=status,stage=status,receipt=receipt,active_operation=None,error_code=None)
                        target.status=status;target.receipt=receipt;target.completed_at=now()
                        detail.recovery={**detail.recovery,'reconcile_required':False,'last_operation':target.operation_id}
                        if status=='prefill_complete':records.milestone(db,record,'prefill_completed_at')
                    elif target is None:
                        detail.recovery={**detail.recovery,'reconcile_required':False}
                        change(db,record,record.revision,status='needs_user_review',stage='Review before a new approval',active_operation=None,approval={},error_code=None)
                    else:
                        change(db,record,record.revision,status='waiting_for_user',stage='External outcome unknown',active_operation=None,
                            warnings=['No reliable external receipt is available. Inspect the external form; do not blindly retry completed work.'])
                    attempt=db.scalar(select(MCPAction).where(MCPAction.operation_id==operation));attempt.status='completed';attempt.completed_at=now()
                    event(db,record,'RECONCILIATION_COMPLETE',known=bool(receipt))
                detail.recovery={**detail.recovery,'pending':None}
                event(db,record,'RUN_FINISHED');db.commit()
        except ApplicationStopped:pass
        except Exception as exc:
            self.fail(id,operation,getattr(exc,'code','application_operation_failed'))
        finally:self.capacity.release()
    def shutdown(self):self.executor.shutdown(wait=True,cancel_futures=False)


def lifecycle(status):
    return {'job_selected':'saved','preparing':'preparation_started','needs_user_review':'user_review','approved_for_prefill':'ready_to_prefill','needs_submission_approval':'awaiting_submission'}.get(status,status)
