from fastapi import APIRouter, Depends, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import current_user, get_db
from app.core.errors import AppError
from app.db.models import Document, User
from app.schemas.workspace import DocumentResponse
from app.services.documents import validate_upload
from app.services.profiles import add_activity

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get("/policy")
def policy(request: Request, user: User = Depends(current_user)):
    return {"formats": ["pdf", "docx", "txt"], "max_bytes": request.app.state.settings.max_document_bytes,
        "max_documents": request.app.state.settings.max_documents_per_user, "extraction_available": False}


@router.get("", response_model=list[DocumentResponse])
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Document).where(Document.user_id == user.id).order_by(Document.created_at.desc())).all()


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload(request: Request, response: Response, file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    try:
        name, mime, content, digest = await validate_upload(file, settings.max_document_bytes)
    finally:
        await file.close()
    existing = db.scalar(select(Document).where(Document.user_id == user.id, Document.sha256 == digest))
    if existing:
        response.status_code = 200
        return existing
    count = db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.id))
    if count >= settings.max_documents_per_user:
        raise AppError(409, "document_limit", "Your document library is full. Remove a document before uploading another.")
    document = Document(user_id=user.id, filename=name, content_type=mime, content=content, size_bytes=len(content), sha256=digest)
    db.add(document)
    add_activity(db, user.id, "document_uploaded", "Document stored. Profile extraction is planned for the next phase.")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Document).where(Document.user_id == user.id, Document.sha256 == digest))
        if not existing:
            raise
        response.status_code = 200
        return existing
    db.refresh(document)
    return document


def owned_document(db, user_id, document_id):
    document = db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user_id))
    if document is None:
        raise AppError(404, "document_not_found", "This document was not found in your workspace.")
    return document


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return owned_document(db, user.id, document_id)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(owned_document(db, user.id, document_id))
    add_activity(db, user.id, "document_deleted", "Document and its stored contents removed.")
    db.commit()
    return Response(status_code=204)
