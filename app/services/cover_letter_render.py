"""Trusted template -> isolated, no-shell XeLaTeX/LuaLaTeX -> checked PDF/PNG.

No LibreOffice. Each compile owns its subprocess group and temporary directory.
Private logs/source remain under the version's private artifact directory.
"""
from io import BytesIO
from pathlib import Path
import hashlib,os,re,shutil,signal,subprocess,tempfile,unicodedata
from urllib.parse import quote
from PIL import Image,ImageOps
from pypdf import PdfReader
import pypdfium2 as pdfium
from app.core.errors import AppError
from app.services.application_transform import classify_links,safe_url
from app.schemas.cover_letters import SECTION_KEYS
from app.services.cover_letter_content import PLACEHOLDERS

TEMPLATE=Path(__file__).resolve().parents[1]/'templates'/'cover_letter'
ESCAPES={'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_','{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}

def escape(value):return ''.join(ESCAPES.get(c,c) for c in str(value) if ord(c)>=32 or c in '\n\t')

def href(url,label):
    normalized=safe_url(url)
    if not normalized:raise AppError(422,'cover_url_invalid','A professional link is malformed. Correct it in your confirmed profile.')
    # Percent-encode command delimiters, then escape TeX's remaining reserved URL characters.
    encoded=quote(normalized,safe=':/?=&%#+,;@!.-_~')
    target=''.join({'%':r'\%','#':r'\#','&':r'\&','_':r'\_','~':r'\string~'}.get(c,c) for c in encoded)
    return r'\href{'+target+'}{'+escape(label)+'}'

def signature_image(data):
    if len(data)>2*1024*1024:raise AppError(413,'signature_size','Use a signature image smaller than 2 MB.')
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in {'PNG','JPEG','WEBP'} or getattr(original,'n_frames',1)!=1:raise ValueError()
            w,h=original.size
            if min(w,h)<20 or w*h>12_000_000 or w>10000 or h>10000:raise ValueError()
            original.load();image=ImageOps.exif_transpose(original).convert('RGBA')
        alpha=image.getchannel('A');bbox=alpha.getbbox()
        if not bbox:raise ValueError()
        # Reject blank images on transparent or white backgrounds.
        white=Image.new('RGBA',image.size,'white');white.alpha_composite(image)
        gray=white.convert('L');ink=gray.point(lambda p:255 if p<220 else 0).getbbox()
        if not ink:raise ValueError()
        image=image.crop(ink);image.thumbnail((1600,600),Image.Resampling.LANCZOS)
        stream=BytesIO();image.save(stream,format='PNG')
        return stream.getvalue(),{'width':image.width,'height':image.height,'transparent':alpha.getextrema()[0]<255,'format':'PNG','original_sha256':hashlib.sha256(data).hexdigest()}
    except Exception:
        raise AppError(422,'signature_invalid','Choose a readable PNG, JPEG or WebP signature with visible ink (at least 20 pixels per dimension). SVG is not accepted; export it to PNG.') from None

def engine_path(settings):
    if settings.cover_letter_latex_path:
        path=Path(settings.cover_letter_latex_path).expanduser()
        if path.name not in {'xelatex','lualatex'}:return None
        return str(path.absolute()) if path.is_file() else None
    # A portable local install is optional; no global PATH or TeX profile changes.
    candidates=[shutil.which('xelatex'),shutil.which('lualatex'),'/Library/TeX/texbin/xelatex',
        *map(str,Path('.tools').glob('**/bin/*/xelatex')),*map(str,Path('.tools').glob('**/bin/*/lualatex'))]
    return next((str(Path(p).absolute()) for p in candidates if p and Path(p).is_file()),None)

def readiness(settings):
    return {'engine_available':bool(engine_path(settings)),'engine':'XeLaTeX / LuaLaTeX',
        'fonts_available':all((TEMPLATE/'fonts'/name).is_file() for name in ('SourceSansPro-Regular.otf','SourceSansPro-RegularIt.otf','SourceSansPro-Semibold.otf','SourceSansPro-Bold.otf')),
        'template':'supplied-source-sans-pro-v1','signature_formats':['png','jpg','jpeg','webp']}

def populate(content,facts,signature,width_mm,parskip=12):
    contact=[escape(content.signature_phone)] if content.signature_phone else []
    if content.signature_email:
        # Profile email shape has already been checked. No arbitrary URI scheme.
        email=quote(content.signature_email,safe='@.+-')
        contact.append(r'\href{mailto:'+escape(email)+'}{'+escape(content.signature_email)+'}')
    links=[]
    for link in classify_links(facts):
        if link.primary and link.platform in {'linkedin','portfolio'} and link.kind not in {'repository','deployment'}:
            links.append(href(link.url,'LinkedIn' if link.platform=='linkedin' else 'Portfolio'))
    day=content.date.strftime('%B %d, %Y').replace(' 0',' ')
    slots={'NAME':escape(content.signature_name),'TITLE':escape(content.job_title),'DATE':escape(day),
        'CONTACT':r'\\ '.join(contact),'LINKS':r'\\ '.join(links[:2]),'SALUTATION':escape(content.salutation),
        'BODY':'\n'.join(escape(getattr(content,key))+r'\par' for key in SECTION_KEYS if getattr(content,key)),
        'SIGNATURE':r'\includegraphics[width='+str(width_mm)+r'mm,height=14mm,keepaspectratio]{signature.png}\par\vspace{0.1cm}' if signature else '',
        'SIGNCONTACT':r'\par '.join(contact),'PARSKIP':str(parskip)}
    template=(TEMPLATE/'letter.tex').read_text()
    # Single-pass replacement: user values containing tokens cannot be re-expanded.
    return re.sub(r'@@([A-Z]+)@@',lambda m:slots[m.group(1)],template)

def normalized(text):return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())

