"""Local authenticated cover-letter workspace. Never submits an application."""
from hashlib import sha256
from pathlib import Path
from fastapi import APIRouter,Depends,Request,UploadFile,Form,Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.dependencies import current_user,get_db
from app.core.errors import AppError
from app.db.models import User,SignatureAsset,CoverLetterVersion,new_id
from app.schemas.cover_letters import CoverStart,CoverAction
from app.services import cover_letters as service
from app.services import applications
from app.services.cover_letter_render import readiness as render_readiness,signature_image

router=APIRouter(prefix='/cover-letters',tags=['Cover letters'])

@router.get('/readiness')
def readiness(request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    try:profile,facts=applications.source(db,user.id);ready=bool(facts.get('name'));name=facts.get('name','')
    except AppError:ready=False;name=''
    return {**render_readiness(request.app.state.settings),'profile_ready':ready,'candidate_name':name,
        'groq_configured':bool(request.app.state.settings.groq_keys),'signature_required_before_render':True,
        'message':'Upload your own signature and approve its use, or explicitly choose a letter with a typed name only.'}

@router.get('/signatures')
def signatures(user:User=Depends(current_user),db:Session=Depends(get_db)):
    return [{'id':s.id,'signer_name':s.signer_name,'sha256':s.sha256,'metadata':s.metadata_json,'created_at':s.created_at} for s in db.scalars(select(SignatureAsset).where(SignatureAsset.user_id==user.id).order_by(SignatureAsset.created_at.desc()).limit(30))]

@router.post('/signatures',status_code=201)
async def upload_signature(file:UploadFile,signer_name:str=Form(...,min_length=1,max_length=120),approved:bool=Form(False),user:User=Depends(current_user),db:Session=Depends(get_db)):
    if not approved:raise AppError(422,'signature_ownership_required','Confirm that this is your signature and that you authorise storing it locally.')
    profile,facts=applications.source(db,user.id)
    if ' '.join(signer_name.casefold().split())!=' '.join(facts.get('name','').casefold().split()):raise AppError(422,'signature_signer_mismatch','Signer name must match the confirmed profile.')
    try:data=await file.read(2*1024*1024+1)
    finally:await file.close()
    content,metadata=signature_image(data)
    row=SignatureAsset(user_id=user.id,signer_name=signer_name,sha256=sha256(content).hexdigest(),content=content,metadata_json={**metadata,'user_supplied':True,'storage_approved':True})
    db.add(row);db.commit()
    return {'id':row.id,'signer_name':row.signer_name,'metadata':row.metadata_json}

@router.get('/signatures/{id}/image')
def signature_preview(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=db.scalar(select(SignatureAsset).where(SignatureAsset.id==id,SignatureAsset.user_id==user.id))
    if not row:raise AppError(404,'signature_not_found','Signature not found in this workspace.')
    return Response(row.content,media_type='image/png',headers={'Content-Disposition':'inline; filename="signature.png"'})

@router.get('')
def listing(application_id:str=Query(...,max_length=36),user:User=Depends(current_user),db:Session=Depends(get_db)):
    applications.owned(db,user.id,application_id)
    return [service.public(row) for row in db.scalars(select(CoverLetterVersion).where(CoverLetterVersion.user_id==user.id,CoverLetterVersion.application_id==application_id).order_by(CoverLetterVersion.version.desc()).limit(100))]

@router.post('/applications/{id}',status_code=202)
def generate(id:str,payload:CoverStart,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    app=applications.owned(db,user.id,id);profile,facts=applications.source(db,user.id)
    processor=request.app.state.cover_letter_processor;processor.reserve()
    try:row,operation=service.new_version(db,app,profile,facts,payload);db.commit()
    except Exception:db.rollback();processor.capacity.release();raise
    processor.submit(row.id,operation)
    return service.public(row)

@router.get('/{id}')
def get(id:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    return service.public(service.owned(db,user.id,id))

@router.post('/{id}/actions')
def action(id:str,payload:CoverAction,request:Request,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=service.owned(db,user.id,id)
    if row.revision!=payload.expected_revision:raise AppError(409,'cover_revision_conflict','Letter changed. Refresh before continuing.')
    if payload.action=='cancel':
        if row.status in {'approved','attached','replaced'}:raise AppError(409,'cover_immutable','Approved versions are immutable. Create a new version to replace one.')
        service.change(db,row,payload.expected_revision,status='cancelled',stage='Cancelled',active_operation=None)
        service.event(db,row,'RUN_FINISHED',stage='Cover letter cancelled');db.commit()
    else:
        if row.active_operation:raise AppError(409,'cover_in_progress','Wait for this operation or cancel it first.')
        service.check_current(db,row)
        if payload.action=='approve':
            if not payload.reviewed:raise AppError(422,'cover_review_required','Review the text, signature and PDF before approving.')
            service.approve(db,row,payload.expected_revision);db.commit()
        elif payload.action=='attach':
            service.attach(db,row,payload.expected_revision,payload.expected_application_revision);db.commit()
        elif payload.action=='render':
            if row.status not in {'waiting_for_signature','draft_ready','failed'} or not row.data.get('validation',{}).get('valid'):raise AppError(409,'cover_new_version_required','Generate an edited version to change a rendered or approved letter.')
            asset=None
            if payload.include_signature:
                asset=service.signature(db,user.id,payload.signature_id,row.candidate_snapshot['name'])
                if not payload.signature_approved:raise AppError(422,'signature_approval_required','Approve this signature for this letter.')
            options={**row.request,'include_signature':payload.include_signature,'signature_id':asset.id if asset else None,
                'signature_approved':bool(asset),'signature_width_mm':payload.signature_width_mm}
            operation=new_id();processor=request.app.state.cover_letter_processor;processor.reserve()
            try:
                service.change(db,row,payload.expected_revision,status='rendering',stage='Rendering',request=options,signature_id=asset.id if asset else None,active_operation=operation,error_code=None)
                service.event(db,row,'RUN_STARTED',stage='Cover letter rendering');db.commit()
            except Exception:db.rollback();processor.capacity.release();raise
            processor.submit(row.id,operation,'render')
    return service.public(row)

@router.get('/{id}/artifacts/{kind}')
def artifact(id:str,kind:str,user:User=Depends(current_user),db:Session=Depends(get_db)):
    row=service.owned(db,user.id,id)
    files={'pdf':('letter.pdf','application/pdf'),'preview':('preview.png','image/png'),'source':('letter.tex','text/plain')}
    if kind=='compile-log':
        audit=row.data.get('render',{});attempts=audit.get('attempts',[])
        if not attempts:raise AppError(404,'cover_artifact_missing','No compiler log is available yet.')
        # Only a server-generated bounded reference; never accept a path from a client.
        last=min(3,max(1,int(attempts[-1]['attempt'])))
        files[kind]=(f'attempt-{last}.log','text/plain')
    if kind not in files:raise AppError(404,'cover_artifact_missing','Document artifact not found.')
    name,mime=files[kind];path=Path(row.artifact_dir)/name if row.artifact_dir else None
    if not path or not path.is_file():raise AppError(404,'cover_artifact_missing','This artifact has not been produced yet.')
    if kind in {'pdf','preview'} and (row.status in {'failed','cancelled'} or not row.data.get('render',{}).get('quality',{}).get('valid')):
        raise AppError(409,'cover_pdf_unvalidated','A validated preview is not available. Inspect the error and regenerate.')
    disposition='inline' if kind=='preview' else 'attachment'
    return Response(path.read_bytes(),media_type=mime,headers={'Content-Disposition':f'{disposition}; filename="cover-letter-v{row.version}.{name.rsplit(".",1)[-1]}"','Cache-Control':'no-store'})
