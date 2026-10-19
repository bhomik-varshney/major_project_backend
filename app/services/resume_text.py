"""Bounded local parsing. Original files stay private in database storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader, mult

from app.core.errors import AppError


def extract_text(content: bytes, filename: str, document_id: str, settings):
    root = settings.document_storage_path
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    blocks, warnings = [], []
    page_count = None
    total = 0

    def add(text, page=None, paragraph=None, **metadata):
        nonlocal total
        text = text.replace("\x00", "").rstrip()
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
                reader = PdfReader(path, strict=True)
                page_count = len(reader.pages)
                if page_count > settings.max_extraction_pages:
                    raise AppError(422, "document_page_limit", "This PDF has too many pages. Upload a shorter document.")
                for number, page in enumerate(reader.pages, 1):
                    stream = page.get_contents()
                    if stream is not None and len(stream.get_data()) > 4 * 1024 * 1024:
                        raise AppError(422, "document_complexity", "This PDF page is too complex. Export a simpler text-based PDF.")
                    fragments = []
                    def visitor(text, cm, tm, font, size):
                        if text.strip():
                            matrix = mult(tm, cm)
                            fragments.append((text, float(matrix[4]), float(matrix[5]), float(size),
                                bool(font and "bold" in str(font.get("/BaseFont", "")).lower())))
                    plain = page.extract_text(visitor_text=visitor) or ""
                    # Preserve content-stream order. Coordinates are evidence, not a license
                    # to join columns; uncertain column pages require candidate review.
                    xs = [x for _, x, _, _, _ in fragments]
                    width = float(page.mediabox.width)
                    uncertain = any(x < width * .4 for x in xs) and any(x > width * .5 for x in xs)
                    if uncertain:
                        warnings.append(f"Page {number}: possible multi-column layout; reading order is uncertain. Columns were not merged.")
                    if not plain.strip():
                        warnings.append(f"Page {number}: no usable text; image-only/scanned pages require future OCR.")
                    for text, x, y, size, bold in fragments:
                        for line_number, line in enumerate(text.splitlines(), 1):
                            add(line, page=number, line=line_number, position={"x": x, "y": y, "font_size": size},
                                bold=bold, page_width=width, reading_order_uncertain=uncertain)
                    if not fragments and plain.strip():
                        for line_number, line in enumerate(plain.splitlines(), 1):
                            add(line, page=number, line=line_number, reading_order_uncertain=True)
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
    if not any(block["text"].strip() for block in blocks):
        raise AppError(422, "no_readable_text", "No readable text was found. Scanned PDFs need OCR, which is not available yet. Upload a text-based file or use manual entry.")
    if total >= settings.max_extraction_chars and not warnings:
        warnings.append("Text limit reached. Some content may be omitted.")
    return blocks, page_count, warnings