def pdf_quality(path,content,signature,log=''):
    issues=[]
    try:
        reader=PdfReader(str(path),strict=True)
        if reader.is_encrypted or len(reader.pages)!=1:issues.append('page_overflow')
        texts=[p.extract_text() or '' for p in reader.pages];text='\n'.join(texts)
        if PLACEHOLDERS.search(text):issues.append('placeholder_detected')
        for key,value in [('candidate',content.signature_name),('job',content.job_title),('company',content.company_name),*[(key,getattr(content,key)) for key in SECTION_KEYS]]:
            if value and normalized(value) not in normalized(text):issues.append('missing_text_'+key)
        if len(text)<300:issues.append('text_not_selectable')
        embedded=True
        for page in reader.pages:
            for annotation in page.get('/Annots',[]):
                action=annotation.get_object().get('/A',{})
                if action.get('/S')=='/URI':
                    uri=str(action.get('/URI',''))
                    if not uri.startswith('mailto:') and not safe_url(uri):issues.append('invalid_pdf_url')
            for font in page.get('/Resources',{}).get('/Font',{}).values():
                font=font.get_object();descendants=font.get('/DescendantFonts')
                descriptors=[f.get_object().get('/FontDescriptor') for f in descendants] if descendants else [font.get('/FontDescriptor')]
                if not all(d and any(k in d.get_object() for k in ('/FontFile','/FontFile2','/FontFile3')) for d in descriptors):embedded=False
        if not embedded:issues.append('font_not_embedded')
        with pdfium.PdfDocument(str(path)) as pdf:
            page=pdf[0];width,height=page.get_size();tp=page.get_textpage()
            boxes=[tp.get_charbox(i) for i in range(tp.count_chars()) if tp.get_text_range(i,1).strip()]
            if any(x0<20 or x1>width-20 or y0<12 or y1>height-12 for x0,y0,x1,y1 in boxes):issues.append('text_clipped')
            # Inspect actual image placement; signature is the only permitted image.
            images=[obj.get_bounds() for obj in page.get_objects() if obj.type==pdfium.raw.FPDF_PAGEOBJ_IMAGE]
            if bool(images)!=bool(signature) or len(images)>1:issues.append('signature_missing_or_unexpected_image')
            if signature and images:
                x0,y0,x1,y1=images[0]
                if x0<25 or x1>width-25 or y0<45 or y1>height-150 or y1-y0>42:issues.append('signature_out_of_bounds')
                if any(min(x1,b[2])-max(x0,b[0])>1 and min(y1,b[3])-max(y0,b[1])>1 for b in boxes):issues.append('signature_overlaps_text')
            page.render(scale=1.5).to_pil().save(path.parent/'preview.png')
            tp.close();page.close()
        if re.search(r'Overfull \\[hv]box|Missing character:',log):issues.append('layout_or_missing_glyph')
        return {'valid':not issues,'issues':list(dict.fromkeys(issues)),'page_count':len(reader.pages),'selectable_text':len(text)>300,
            'fonts_embedded':embedded,'signature_present':bool(images),'text_bounds_checked':True,'signature_overlap_checked':bool(signature),
            'preview_generated':(path.parent/'preview.png').is_file(),'visual_check':'Programmatic text bounds, image bounds, glyph and paragraph completeness; human preview review still required.'}
    except Exception:
        return {'valid':False,'issues':['invalid_pdf_or_preview'],'preview_generated':False}

