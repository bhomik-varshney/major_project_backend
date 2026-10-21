"""Bounded local evidence preparation in a killable process, never a profile writer."""
from collections import defaultdict
from hashlib import sha256
from io import BytesIO
import csv
import copy
import logging
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import shutil
import signal
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from zipfile import ZipFile

from pypdf import PdfReader
from app.core.errors import AppError
from app.schemas.intelligence import LayoutBlock, Region
from app.services.resume_structure import detect_heading, normalize
from app.services.resume_text import extract_text
from app.services.document_conversion import convert_docx


def converter(settings):
    return settings.libreoffice_path or shutil.which('soffice') or ('/Applications/LibreOffice.app/Contents/MacOS/soffice' if Path('/Applications/LibreOffice.app/Contents/MacOS/soffice').exists() else None)


def region_for(block):
    if block.get('native_bbox') and block.get('page_height'):
        left,bottom,right,top=block['native_bbox'];w,h=block.get('page_width',612),block['page_height']
        x,y=max(0,min(.999,left/w)),max(0,min(.999,1-top/h))
        return Region(x=x,y=y,width=min(1-x,max(.005,(right-left)/w)),height=min(1-y,max(.005,(top-bottom)/h))).model_dump()
    p = block.get('position')
    if not p or not block.get('page_height'):
        return None
    w, h = block.get('page_width', 612), block['page_height']
    x, y = max(0, min(.999, p['x']/w)), max(0, min(.999, 1-(p['y']+p['font_size'])/h))
    width = min(1-x, max(.005, len(block['text'])*p['font_size']*.52/w))
    return Region(x=x, y=y, width=width, height=min(1-y, max(.005, p['font_size']*1.4/h))).model_dump()


def local_layout(blocks):
    result, states = [], {}
    for order, block in enumerate(blocks):
        page = block.get('page')
        if not page or not block['text'].strip():
            continue
        column = 'right' if block.get('position',{}).get('x',0) > block.get('page_width',612)*.5 else 'left'
        heading = detect_heading(block['text'])
        if heading:
            states[(page,column)] = heading[0]
        section = states.get((page,column),'contact')
        kind = 'heading' if heading else 'table' if block.get('cells') else 'contact' if section == 'contact' else 'text'
        result.append(LayoutBlock(id=f'text_{block["block_id"]}',page=page,section=section,
            raw_text=block['text'][:2000],block_type=kind,region=region_for(block),
            source_block_ids=[block['block_id']],reading_order=order,confidence=.5,
            method='text',status='supported',warnings=['Approximate text geometry; not visual analysis.']).model_dump())
    return result


def command(args, timeout=15):
    # No shell, no network conversions, private scratch and bounded subprocess time.
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=timeout, check=True)


def read_ocr(png, page, first_id, scratch, char_budget):
    executable = shutil.which('tesseract')
    if not executable:
        return [], ['OCR is unavailable: install local Tesseract to read image-based text.']
    source = Path(scratch)/f'ocr-{page}.png'
    source.write_bytes(png); source.chmod(0o600)
    try:
        raw = command([executable,str(source),'stdout','--psm','3','tsv'], timeout=12).stdout
        if len(raw)>2_000_000:
            raise ValueError()
        from PIL import Image
        with Image.open(BytesIO(png)) as image:
            width,height=image.size
        groups=defaultdict(list)
        for row in csv.DictReader(raw.decode('utf-8').splitlines(),delimiter='\t',quoting=csv.QUOTE_NONE):
            text=row.get('text','').strip()
            if text and float(row['conf'])>=0:
                groups[(row['block_num'],row['par_num'],row['line_num'])].append(row)
        blocks=[]; total=0
        for words in groups.values():
            text=' '.join(w['text'] for w in words)[:min(2000,max(0,char_budget-total))]
            if not text:
                break
            total+=len(text)
            left=min(int(w['left']) for w in words); top=min(int(w['top']) for w in words)
            right=max(int(w['left'])+int(w['width']) for w in words); bottom=max(int(w['top'])+int(w['height']) for w in words)
            confidence=sum(float(w['conf']) for w in words)/len(words)/100
            blocks.append(dict(block_id=first_id+len(blocks),page=page,text=text,
                region=Region(x=left/width,y=top/height,width=(right-left)/width,height=(bottom-top)/height).model_dump(),
                confidence=round(confidence,3),warnings=['OCR may misread names, dates and punctuation.'] if confidence<.85 else []))
        return blocks, [] if blocks else [f'Page {page}: OCR found no readable text.']
    except (OSError,subprocess.SubprocessError,ValueError,KeyError):
        return [],[f'Page {page}: OCR failed or timed out. Manual review is required.']
    finally:
        source.unlink(missing_ok=True)


