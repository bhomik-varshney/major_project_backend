"""Owned tailoring/preview/version API; untrusted uploaded TeX is never executed."""
from hashlib import sha256
from pathlib import Path,PurePosixPath
import re
from fastapi import APIRouter,Depends,Request,UploadFile,Query
from fastapi.responses import Response
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.api.dependencies import current_user,get_db
from app.core.errors import AppError
from app.db.models import User,Document,TailoredResumeVersion,new_id
from app.schemas.resume_tailoring import ResumeStart,ResumeAction
from app.services import resume_tailoring as service,applications
from app.services.resume_tailoring_render import readiness as render_readiness,TEMPLATE

router=APIRouter(prefix='/resume-tailoring',tags=['Resume tailoring'])

@router.get('/readiness')
def readiness(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    try:profile,facts=applications.source(db,user.id);ready=bool(facts.get('name'))
    except AppError:ready=False
    return {**render_readiness(request.app.state.settings),'profile_ready':ready,'groq_configured':bool(request.app.state.settings.groq_keys),
        'templates':[{'id':'supplied-navy-v1','label':'Supplied navy resume.cls template'}],
        'sources':[{'id':d.id,'filename':d.filename,'content_type':d.content_type} for d in db.scalars(select(Document).where(Document.user_id==user.id,Document.content_type.in_(['text/x-tex','application/pdf'])).order_by(Document.created_at.desc()).limit(100))],
        'source_policy':'All modes use confirmed profile facts. Uploaded TeX is a passive reference, never compiled. Confirm missing source facts through profile review first.'}

@router.post('/sources',status_code=201)
async def source_upload(file:UploadFile,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    name=PurePosixPath((file.filename or '').replace('\\','/')).name
    if not name.endswith('.tex') or len(name)>180 or re.search(r'[\x00-\x1f\x7f]',name):raise AppError(422,'resume_tex_source_invalid','Choose a UTF-8 .tex resume source.')
    try:content=await file.read(200001)
    finally:await file.close()
    try:
        text=content.decode('utf-8-sig')
        if not content or len(content)>200000 or '\x00' in text or r'\documentclass' not in text or r'\begin{document}' not in text or r'\end{document}' not in text:raise ValueError()
    except (ValueError,UnicodeError):raise AppError(422,'resume_tex_source_invalid','Use a readable UTF-8 LaTeX resume (up to 200 KB) with a document class and document environment.') from None
    digest=sha256(content).hexdigest();existing=db.scalar(select(Document).where(Document.user_id==user.id,Document.sha256==digest))
    if existing:return {'id':existing.id,'filename':existing.filename,'passive_reference_only':True}
    if db.scalar(select(func.count()).select_from(Document).where(Document.user_id==user.id))>=request.app.state.settings.max_documents_per_user:raise AppError(409,'document_limit','Document library is full. Remove an unneeded document before adding a reference.')
    doc=Document(user_id=user.id,filename=name,content_type='text/x-tex',size_bytes=len(content),sha256=digest,content=content)
    db.add(doc);db.commit()
    return {'id':doc.id,'filename':doc.filename,'sha256':doc.sha256,'passive_reference_only':True}

@router.get('')
def listing(application_id:str=Query(...,max_length=36),user:User=Depends(current_user),db:Session=Depends(get_db)):
    applications.owned(db,user.id,application_id)
    return [service.public(r) for r in db.scalars(select(TailoredResumeVersion).where(TailoredResumeVersion.user_id==user.id,TailoredResumeVersion.application_id==application_id).order_by(TailoredResumeVersion.version.desc()).limit(100))]

@router.post('/applications/{id}',status_code=202)
def start(id:str,payload:ResumeStart,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    app=applications.owned(db,user.id,id);profile,facts=applications.source(db,user.id)
    processor=request.app.state.resume_tailoring_processor;processor.reserve()
    try:row=service.new_version(db,app,profile,facts,payload);db.commit()
    except Exception:db.rollback();processor.capacity.release();raise
    processor.submit(row.id,row.active_operation);return service.public(row)

@router.get('/{id}')
def get(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):return service.public(service.owned(db,user.id,id))

@router.post('/{id}/actions')
def action(id:str,payload:ResumeAction,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=service.owned(db,user.id,id)
    if row.revision!=payload.expected_revision:raise AppError(409,'resume_revision_conflict','This version changed. Refresh before continuing.')
    if payload.action=='cancel':
        if row.status in {'approved','attached','replaced'}:raise AppError(409,'resume_immutable','Approved versions are immutable. Create another version to replace one.')
        service.change(db,row,payload.expected_revision,status='cancelled',stage='Cancelled',active_operation=None);service.history(row,'cancelled');service.event(db,row,'RUN_FINISHED',stage='Resume tailoring cancelled');db.commit()
    else:
        if row.active_operation:raise AppError(409,'resume_tailoring_in_progress','Wait for the current operation or cancel it first.')
        service.check_current(db,row)
        if payload.action=='approve':
            if not payload.reviewed:raise AppError(422,'resume_review_required','Review the facts, changes and one-page PDF before approving.')
            service.approve(db,row,payload.expected_revision);db.commit()
        elif payload.action=='attach':service.attach(db,row,payload.expected_revision,payload.expected_application_revision);db.commit()
        elif payload.action=='compile':
            if row.status not in {'generated','failed'} or not row.data.get('validation',{}).get('valid'):raise AppError(409,'resume_new_version_required','Create a new version to change a rendered or approved resume.')
            operation=new_id();processor=request.app.state.resume_tailoring_processor;processor.reserve()
            try:
                service.change(db,row,payload.expected_revision,status='rendering',stage='Compiling resume',active_operation=operation,error_code=None);service.event(db,row,'RUN_STARTED',stage=row.stage);db.commit()
            except Exception:db.rollback();processor.capacity.release();raise
            processor.submit(row.id,operation,'compile')
    return service.public(row)

@router.get('/{id}/artifacts/{kind}')
def artifact(id:str,kind:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=service.owned(db,user.id,id)
    files={'pdf':('document.pdf','application/pdf'),'preview':('preview.png','image/png'),'source':('document.tex','text/plain'),'class':('resume.cls','text/plain')}
    if kind=='compile-log':
        attempts=row.data.get('render',{}).get('attempts',[])
        if not attempts:raise AppError(404,'resume_artifact_missing','No compiler log is available.')
        files[kind]=(f'attempt-{min(3,max(1,int(attempts[-1]["attempt"])))}.log','text/plain')
    if kind not in files:raise AppError(404,'resume_artifact_missing','Artifact not found.')
    name,mime=files[kind];path=Path(row.artifact_dir)/name if row.artifact_dir else None
    if not path or not path.is_file():raise AppError(404,'resume_artifact_missing','This artifact has not been produced yet.')
    if kind in {'pdf','preview'} and (row.status in {'failed','cancelled','validation_failed'} or not row.data.get('render',{}).get('quality',{}).get('valid')):raise AppError(409,'resume_pdf_unvalidated','A validated one-page PDF is not available yet.')
    return Response(path.read_bytes(),media_type=mime,headers={'Content-Disposition':f'{"inline" if kind=="preview" else "attachment"}; filename="tailored-resume-v{row.version}.{name.rsplit(".",1)[-1]}"','Cache-Control':'no-store'})