class RenderFailure(Exception):
    def __init__(self,code,audit):self.code,self.audit=code,audit;super().__init__(code)

def compile_letter(content,facts,signature,width_mm,destination,settings,check=lambda:None):
    destination=Path(destination);destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    binary=engine_path(settings);attempts=[]
    for index,spacing in enumerate((12,10,8),1):
        check()
        tex=populate(content,facts,signature,width_mm,spacing)
        (destination/'letter.tex').write_text(tex)
        if not binary:raise RenderFailure('latex_engine_unavailable',{'attempts':attempts,'source_retained':True})
        if not readiness(settings)['fonts_available']:raise RenderFailure('latex_fonts_missing',{'attempts':attempts,'source_retained':True})
        with tempfile.TemporaryDirectory(prefix='compile-',dir=destination) as scratch:
            directory=Path(scratch);shutil.copytree(TEMPLATE/'fonts',directory/'fonts')
            (directory/'letter.tex').write_text(tex)
            if signature:(directory/'signature.png').write_bytes(signature)
            env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','SYSTEMROOT','LANG'}}
            env.update(openin_any='p',openout_any='p',shell_escape='f',TEXMFOUTPUT=scratch,
                TEXMFVAR=str(directory/'texmf-var'),TEXMFCONFIG=str(directory/'texmf-config'))
            command=[binary,'-no-shell-escape','-interaction=nonstopmode','-halt-on-error','-file-line-error','letter.tex']
            process=subprocess.Popen(command,cwd=directory,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            timeout=False
            try:stdout,stderr=process.communicate(timeout=settings.cover_letter_compile_seconds)
            except subprocess.TimeoutExpired:
                timeout=True;os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate()
            log=(directory/'letter.log').read_text(errors='replace') if (directory/'letter.log').is_file() else ''
            output=stdout.decode(errors='replace');errors=stderr.decode(errors='replace')
            for name,value in [('stdout',output),('stderr',errors),('log',log)]:
                (destination/f'attempt-{index}.{name}').write_text(value)
            summary={'attempt':index,'paragraph_spacing_pt':spacing,'exit_code':process.returncode,'timeout':timeout,
                'stdout_bytes':len(stdout),'stderr_bytes':len(stderr),'generated_file_exists':(directory/'letter.pdf').is_file(),
                'command':[Path(binary).name,*command[1:]],'log_reference':f'attempt-{index}.log'}
            summary['diagnostics']=[line[:220] for line in (output+'\n'+errors).splitlines() if re.match(r'^! (LaTeX|Package|Font)|^Missing character:|^Overfull \\[hv]box',line)][:8]
            if timeout:summary['diagnostics'].append('Compiler exceeded its execution time limit.')
            attempts.append(summary);check()
            if timeout or process.returncode!=0 or not (directory/'letter.pdf').is_file():
                # Retry abnormal compilation once, in an entirely fresh directory.
                if index<2:continue
                raise RenderFailure('latex_compile_timeout' if timeout else 'latex_compile_failed',{'attempts':attempts,'source_retained':True})
            shutil.copyfile(directory/'letter.pdf',destination/'letter.pdf')
            qa=pdf_quality(destination/'letter.pdf',content,signature,log);summary['quality']=qa
            if qa['valid']:return {'attempts':attempts,'quality':qa,'source_retained':True,'pdf_sha256':hashlib.sha256((destination/'letter.pdf').read_bytes()).hexdigest()}
            if index==3:raise RenderFailure('pdf_quality_failed',{'attempts':attempts,'quality':qa,'source_retained':True})
    raise RenderFailure('latex_compile_failed',{'attempts':attempts})