def prepare_document(content,filename,document_id,settings,scratch,progress=lambda stage:None,snapshot=lambda data:None):
    progress('extracting_text')
    blocks,page_count,warnings=extract_text(content,filename,document_id,settings,allow_empty=True)
    for block in blocks:
        block['extraction_method']='text'
    exact=[dict(block_id=b['block_id'],text=b['text'],page=b.get('page'),paragraph=b.get('paragraph'),order=i,
        method='text',region=region_for(b),metadata={k:v for k,v in b.items() if k not in {'block_id','text','page','paragraph'}}) for i,b in enumerate(blocks)]
    pages,ocr,image_pages=[],[],[]
    conversion=None
    page_quality=[]; has_tables=any(b.get('cells') for b in blocks)
    def partial():
        return dict(blocks=blocks,exact_text=exact,page_count=page_count,warnings=list(warnings),pages=pages,ocr=ocr,
            layout=local_layout(blocks),conversion=conversion,quality={'classification':'incomplete','page_count':page_count,
                'selectable_text':any(b['text'].strip() for b in exact),'tables_detected':has_tables,
                'pages':copy.deepcopy(page_quality),
                'limitations':['Analysis is incomplete; some page previews or OCR could not finish.']})
    snapshot(partial())
    suffix=Path(filename).suffix.lower()
    pdf=content if suffix=='.pdf' else None
    if suffix=='.docx':
        executable=converter(settings)
        if executable:
            result=convert_docx(content,executable,scratch)
            conversion={'status':'completed' if result.pdf is not None else 'failed',
                'attempts':result.attempts,'error':result.error}
            pdf=result.pdf
            if result.error:
                warnings.append(result.error)
            snapshot(partial())
        else:
            warnings.append('DOCX previews unavailable: local LibreOffice is not installed. Exact paragraphs/tables remain available.')
    if suffix=='.docx':
        with ZipFile(BytesIO(content)) as archive:
            xml=archive.read('word/document.xml')
            has_tables=has_tables or b'<w:tbl' in xml
            if b'<w:drawing' in xml:
                warnings.append('DOCX contains drawings. Review page previews; paragraph extraction cannot locate image text.')
    if pdf:
        reader=PdfReader(BytesIO(pdf),strict=True);page_count=len(reader.pages)
        if page_count>settings.max_extraction_pages:
            raise AppError(422,'document_page_limit','This document has too many pages. Upload a shorter document.')
        for number,page in enumerate(reader.pages,1):
            image_area=0.0; rectangle_count=0
            def inspect_operator(operator,operands,cm,tm):
                nonlocal image_area,rectangle_count
                if operator==b're':
                    rectangle_count+=1
                elif operator==b'Do' and operands:
                    objects=page.get('/Resources',{}).get('/XObject',{})
                    objects=objects.get_object() if hasattr(objects,'get_object') else objects
                    obj=objects.get(operands[0])
                    obj=obj.get_object() if hasattr(obj,'get_object') else obj
                    if obj and obj.get('/Subtype')=='/Image':
                        image_area+=abs(float(cm[0])*float(cm[3])-float(cm[1])*float(cm[2]))
            text=page.extract_text(visitor_operand_before=inspect_operator) or ''
            images=bool(page.get('/Resources',{}).get('/XObject'))
            area=float(page.mediabox.width)*float(page.mediabox.height)
            coverage=min(1.0,image_area/area) if area>0 else 0.0
            if rectangle_count>=4:
                has_tables=True  # Rectangle grids are heuristic, not semantic table recognition.
            needs_ocr=len(text.strip())<40 or (images and (len(text.strip())<250 or coverage>.20))
            page_quality.append(dict(page=number,selectable_characters=len(text.strip()),image_objects=images,
                image_coverage=round(coverage,3),ocr_recommended=needs_ocr,
                ocr_status='not_processed' if needs_ocr else 'not_required',ocr_blocks=0,
                ocr_reason='OCR is needed but has not run on this page.' if needs_ocr else 'Readable native text is available; no substantial image region requires OCR.'))
            if images or needs_ocr:
                image_pages.append(number)
        if suffix=='.docx':
            # Conversion establishes a rendered location only when a paragraph/row
            # matches one page uniquely. Original paragraph evidence remains intact.
            page_texts=[normalize(p.extract_text() or '') for p in reader.pages]
            for block,original in zip(blocks,exact):
                pieces=[normalize(c) for c in block.get('cells',[]) if c.strip()] or [normalize(block['text'])]
                matches=[i+1 for i,text in enumerate(page_texts) if all(piece and piece in text for piece in pieces)]
                if len(matches)==1:
                    block['rendered_page']=matches[0]
                    original['metadata']['rendered_page']=matches[0]
        progress('rendering_pages')
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(pdf) as doc:
            retained=0
            for index in range(min(len(doc),settings.render_max_pages)):
                try:
                    page=doc[index]
                    try:
                        w,h=page.get_size()
                        if w<=0 or h<=0:
                            raise ValueError()
                        scale=min(2.5,settings.render_max_dimension/max(w,h))
                        bitmap=page.render(scale=scale,rev_byteorder=True)
                        try:
                            image=bitmap.to_pil();out=BytesIO();image.save(out,format='PNG');png=out.getvalue();iw,ih=image.size;image.close()
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
                    if len(png)>3*1024*1024 or retained+len(png)>18*1024*1024:
                        warnings.append(f'Page {index+1}: preview storage limit reached; exact text remains available.')
                        continue
                    retained+=len(png)
                    pages.append(dict(id=str(uuid4()),page=index+1,width=iw,height=ih,sha256=sha256(png).hexdigest(),
                        bytes=len(png),media_type='image/png',renderer='pdfium' if suffix=='.pdf' else 'libreoffice+pdfium',content=png))
                    if page_quality[index]['ocr_recommended'] and settings.ocr_enabled:
                        progress('ocr_processing')
                        found,notes=read_ocr(png,index+1,max([b['block_id'] for b in blocks]+[b['block_id'] for b in ocr]+[0])+1,scratch,settings.max_extraction_chars-sum(len(b['text']) for b in blocks))
                        status='completed' if found else 'unavailable' if not shutil.which('tesseract') else 'failed' if any('failed' in note for note in notes) else 'no_text'
                        page_quality[index].update(ocr_status=status,ocr_blocks=len(found),ocr_reason='Local Tesseract produced recognition evidence; compare it with the page.' if found else ' '.join(notes))
                        ocr.extend(found);warnings.extend(notes)
                        # OCR on a readable mixed page is retained separately. Only novel
                        # lines enter the evidence stream; no duplicate selectable text.
                        native=normalize(page_quality[index].get('selectable_text','') or reader.pages[index].extract_text() or '')
                        for item in found:
                            if normalize(item['text']) not in native:
                                blocks.append(dict(block_id=item['block_id'],page=index+1,paragraph=None,text=item['text'],
                                    extraction_method='ocr',ocr_confidence=item['confidence'],ocr_region=item['region']))
                        if not found:
                            warnings.append(f'Page {index+1}: extraction is incomplete; no OCR evidence was recovered.')
                    elif page_quality[index]['ocr_recommended']:
                        page_quality[index].update(ocr_status='disabled',ocr_reason='OCR is needed but disabled in backend settings.')
                        warnings.append(f'Page {index+1}: OCR is disabled. Image-based text is unresolved.')
                    snapshot(partial())
                except (OSError,ValueError,RuntimeError):
                    if page_quality[index]['ocr_recommended']:
                        page_quality[index].update(ocr_status='not_processed',ocr_reason='The page preview could not be rendered, so OCR could not run.')
                    warnings.append(f'Page {index+1}: preview could not be rendered; other pages and exact text were retained.')
        if page_count>settings.render_max_pages:
            warnings.append(f'Only the first {settings.render_max_pages} pages have previews/OCR. Remaining exact text is retained; extraction may be incomplete.')
    native_pages={b.get('page') for b in exact if b['text'].strip()}
    empty_pages=[p['page'] for p in page_quality if p['selectable_characters']<40]
    multi=any(b.get('reading_order_uncertain') for b in blocks)
    quality=dict(classification='image_only' if empty_pages and len(empty_pages)==page_count else 'mixed' if empty_pages or any(p['ocr_recommended'] and p['selectable_characters']>=40 for p in page_quality) else 'layout_complex' if multi or has_tables else 'text_readable',
        page_count=page_count,selectable_text=bool(native_pages or any(b['text'].strip() for b in exact)),
        likely_scanned=bool(empty_pages),columns='multiple_suspected' if multi else 'one_or_unknown',
        tables_detected=has_tables,image_heavy_pages=image_pages,ocr_recommended=bool(empty_pages or any(p['ocr_recommended'] for p in page_quality)),
        vision_recommended=bool(multi or has_tables or image_pages),pages=page_quality,
        limitations=['Layout detection is heuristic. PDF table structure and image coverage may be incomplete.'])
    return dict(blocks=blocks,exact_text=exact,page_count=page_count,warnings=list(dict.fromkeys(warnings)),pages=pages,ocr=ocr,
        layout=local_layout(blocks),quality=quality,conversion=conversion)


