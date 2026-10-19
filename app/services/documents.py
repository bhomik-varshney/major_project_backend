from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
import re

from fastapi import UploadFile

from app.core.errors import AppError

MIME_TYPES = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain"}


async def validate_upload(file: UploadFile, limit: int):
    name = PurePosixPath((file.filename or "document").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    if len(name) > 180:
        raise AppError(422, "filename_invalid", "Use a filename of at most 180 characters.")
    extension = PurePosixPath(name).suffix.lower()
    if extension not in MIME_TYPES:
        raise AppError(415, "document_type", "Choose a PDF, DOCX, or UTF-8 TXT file.")
    mime = MIME_TYPES[extension]
    if file.content_type not in {mime, "application/octet-stream"}:
        raise AppError(415, "document_type", "The file type does not match its filename.")
    content = bytearray()
    while chunk := await file.read(64 * 1024):
        content.extend(chunk)
        if len(content) > limit:
            raise AppError(413, "document_size", "This document exceeds the upload limit.")
    data = bytes(content)
    if not data:
        raise AppError(422, "document_empty", "The selected document is empty.")
    try:
        if extension == ".pdf":
            from pypdf import PdfReader
            if not data.startswith(b"%PDF-"):
                raise ValueError()
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted or not len(reader.pages):
                raise ValueError()
        elif extension == ".docx":
            with ZipFile(BytesIO(data)) as archive:
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
                    raise ValueError()
                if sum(info.file_size for info in archive.infolist()) > 30 * 1024 * 1024:
                    raise ValueError()
                from xml.etree import ElementTree
                ElementTree.fromstring(archive.read("word/document.xml"))
        else:
            text = data.decode("utf-8-sig")
            if not text.strip() or "\x00" in text:
                raise ValueError()
    except (Exception,) as exc:
        # Parser details may include personal content. Never expose them.
        raise AppError(422, "document_invalid", "This document is unreadable, corrupt, or password protected. Choose another file.") from exc
    return name, mime, data, sha256(data).hexdigest()
