"""Owned, versioned cover-letter orchestration and exact-byte approval/attachment."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import BoundedSemaphore
from sqlalchemy import func,select,update
from app.core.errors import AppError
from app.db.models import (CoverLetterVersion,SignatureAsset,ApplicationRecord,ApplicationDocumentRecord,
    ApplicationWorkspace,Document,SkillGapRun,UserApproval,new_id,now)
from app.services import applications as applications
from app.services import application_records as records
from app.services.match_normalization import fingerprint
from app.schemas.cover_letters import LetterContent,Sentence
from app.services.cover_letter_content import validate_content
from app.services.cover_letter_render import compile_letter,RenderFailure
from app.orchestration.cover_letters import execute_cover

def owned(db,user_id,id):
    row=db.scalar(select(CoverLetterVersion).where(CoverLetterVersion.id==id,CoverLetterVersion.user_id==user_id))
    if not row:raise AppError(404,'cover_not_found','This cover-letter version was not found in your workspace.')
    return row

def signature(db,user_id,id,signer):
    asset=db.scalar(select(SignatureAsset).where(SignatureAsset.id==id,SignatureAsset.user_id==user_id)) if id else None
    if not asset:raise AppError(409,'signature_required','Upload and approve your signature, or explicitly choose a letter without an image signature.')
    if sha256(asset.content).hexdigest()!=asset.sha256:raise AppError(409,'signature_changed','Stored signature changed. Upload and approve a fresh asset.')
    if ' '.join(asset.signer_name.casefold().split())!=' '.join(signer.casefold().split()):raise AppError(422,'signature_signer_mismatch','Signature signer must match the confirmed candidate name.')
    return asset

def change(db,row,expected,**values):
    result=db.execute(update(CoverLetterVersion).where(CoverLetterVersion.id==row.id,CoverLetterVersion.revision==expected)
        .values(**values,revision=expected+1,updated_at=now()).execution_options(synchronize_session=False))
    if result.rowcount!=1:raise AppError(409,'cover_revision_conflict','This letter changed. Refresh and review its current version.')
    db.expire(row);db.refresh(row)

def history(row,state):
    row.data={**row.data,'history':row.data.get('history',[])+[{'state':state,'timestamp':records.iso(now())}]}

def event(db,row,type,**payload):
    app=db.get(ApplicationRecord,row.application_id)
    applications.event(db,app,type,cover_letter_id=row.id,version=row.version,**payload)

def public(row):
    return {'id':row.id,'application_id':row.application_id,'version':row.version,'revision':row.revision,
        'status':row.status,'stage':row.stage,'operation_active':bool(row.active_operation),'profile_revision':row.profile_revision,
        'review_state':'needs_user_review' if row.status=='preview_ready' else row.status,
        'job':{k:row.job_snapshot.get(k,'') for k in ('id','title','company','source','location')},
        'data':row.data,'signature_id':row.signature_id,'document_id':row.document_id,'approval':row.approval,
        'tasks':row.tasks,'error_code':row.error_code,'created_at':records.iso(row.created_at),'updated_at':records.iso(row.updated_at),
        'source_available':bool(row.artifact_dir and (Path(row.artifact_dir)/'letter.tex').is_file()),
        'pdf_available':bool(row.data.get('render',{}).get('quality',{}).get('valid')) and row.status not in {'cancelled','failed'},
        'preview_available':bool(row.data.get('render',{}).get('quality',{}).get('preview_generated')) and row.status not in {'cancelled','failed'}}

def check_current(db,row):
    profile,facts=applications.source(db,row.user_id)
    app=applications.owned(db,row.user_id,row.application_id)
    if profile.revision!=row.profile_revision or fingerprint(facts)!=row.candidate_hash or fingerprint(app.job_snapshot)!=row.job_hash:
        raise AppError(409,'cover_source_stale','Profile or selected job changed. Generate a new letter from current confirmed facts.')
    return app

def context_for(db,app,profile,facts):
    detail=db.get(ApplicationWorkspace,app.id)
    matching=detail.context if detail else {}
    if app.profile_revision!=profile.revision or app.candidate_hash!=fingerprint(facts):matching={}
    gap=db.scalar(select(SkillGapRun).where(SkillGapRun.user_id==app.user_id,SkillGapRun.profile_revision==profile.revision,
        SkillGapRun.candidate_hash==fingerprint(facts),SkillGapRun.status.in_(['completed','partial'])).order_by(SkillGapRun.created_at.desc()).limit(1))
    gap_summary={}
    if gap and any(j.get('id')==app.job_id for j in gap.job_snapshot):
        # Only relevant gap labels, never another user's/global source text.
        gap_summary={'run_id':gap.id,'profile_revision':gap.profile_revision,
            'gaps':[{'skill':g.get('skill',g.get('name','')),'status':g.get('status','missing_evidence')} for g in gap.data.get('gaps',[])[:15]]}
    allowed={'status','message','scores','hybrid_score','match_percentage','missing_required','missing_preferred','matched_required','missing_skills','matched_skills','score_breakdown'}
    return {'matching':{k:v for k,v in matching.items() if k in allowed},'skill_gaps':gap_summary}

def new_version(db,app,profile,facts,payload):
    if app.active_operation:raise AppError(409,'application_in_progress','Wait for the current application operation before generating a letter.')
    if db.scalar(select(CoverLetterVersion.id).where(CoverLetterVersion.application_id==app.id,CoverLetterVersion.active_operation.is_not(None))):raise AppError(409,'cover_in_progress','A cover-letter operation is already running for this application.')
    if not facts.get('name') or not app.job_snapshot.get('title'):raise AppError(422,'cover_inputs_missing','A confirmed candidate name and selected job title are required.')
    base=None
    if payload.base_version_id:
        base=owned(db,app.user_id,payload.base_version_id)
        if base.application_id!=app.id or base.candidate_hash!=fingerprint(facts) or base.job_hash!=fingerprint(app.job_snapshot):raise AppError(409,'cover_base_stale','The base letter belongs to a different job or profile. Generate from current facts.')
    if payload.regenerate_section!='all' and not base:raise AppError(422,'cover_base_required','Select an existing version before regenerating one paragraph.')
    asset=None
    if payload.include_signature and payload.signature_id:
        asset=signature(db,app.user_id,payload.signature_id,facts['name'])
        if not payload.signature_approved:raise AppError(422,'signature_approval_required','Approve use of this signature for this document.')
    applications.change(db,app,payload.expected_application_revision)
    version=(db.scalar(select(func.max(CoverLetterVersion.version)).where(CoverLetterVersion.application_id==app.id)) or 0)+1
    options=payload.model_dump(mode='json');operation=new_id()
    row=CoverLetterVersion(user_id=app.user_id,application_id=app.id,version=version,profile_revision=profile.revision,
        candidate_hash=fingerprint(facts),candidate_snapshot=deepcopy(facts),job_snapshot=deepcopy(app.job_snapshot),job_hash=fingerprint(app.job_snapshot),
        request=options,signature_id=asset.id if asset else None,active_operation=operation,
        data={'context':context_for(db,app,profile,facts),'base_content':base.data.get('content') if base else None,
            'history':[{'state':'edited' if payload.edits else 'generated','timestamp':records.iso(now())}]})
    db.add(row);db.flush();event(db,row,'RUN_STARTED',stage='Cover letter preparation');return row,operation

def approval_digest(row):
    return fingerprint({'content':row.data['content'],'profile_hash':row.candidate_hash,'job_hash':row.job_hash,
        'signature':row.data.get('signature'),'pdf_sha256':row.data['render']['pdf_sha256'],'version_id':row.id})

def approve(db,row,expected):
    check_current(db,row)
    if row.status!='preview_ready' or not row.data.get('render',{}).get('quality',{}).get('valid'):raise AppError(409,'cover_not_ready','Render and review a validated PDF before approval.')
    path=Path(row.artifact_dir)/'letter.pdf'
    if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=row.data['render']['pdf_sha256']:raise AppError(409,'cover_pdf_changed','The PDF changed. Render and review a fresh version.')
    digest=approval_digest(row)
    content=path.read_bytes();document=db.scalar(select(Document).where(Document.user_id==row.user_id,Document.sha256==sha256(content).hexdigest()))
    if not document:
        document=Document(user_id=row.user_id,filename=f'cover-letter-v{row.version}.pdf',content_type='application/pdf',size_bytes=len(content),sha256=sha256(content).hexdigest(),content=content)
        db.add(document);db.flush()
    change(db,row,expected,status='approved',stage='Approved PDF',document_id=document.id,
        approval={'digest':digest,'reviewed_at':records.iso(now()),'signature_included':bool(row.signature_id),'version_id':row.id})
    history(row,'reviewed');history(row,'approved')
    app=db.get(ApplicationRecord,row.application_id)
    db.add(UserApproval(application_id=app.id,kind='cover_letter',revision=app.revision,digest=digest,scope={'cover_letter_id':row.id,'document_id':document.id,'signature_included':bool(row.signature_id)}))
    event(db,row,'TRANSACTION_STATE',stage='Cover letter approved; application not submitted')

def attach(db,row,expected,application_revision):
    app=check_current(db,row)
    if row.status!='approved' or row.approval.get('digest')!=approval_digest(row):raise AppError(409,'cover_approval_required','Approve this exact document version before attaching.')
    if application_revision is None:raise AppError(422,'application_revision_required','Refresh the application revision before attaching.')
    if app.active_operation or app.status in applications.TRACKING or app.receipt.get('applied_field_ids') or app.receipt.get('attached_documents'):
        raise AppError(409,'cover_attachment_locked','This application already has external work or is tracking a submission. Keep the approved download; attachments cannot change silently.')
    if app.profile_revision!=row.profile_revision or app.candidate_hash!=row.candidate_hash:raise AppError(409,'application_profile_stale','Refresh application preparation with the current confirmed profile before attaching.')
    doc=db.get(Document,row.document_id)
    if not doc or sha256(doc.content).hexdigest()!=row.data['render']['pdf_sha256']:raise AppError(409,'cover_document_missing','Approved document is missing or changed. Generate and approve a new version.')
    change(db,row,expected,status='attached',stage='Attached to selected application')
    for old in db.scalars(select(CoverLetterVersion).where(CoverLetterVersion.application_id==app.id,CoverLetterVersion.id!=row.id,CoverLetterVersion.status=='attached')):
        old.status='replaced';history(old,'replaced')
    option={'id':doc.id,'document_id':doc.id,'filename':doc.filename,'sha256':doc.sha256,'content_type':doc.content_type,'size_bytes':doc.size_bytes,
        'job_id':app.job_id,'application_id':app.id,'role':'cover_letter','approved':True,'confirmed_resume':False,'cover_letter_id':row.id,'selected':True}
    draft=deepcopy(app.draft)
    if draft:
        draft['documents']=[d for d in draft.get('documents',[]) if d.get('role')!='cover_letter']+[option]
        draft['document_options']=applications.document_options(db,row.user_id)
        draft['preview']=None
    applications.change(db,app,application_revision,draft=draft,approval={},status='needs_user_review' if draft else app.status)
    records.revoke(db,app)
    previous=db.scalar(select(ApplicationDocumentRecord).where(ApplicationDocumentRecord.application_id==app.id,ApplicationDocumentRecord.document_ref==doc.id))
    if previous:previous.data=option
    else:db.add(ApplicationDocumentRecord(application_id=app.id,document_id=doc.id,document_ref=doc.id,data=option))
    history(row,'attached');event(db,row,'TRANSACTION_STATE',stage='Approved cover letter attached; application not submitted',document_id=doc.id)

class Stopped(Exception):pass

class CoverLetterProcessor:
    def __init__(self,factory,settings,manager):
        self.factory,self.settings,self.manager=factory,settings,manager
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='cover-letter');self.capacity=BoundedSemaphore(3)
    def reserve(self):
        if not self.capacity.acquire(blocking=False):raise AppError(429,'cover_capacity','Cover-letter workers are busy. Retry shortly.')
    def submit(self,id,operation,kind='generate'):
        try:self.executor.submit(self._run,id,operation,kind)
        except RuntimeError:
            self.capacity.release();self.fail(id,operation,'cover_worker_unavailable')
            raise AppError(503,'cover_worker_unavailable','Cover-letter worker is shutting down.') from None
    def recover_interrupted(self):
        with self.factory() as db:pending=[(r.id,r.active_operation) for r in db.scalars(select(CoverLetterVersion).where(CoverLetterVersion.active_operation.is_not(None)))]
        for id,operation in pending:self.fail(id,operation,'cover_interrupted')
    def shutdown(self):self.executor.shutdown(wait=True,cancel_futures=True)
    def fail(self,id,operation,code,audit=None):
        with self.factory() as db:
            row=db.get(CoverLetterVersion,id)
            if not row or row.active_operation!=operation:return
            change(db,row,row.revision,status='failed',stage='Cover letter failed — inspect the audit and retry',active_operation=None,error_code=code,
                data={**row.data,**({'render':audit} if audit else {})})
            event(db,row,'RUN_ERROR',code=code);db.commit()
    def _run(self,id,operation,kind):
        try:
            def progress(data=None):
                with self.factory() as db:
                    row=db.get(CoverLetterVersion,id)
                    if not row or row.active_operation!=operation:raise Stopped()
                    if data:
                        task={'name':data['stage'],'status':'completed'}
                        change(db,row,row.revision,stage=data['stage'],tasks=row.tasks+[task])
                        event(db,row,'AGENT_STATE',stage=data['stage']);db.commit()
            with self.factory() as db:
                row=db.get(CoverLetterVersion,id)
                if not row or row.active_operation!=operation:return
                payload={'facts':deepcopy(row.candidate_snapshot),'job':deepcopy(row.job_snapshot),'options':deepcopy(row.request),
                    'context':row.data.get('context',{}),'base_content':row.data.get('base_content')}
            if kind=='generate':
                result=execute_cover(payload,self.settings,self.manager,progress)
                # Independent host validation, even for worker/model output.
                from app.services.cover_letter_content import research,sentence_catalog
                expected_plan=research(payload['facts'],payload['job'],payload['context'])
                expected_catalog=sentence_catalog(payload['facts'],payload['job'],expected_plan,payload['options']['tone'])
                if result['plan']!=expected_plan or result['catalog']!=[s.model_dump() for s in expected_catalog]:raise ValueError('cover_evidence_changed')
                content=LetterContent.model_validate(result['content'])
                original=LetterContent.model_validate(result['generated_content'])
                if original.date.isoformat()!=payload['options']['document_date'] or content.date!=original.date:raise ValueError('cover_date_changed')
                if not validate_content(original,expected_catalog,payload['facts'],payload['job'])['valid']:raise ValueError('cover_unsupported_generated_content')
                options=payload['options'];permitted={}
                if payload.get('base_content') and options['regenerate_section']!='all':
                    permitted={k:payload['base_content'][k] for k in ('opening','motivation','experience','skills_alignment','closing') if k!=options['regenerate_section']}
                permitted.update(options.get('edits',{}))
                if options.get('personal_sentence'):
                    # The preceding paragraph must itself be grounded or an explicitly retained/edited paragraph.
                    before=permitted.get('motivation',original.motivation)
                    permitted['motivation']=(before+' '+options['personal_sentence']).strip()
                if result.get('user_sections')!=permitted:raise ValueError('cover_unapproved_user_text')
                retained=[key for key in ('opening','motivation','experience','skills_alignment','closing') if payload.get('base_content') and options['regenerate_section']!='all' and key!=options['regenerate_section'] and key not in options.get('edits',{}) and not (key=='motivation' and options.get('personal_sentence'))]
                if result.get('retained_sections')!=retained:raise ValueError('cover_retained_sections_changed')
                validation=validate_content(content,expected_catalog,payload['facts'],payload['job'],user_sections=result.get('user_sections'),retained_sections=retained)
                with self.factory() as db:
                    row=db.get(CoverLetterVersion,id)
                    if row.active_operation!=operation:raise Stopped()
                    result['validation']=validation
                    change(db,row,row.revision,status='draft_ready' if validation['valid'] else 'validation_failed',stage='Structured draft ready',data={**row.data,**result})
                    history(row,'validated' if validation['valid'] else 'validation_failed');event(db,row,'STATE_SNAPSHOT',stage=row.stage)
                    if not validation['valid']:row.active_operation=None;db.commit();return
                    db.commit()
            progress(None)
            with self.factory() as db:
                row=db.get(CoverLetterVersion,id)
                if row.active_operation!=operation:raise Stopped()
                options=row.request
                if options['include_signature'] and not row.signature_id:
                    change(db,row,row.revision,status='waiting_for_signature',stage='Upload and approve your signature, or explicitly omit the image',active_operation=None)
                    event(db,row,'USER_INPUT_REQUEST',stage=row.stage);db.commit();return
                asset=signature(db,row.user_id,row.signature_id,row.candidate_snapshot['name']) if options['include_signature'] else None
                signature_bytes=asset.content if asset else None
                signature_meta={'included':bool(asset),'approved_by_user':bool(asset and options['signature_approved']),'sha256':asset.sha256 if asset else None,
                    'signer_name':asset.signer_name if asset else row.candidate_snapshot['name'],'width_mm':options['signature_width_mm']}
                destination=self.settings.data_dir.resolve()/'cover_letters'/row.id
                change(db,row,row.revision,status='rendering',stage='Compiling and checking PDF',artifact_dir=str(destination),
                    data={**row.data,'signature':signature_meta})
                content=LetterContent.model_validate(row.data['content']);facts=deepcopy(row.candidate_snapshot)
                event(db,row,'AGENT_STATE',stage='LaTeX Rendering and Document Agent');db.commit()
            render=compile_letter(content,facts,signature_bytes,options['signature_width_mm'],destination,self.settings,lambda:progress(None))
            with self.factory() as db:
                row=db.get(CoverLetterVersion,id)
                if row.active_operation!=operation:raise Stopped()
                change(db,row,row.revision,status='preview_ready',stage='PDF ready — review before approval',active_operation=None,data={**row.data,'render':render})
                history(row,'generated');history(row,'validated');event(db,row,'RUN_FINISHED',stage='Cover letter preview ready');db.commit()
        except Stopped:pass
        except RenderFailure as exc:self.fail(id,operation,exc.code,exc.audit)
        except Exception as exc:self.fail(id,operation,getattr(exc,'code','cover_generation_failed'))
        finally:self.capacity.release()