def _prepare_child(content,filename,document_id,settings,scratch,output):
    if os.name=='posix':
        os.setsid()  # Cancellation kills this worker and its converter/OCR descendants.
    logging.disable(logging.CRITICAL)
    try:
        result=prepare_document(content,filename,document_id,settings,scratch,lambda stage:output.put(('stage',stage)),lambda data:output.put(('partial',copy.deepcopy(data))))
        output.put(('result',result))
    except Exception as exc:
        output.put(('error',{'code':exc.code if isinstance(exc,AppError) else 'document_analysis_failed',
            'message':exc.message if isinstance(exc,AppError) else 'Document analysis failed. Export a simpler PDF or retry.'}))


def prepare_isolated(content,filename,document_id,settings,progress=lambda stage:None,*,child_target=_prepare_child):
    context=multiprocessing.get_context('spawn');output=context.Queue()
    settings.document_storage_path.mkdir(parents=True,exist_ok=True,mode=0o700)
    deadline=time.monotonic()+settings.preparation_timeout_seconds
    partial=None
    with TemporaryDirectory(prefix='analysis-',dir=settings.document_storage_path) as scratch:
        process=context.Process(target=child_target,args=(content,filename,document_id,settings,str(Path(scratch).resolve()),output))
        process.start()
        try:
            while time.monotonic()<deadline:
                progress(None)
                try:
                    kind,data=output.get(timeout=.2)
                except Empty:
                    if not process.is_alive():
                        if partial:
                            partial['warnings'].append('Local analysis stopped early. Recovered text/pages are retained; review for omissions.')
                            return partial
                        raise AppError(422,'document_analysis_failed','Document analysis stopped unexpectedly. Retry with a simpler file.')
                    continue
                if kind=='stage':
                    progress(data)
                elif kind=='partial':
                    partial=data
                elif kind=='result':
                    return data
                else:
                    raise AppError(422,data['code'],data['message'])
            if partial:
                partial['warnings'].append('Local page analysis timed out. Recovered evidence is retained; remaining pages/OCR need manual review.')
                return partial
            raise AppError(422,'document_analysis_timeout','Document analysis timed out. Upload a shorter PDF or use manual entry.')
        finally:
            process.join(timeout=.3)
            group=None
            if process.is_alive():
                try:
                    if os.name=='posix' and os.getpgid(process.pid)==process.pid:
                        group=process.pid
                        os.killpg(group,signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
            process.join(timeout=2)
            if group is not None:
                try:
                    os.killpg(group,signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.is_alive():
                process.kill();process.join(timeout=2)
            output.close();output.cancel_join_thread()
