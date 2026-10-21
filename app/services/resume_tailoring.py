"""Separate, owned resume versions; original documents/profile are read-only inputs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import BoundedSemaphore
from sqlalchemy import select,func,update
from app.core.errors import AppError
from app.db.models import TailoredResumeVersion,Document,DocumentExtraction,VerificationRecord,ApplicationRecord,ApplicationDocumentRecord,UserApproval,new_id,now
from app.services import applications,application_records as records
from app.services.cover_letters import context_for
from app.services.match_normalization import fingerprint
from app.services.resume_tailoring_content import catalog,assemble,validate,report,edit_draft
from app.schemas.resume_tailoring import ResumeDraft
from app.services.resume_tailoring_render import compile_resume
from app.services.cover_letter_render import RenderFailure
from app.orchestration.resume_tailoring import execute_resume

def owned(db,user,id):
    row=db.scalar(select(TailoredResumeVersion).where(TailoredResumeVersion.id==id,TailoredResumeVersion.user_id==user))
    if not row:raise AppError(404,'tailored_resume_not_found','This tailored resume was not found in your workspace.')
    return row

def change(db,row,expected,**values):
    changed=db.execute(update(TailoredResumeVersion).where(TailoredResumeVersion.id==row.id,TailoredResumeVersion.revision==expected)
        .values(**values,revision=expected+1,updated_at=now()).execution_options(synchronize_session=False))
    if changed.rowcount!=1:raise AppError(409,'resume_revision_conflict','This resume changed. Refresh and review its current version.')
    db.expire(row);db.refresh(row)

def history(row,state):row.data={**row.data,'history':row.data.get('history',[])+[{'state':state,'timestamp':records.iso(now())}]}

def event(db,row,type,**extra):applications.event(db,db.get(ApplicationRecord,row.application_id),type,tailored_resume_id=row.id,version=row.version,**extra)

def public(row):
    valid=row.data.get('render',{}).get('quality',{}).get('valid',False) and row.status not in {'failed','cancelled','validation_failed'}
    return {'id':row.id,'application_id':row.application_id,'version':row.version,'revision':row.revision,'status':row.status,'stage':row.stage,
        'operation_active':bool(row.active_operation),'profile_revision':row.profile_revision,'job':{k:row.job_snapshot.get(k,'') for k in ('id','title','company','location')},
        'data':row.data,'document_id':row.document_id,'approval':row.approval,'error_code':row.error_code,'tasks':row.tasks,
        'source_available':bool(row.artifact_dir and (Path(row.artifact_dir)/'document.tex').is_file()),'pdf_available':bool(valid),'preview_available':bool(valid),
        'created_at':records.iso(row.created_at),'updated_at':records.iso(row.updated_at)}

def check_current(db,row):
    profile,facts=applications.source(db,row.user_id);app=applications.owned(db,row.user_id,row.application_id)
    if profile.revision!=row.profile_revision or fingerprint(facts)!=row.candidate_hash or fingerprint(app.job_snapshot)!=row.job_hash:
        raise AppError(409,'resume_sources_changed','Confirmed profile or job changed. Generate a new tailored version.')
    return app

def input_source(db,app,profile,payload):
    id=payload.source_document_id
    if payload.input_mode in {'processed_pdf','latex_source'} and not id:raise AppError(422,'resume_source_required','Select the confirmed processed PDF or upload a LaTeX reference source.')
    if not id:return {'mode':payload.input_mode,'authority':'Current confirmed structured profile; supplied static template.'}
    doc=db.scalar(select(Document).where(Document.id==id,Document.user_id==app.user_id))
    if not doc:raise AppError(404,'resume_source_missing','Source document was not found in this workspace.')
    if sha256(doc.content).hexdigest()!=doc.sha256:raise AppError(409,'resume_source_changed','Original source bytes changed. Upload and review the correct source.')
    if payload.input_mode=='latex_source' and doc.content_type!='text/x-tex':raise AppError(422,'resume_source_type','Select a LaTeX reference uploaded in the tailoring workspace.')
    if payload.input_mode=='processed_pdf':
        proof=db.scalar(select(VerificationRecord.id).where(VerificationRecord.user_id==app.user_id,VerificationRecord.document_id==id,VerificationRecord.profile_id==profile.id))
        if doc.content_type!='application/pdf' or not proof:raise AppError(409,'resume_pdf_unconfirmed','Process and confirm the PDF using the existing resume review workflow first.')
    return {'mode':payload.input_mode,'document_id':doc.id,'filename':doc.filename,'sha256':doc.sha256,
        'authority':'Confirmed profile is authoritative. Uploaded TeX is retained as a passive reference and never executed.' if payload.input_mode=='latex_source' else 'Current confirmed structured profile; original document preserved.'}

def new_version(db,app,profile,facts,payload):
    if app.active_operation:raise AppError(409,'application_in_progress','Wait for the current application operation before tailoring.')
    if db.scalar(select(TailoredResumeVersion.id).where(TailoredResumeVersion.application_id==app.id,TailoredResumeVersion.active_operation.is_not(None))):raise AppError(409,'resume_tailoring_in_progress','Resume tailoring is already running for this application.')
    if not facts.get('name') or not app.job_snapshot.get('title'):raise AppError(422,'resume_inputs_missing','A confirmed candidate name and selected job title are required.')
    base=None
    if payload.base_version_id:
        base=owned(db,app.user_id,payload.base_version_id)
        if base.application_id!=app.id or base.candidate_hash!=fingerprint(facts) or base.job_hash!=fingerprint(app.job_snapshot):raise AppError(409,'resume_base_stale','The base version belongs to another job or outdated profile.')
        if not base.data.get('draft'):raise AppError(409,'resume_base_unavailable','The base version has no structured draft.')
    if payload.regenerate_section!='all' and not base:raise AppError(422,'resume_base_required','Select a base version before regenerating one section.')
    source=input_source(db,app,profile,payload)
    applications.change(db,app,payload.expected_application_revision)
    version=(db.scalar(select(func.max(TailoredResumeVersion.version)).where(TailoredResumeVersion.application_id==app.id)) or 0)+1
    row=TailoredResumeVersion(user_id=app.user_id,application_id=app.id,version=version,profile_revision=profile.revision,candidate_hash=fingerprint(facts),
        candidate_snapshot=deepcopy(facts),job_snapshot=deepcopy(app.job_snapshot),job_hash=fingerprint(app.job_snapshot),request=payload.model_dump(mode='json'),active_operation=new_id(),
        data={'context':context_for(db,app,profile,facts),'input_source':source,'original_evidence':original_evidence(db,app.user_id,profile),'base_draft':base.data['draft'] if base else None,
            'history':[{'state':'edited' if payload.edits else 'preparing','timestamp':records.iso(now())}]})
    db.add(row);db.flush();event(db,row,'RUN_STARTED',stage='Resume tailoring started');return row

def original_evidence(db,user_id,profile):
    """Reuse already-confirmed extraction evidence locally, never reparse/export.

    The original document stays untouched. Older source facts are references,
    not permission to overwrite the current user-confirmed profile.
    """
    proofs=db.scalars(select(VerificationRecord).where(VerificationRecord.user_id==user_id,
        VerificationRecord.profile_id==profile.id).order_by(VerificationRecord.created_at.desc())).all()
    result=[]
    for proof in proofs:
        doc=db.get(Document,proof.document_id) if proof.document_id else None
        extraction=db.get(DocumentExtraction,proof.extraction_id) if proof.extraction_id else None
        result.append({'profile_revision':proof.profile_revision,'current_revision':proof.profile_revision==profile.revision,
            'document_id':doc.id if doc else None,'sha256':doc.sha256 if doc else None,
            'original_bytes_verified':sha256(doc.content).hexdigest()==doc.sha256 if doc else None,
            'applied_fields':proof.applied_fields,'source_evidence':deepcopy(proof.source_evidence),
            'confirmed_values':deepcopy(proof.confirmed_values),'resume_text_blocks':deepcopy(extraction.text_blocks) if extraction else [],
            'authority':'Local source reference; current confirmed profile remains authoritative. Not exported to the tailoring model.'})
    return result

def digest(row):return fingerprint({'draft':row.data['draft'],'profile_hash':row.candidate_hash,'job_hash':row.job_hash,'pdf_sha256':row.data['render']['pdf_sha256'],'template':row.request['template'],'version_id':row.id})

def approve(db,row,expected):
    check_current(db,row)
    if row.data.get('catalog',{}).get('content_policy_version')!=2:
        raise AppError(409,'resume_policy_update_required','Create a new tailored version so the complete profile and PDF pass the current preservation checks before approval.')
    if row.status!='needs_review' or not row.data.get('render',{}).get('quality',{}).get('valid'):raise AppError(409,'resume_not_ready','Compile and review the one-page PDF before approval.')
    path=Path(row.artifact_dir)/'document.pdf'
    if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=row.data['render']['pdf_sha256']:raise AppError(409,'resume_pdf_changed','PDF bytes changed. Generate and review a fresh version.')
    content=path.read_bytes();doc=db.scalar(select(Document).where(Document.user_id==row.user_id,Document.sha256==sha256(content).hexdigest()))
    if not doc:
        doc=Document(user_id=row.user_id,filename=f'tailored-resume-v{row.version}.pdf',content_type='application/pdf',size_bytes=len(content),sha256=sha256(content).hexdigest(),content=content);db.add(doc);db.flush()
    approval={'digest':digest(row),'reviewed_at':records.iso(now()),'version_id':row.id}
    change(db,row,expected,status='approved',stage='Tailored resume approved',document_id=doc.id,approval=approval)
    history(row,'approved');app=db.get(ApplicationRecord,row.application_id)
    db.add(UserApproval(application_id=app.id,kind='tailored_resume',revision=app.revision,digest=approval['digest'],scope={'tailored_resume_id':row.id,'document_id':doc.id}))
    event(db,row,'TRANSACTION_STATE',stage='Tailored resume approved; application not submitted')

def attach(db,row,expected,application_revision):
    app=check_current(db,row)
    if row.status!='approved' or row.approval.get('digest')!=digest(row):raise AppError(409,'resume_approval_required','Approve this exact PDF version before attachment.')
    if application_revision is None:raise AppError(422,'application_revision_required','Refresh the application revision before attachment.')
    if app.active_operation or app.status in applications.TRACKING or app.receipt.get('applied_field_ids') or app.receipt.get('attached_documents'):raise AppError(409,'resume_attachment_locked','External application work already exists. Download the resume for manual use; attachments cannot change silently.')
    if app.profile_revision!=row.profile_revision or app.candidate_hash!=row.candidate_hash:raise AppError(409,'application_profile_stale','Refresh application preparation from the current confirmed profile first.')
    doc=db.get(Document,row.document_id)
    if not doc or sha256(doc.content).hexdigest()!=row.data['render']['pdf_sha256']:raise AppError(409,'resume_document_changed','Approved PDF is unavailable or changed.')
    change(db,row,expected,status='attached',stage='Attached to selected application')
    for old in db.scalars(select(TailoredResumeVersion).where(TailoredResumeVersion.application_id==app.id,TailoredResumeVersion.id!=row.id,TailoredResumeVersion.status=='attached')):
        old.status='replaced';history(old,'replaced')
    option={'id':doc.id,'document_id':doc.id,'filename':doc.filename,'sha256':doc.sha256,'content_type':doc.content_type,'size_bytes':doc.size_bytes,
        'job_id':app.job_id,'application_id':app.id,'role':'resume','approved':True,'confirmed_resume':True,'tailored_resume_id':row.id,'selected':True}
    draft=deepcopy(app.draft)
    if draft:
        draft['documents']=[d for d in draft.get('documents',[]) if d.get('role')!='resume']+[option]
        draft['document_options']=applications.document_options(db,row.user_id);draft['preview']=None
    applications.change(db,app,application_revision,draft=draft,approval={},status='needs_user_review' if draft else app.status)
    records.revoke(db,app)
    for old in db.scalars(select(ApplicationDocumentRecord).where(ApplicationDocumentRecord.application_id==app.id)):
        if old.data.get('role')=='resume':old.data={**old.data,'selected':False,'status':'replaced'}
    existing=db.scalar(select(ApplicationDocumentRecord).where(ApplicationDocumentRecord.application_id==app.id,ApplicationDocumentRecord.document_ref==doc.id))
    if existing:existing.data=option
    else:db.add(ApplicationDocumentRecord(application_id=app.id,document_id=doc.id,document_ref=doc.id,data=option))
    history(row,'attached');event(db,row,'TRANSACTION_STATE',stage='Approved tailored resume attached; original preserved; application not submitted',document_id=doc.id)

class Stopped(Exception):pass

class ResumeTailoringProcessor:
    def __init__(self,factory,settings,manager):
        self.factory,self.settings,self.manager=factory,settings,manager
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='resume-tailoring');self.capacity=BoundedSemaphore(3)
    def reserve(self):
        if not self.capacity.acquire(blocking=False):raise AppError(429,'resume_tailoring_capacity','Resume tailoring workers are busy. Retry shortly.')
    def submit(self,id,operation,kind='generate'):
        try:self.executor.submit(self._run,id,operation,kind)
        except RuntimeError:
            self.capacity.release();self.fail(id,operation,'resume_worker_unavailable');raise AppError(503,'resume_worker_unavailable','Resume worker is shutting down.') from None
    def shutdown(self):self.executor.shutdown(wait=True,cancel_futures=True)
    def recover_interrupted(self):
        with self.factory() as db:pending=[(r.id,r.active_operation) for r in db.scalars(select(TailoredResumeVersion).where(TailoredResumeVersion.active_operation.is_not(None)))]
        for id,operation in pending:self.fail(id,operation,'resume_tailoring_interrupted')
    def fail(self,id,operation,code,audit=None):
        with self.factory() as db:
            row=db.get(TailoredResumeVersion,id)
            if not row or row.active_operation!=operation:return
            change(db,row,row.revision,status='failed',stage='Important content exceeds one readable page — review source bullets' if code=='resume_insufficient_space' else 'Resume tailoring failed — inspect validation and retry',active_operation=None,error_code=code,data={**row.data,**({'render':audit} if audit else {})})
            history(row,'failed');event(db,row,'RUN_ERROR',code=code);db.commit()
    def _run(self,id,operation,kind):
        try:
            def progress(data=None):
                with self.factory() as db:
                    row=db.get(TailoredResumeVersion,id)
                    if not row or row.active_operation!=operation:raise Stopped()
                    if data:
                        change(db,row,row.revision,stage=data['stage'],tasks=row.tasks+[{'name':data['stage'],'status':'completed'}])
                        event(db,row,'AGENT_STATE',stage=data['stage']);db.commit()
            with self.factory() as db:
                row=db.get(TailoredResumeVersion,id)
                if not row or row.active_operation!=operation:return
                payload={'facts':deepcopy(row.candidate_snapshot),'job':deepcopy(row.job_snapshot),'options':deepcopy(row.request),'context':row.data.get('context',{}),'base_draft':row.data.get('base_draft')}
            if kind=='generate':
                result=execute_resume(payload,self.settings,self.manager,progress)
                source=catalog(payload['facts'],payload['job'],payload['context'])
                if result['catalog']!=source:raise ValueError('resume_evidence_changed')
                draft=ResumeDraft.model_validate(result['draft']);validation=validate(draft,source,payload['facts'],payload['job'])
                if payload.get('base_draft') and payload['options']['regenerate_section']!='all':
                    expected=edit_draft(ResumeDraft.model_validate(payload['base_draft']),source,payload['options'].get('edits',{}))
                    regenerated=payload['options']['regenerate_section']
                    if regenerated!='summary' and draft.summary!=expected.summary:raise ValueError('resume_retained_summary_changed')
                    for section in expected.section_order:
                        if section in {'summary',regenerated}:continue
                        if [e.model_dump() for e in draft.entries if e.section==section]!=[e.model_dump() for e in expected.entries if e.section==section]:raise ValueError('resume_retained_section_changed')
                    if draft.section_order!=expected.section_order:raise ValueError('resume_section_order_changed')
                with self.factory() as db:
                    row=db.get(TailoredResumeVersion,id)
                    if row.active_operation!=operation:raise Stopped()
                    change(db,row,row.revision,status='generated' if validation['valid'] else 'validation_failed',stage='Structured resume generated',data={**row.data,**result,'validation':validation})
                    history(row,'generated' if validation['valid'] else 'validation_failed');event(db,row,'STATE_SNAPSHOT',stage=row.stage)
                    if not validation['valid']:row.active_operation=None;db.commit();return
                    db.commit()
            progress(None)
            with self.factory() as db:
                row=db.get(TailoredResumeVersion,id)
                if row.active_operation!=operation:raise Stopped()
                directory=self.settings.data_dir.resolve()/'tailored_resumes'/row.id
                change(db,row,row.revision,status='rendering',stage='LaTeX Resume Generation Agent',artifact_dir=str(directory))
                draft=ResumeDraft.model_validate(row.data['draft']);source=deepcopy(row.data['catalog']);db.commit()
            final,render=compile_resume(draft,source,payload['facts'],payload['job'],directory,self.settings,lambda:progress(None))
            with self.factory() as db:
                row=db.get(TailoredResumeVersion,id)
                if row.active_operation!=operation:raise Stopped()
                validation=validate(final,source,payload['facts'],payload['job']);validation['one_page']='passed'
                change(db,row,row.revision,status='needs_review',stage='One-page PDF ready — review changes and approve',active_operation=None,
                    data={**row.data,'draft':final.model_dump(),'render':render,'validation':validation,'report':report(final,source,payload['facts'])})
                history(row,'validated');history(row,'needs_review');event(db,row,'RUN_FINISHED',stage=row.stage);db.commit()
        except Stopped:pass
        except RenderFailure as exc:self.fail(id,operation,exc.code,exc.audit)
        except Exception as exc:
            # Catalogue changes during a backend update fail closed, with a
            # useful code rather than an opaque failure or mixed-version PDF.
            safe_codes={'resume_evidence_changed','resume_unsupported_proposal','resume_retained_section_changed'}
            code=getattr(exc,'code',str(exc) if str(exc) in safe_codes else 'resume_tailoring_failed')
            self.fail(id,operation,code)
        finally:self.capacity.release()
