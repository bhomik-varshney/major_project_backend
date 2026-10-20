"""Bounded local parsing. Original files stay private in database storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from app.core.errors import AppError


def extract_text(content: bytes, filename: str, document_id: str, settings, *, allow_empty=False):
    root = settings.document_storage_path
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    blocks, warnings = [], []
    page_count = None
    total = 0

    def add(text, page=None, paragraph=None, **metadata):
        nonlocal total
        text = text.replace("\x00", "")
        available = settings.max_extraction_chars - total
        if available <= 0:
            return
        if len(text) > available:
            warnings.append("Text limit reached. Review the partial result and enter omitted details manually.")
        text = text[:available]
        total += len(text)
        blocks.append({"block_id": len(blocks) + 1, "page": page, "paragraph": paragraph, "text": text, **metadata})

    suffix = Path(filename).suffix.lower()
    # Only server-issued UUIDs become internal names; original names are never paths.
    from uuid import UUID
    safe_id = str(UUID(document_id))
    try:
        with TemporaryDirectory(prefix=f"{safe_id}-", dir=root) as scratch:
            path = Path(scratch) / f"{safe_id}{suffix}"
            with open(path, "xb", opener=lambda p, f: __import__("os").open(p, f, 0o600)) as out:
                out.write(content)
            if suffix == ".pdf":
                import pypdfium2 as pdfium
                from app.services.pdf_native import native_lines,columns_uncertain
                reader = PdfReader(path, strict=True)
                page_count = len(reader.pages)
                if page_count > settings.max_extraction_pages:
                    raise AppError(422, "document_page_limit", "This PDF has too many pages. Upload a shorter document.")
                for page in reader.pages:
                    stream=page.get_contents()
                    if stream is not None and len(stream.get_data())>4*1024*1024:
                        raise AppError(422,'document_complexity','This PDF page is too complex. Export a simpler text-based PDF.')
                with pdfium.PdfDocument(content) as native_pdf:
                    native_pages=[];remaining=settings.max_extraction_chars
                    for number in range(page_count):
                        if remaining<=0:
                            native_pages.append([])
                            continue
                        native_page=native_pdf[number]
                        try:
                            lines=native_lines(native_page,remaining)
                            native_pages.append(lines);remaining-=sum(len(line['text']) for line in lines)
                        finally:native_page.close()
                for number, page in enumerate(reader.pages, 1):
                    lines=native_pages[number-1]
                    annotations=[]
                    for ref in page.get('/Annots',[]):
                        annotation=ref.get_object();action=annotation.get('/A',{})
                        action=action.get_object() if hasattr(action,'get_object') else action
                        uri=action.get('/URI','');rect=annotation.get('/Rect')
                        if isinstance(uri,str) and uri.startswith(('https://','http://')) and len(uri)<=500 and rect and len(rect)==4:
                            annotations.append((uri,[float(v) for v in rect]))
                    width = float(page.mediabox.width)
                    uncertain=columns_uncertain(lines,width)
                    if uncertain:
                        warnings.append(f"Page {number}: possible multi-column layout; reading order is uncertain. Columns were not merged.")
                    if not lines:
                        warnings.append(f"Page {number}: no selectable text; OCR is needed.")
                    for line_number,line in enumerate(lines,1):
                        add(page=number,line=line_number,page_width=width,page_height=float(page.mediabox.height),reading_order_uncertain=uncertain,**line)
                        # Link destinations are original PDF annotation data, kept
                        # distinct from visible native text and attached in order.
                        box=line['native_bbox'];linked=[]
                        for item in annotations:
                            uri,rect=item
                            cx,cy=(rect[0]+rect[2])/2,(rect[1]+rect[3])/2
                            if box[0]-2<=cx<=box[2]+2 and box[1]-3<=cy<=box[3]+3:
                                linked.append(item)
                        for uri,rect in linked:
                            add(uri,page=number,line=line_number,source_engine='pdf_annotation',source_kind='link_destination',
                                position={'x':rect[0],'y':rect[1],'font_size':max(1,rect[3]-rect[1])},native_bbox=rect,
                                page_width=width,page_height=float(page.mediabox.height),reading_order_uncertain=uncertain)
                            annotations.remove((uri,rect))
            elif suffix == ".docx":
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                with ZipFile(path) as archive:
                    xml = archive.read("word/document.xml")
                if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                    raise ValueError("Unsupported XML declaration")
                body = ET.fromstring(xml).find("w:body", ns)
                if body is None:
                    # Tolerate the minimal legacy upload fixture; no invented text.
                    raise ValueError("No DOCX body")
                def paragraph_text(paragraph):
                    return "".join(node.text or "" if node.tag == f"{{{ns['w']}}}t" else "\t" if node.tag == f"{{{ns['w']}}}tab" else "\n"
                        for node in paragraph.iter() if node.tag in {f"{{{ns['w']}}}t", f"{{{ns['w']}}}tab", f"{{{ns['w']}}}br"})
                paragraph_number = 0
                for element in body:
                    if element.tag == f"{{{ns['w']}}}p":
                        paragraph_number += 1
                        style = element.find("w:pPr/w:pStyle", ns)
                        add(paragraph_text(element), paragraph=paragraph_number,
                            style=style.get(f"{{{ns['w']}}}val", "") if style is not None else "",
                            bold=element.find(".//w:rPr/w:b", ns) is not None,
                            list_item=element.find("w:pPr/w:numPr", ns) is not None)
                    elif element.tag == f"{{{ns['w']}}}tbl":
                        for row_number, row in enumerate(element.findall("w:tr", ns), 1):
                            paragraph_number += 1
                            cells = ["\n".join(paragraph_text(p) for p in cell.findall(".//w:p", ns)) for cell in row.findall("w:tc", ns)]
                            add(" | ".join(cells), paragraph=paragraph_number, table_row=row_number, cells=cells)
            else:
                for number, paragraph in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                    add(paragraph, paragraph=number)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(422, "text_extraction_failed", "Text could not be read from this document. Export it again or enter your profile manually.") from exc
    if not allow_empty and not any(block["text"].strip() for block in blocks):
        raise AppError(422, "no_readable_text", "No selectable text was found. Use the document-processing workflow for OCR, upload a text-based file, or use manual entry.")
    if total >= settings.max_extraction_chars and not warnings:
        warnings.append("Text limit reached. Some content may be omitted.")
    return blocks, page_count, warnings
