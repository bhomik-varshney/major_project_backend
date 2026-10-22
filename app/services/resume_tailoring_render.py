"""Supplied resume.cls, safe slots, bounded evidence-preserving fit and PDF QA."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
from pypdf import PdfReader
import pypdfium2 as pdfium
from app.schemas.resume_tailoring import ResumeDraft
from app.services.cover_letter_render import escape,href,normalized,RenderFailure,engine_path
from app.services.application_transform import safe_url
from app.services.latex_documents import compile_once
from app.services.resume_tailoring_content import FICTION,validate,report
from app.services.resume_tailoring_evidence import display_title, skill_rows, project_technologies, bullet_wording, display_project_links

TEMPLATE=Path(__file__).resolve().parents[1]/'templates'/'resume_tailoring'
LABELS={'summary':'Professional Summary','experience':'Experience','projects':'Projects','skills':'Technical Skills','education':'Education','certifications':'Certifications','achievements':'Achievements','publications':'Publications'}

def readiness(settings):return {'engine_available':bool(engine_path(settings)),'template':'supplied-navy-v1','template_available':all((TEMPLATE/f).is_file() for f in ('resume.cls','original.tex','document.tex')),'paper':'Letter (supplied class)','body_font_pt':11}

def escaped_email(value):
    from urllib.parse import quote
    return r'\href{mailto:'+escape(quote(value,safe='@.+-'))+'}{'+escape(value)+'}'

def resume_link(link):
    # Exact validated target, readable destination text with safe break points.
    label=''.join(escape(c)+(r'\allowbreak ' if c in '/._-?&=#' else '') for c in link['label'])
    return href(link['url'],'')[:-1]+label+'}'


def aligned_line(left,right,bold=False,italic=False):
    style=r'\bfseries ' if bold else r'\itshape ' if italic else ''
    # Fixed date/location columns prevent collision with long organisations.
    return (r'\noindent\begin{tabularx}{\linewidth}{@{}>{\raggedright\arraybackslash}X@{\hspace{.02\linewidth}}>{\raggedleft\arraybackslash}p{.24\linewidth}@{}}'+
            style+escape(left)+' & '+escape(right)+r'\tabularnewline\end{tabularx}\par\nopagebreak')


def populate(draft,spacing=6):
    contact=[]
    if draft.location:contact.append(escape('India' if draft.location.casefold()=='india' else draft.location))
    if draft.email:contact.append(escaped_email(draft.email))
    if draft.phone:contact.append(escape(draft.phone))
    first=r'\personalInfo{'+r'\parbox{\linewidth}{\centering '+r'\infoSeparator '.join(contact)+'}}'
    # Profile URLs themselves identify the platform; no generic-only labels.
    profile_links=[{**v,'label':v['label'].split(': ',1)[-1]} for v in draft.links]
    if profile_links:
        separator=r'\infoSeparator ' if sum(len(v['label']) for v in profile_links)<100 else r'\\ '
        first+=r'\personalInfo{\parbox{\linewidth}{\centering '+separator.join(resume_link(v) for v in profile_links)+'}}'
    sections=[]
    visible_skills={e.title.casefold() for e in draft.entries if e.section=='skills'}
    for section in draft.section_order:
        entries=[e for e in draft.entries if e.section==section]
        if section=='summary':sections.append(r'\begin{rSection}{Professional Summary}'+escape(draft.summary)+r'\end{rSection}');continue
        if not entries:continue
        body=[]
        if section=='skills':
            for group,values in skill_rows(entries).items():
                labels=[r'\mbox{'+escape(v)+'}' if len(v)<44 else escape(v) for v in values]
                body.append(r'{\hangindent=1em\hangafter=1\noindent\textbf{'+escape(group)+':} '+', '.join(labels)+r'\par}')
        else:
            for e in entries:
                if section=='experience':
                    body.append(aligned_line(e.title,e.period,bold=True))
                    body.append(aligned_line(e.subtitle,e.location,italic=True))
                elif section=='education':
                    body.append(aligned_line(e.title,e.period,bold=True))
                    grade=e.grade if re.match(r'^(?:CGPA|CGPI|GPA|Grade)\b',e.grade,re.I) else 'Grade / CGPA: '+e.grade if e.grade else ''
                    details=[v for v in (e.subtitle,e.location,grade) if v]
                    if details:body.append(escape(' · '.join(details))+r'\par\nopagebreak')
                else:
                    heading=escape(display_title(e)) if section in {'achievements','certifications','publications'} else r'\textbf{'+escape(display_title(e))+'}'
                    if e.period:heading+=r'\hfill '+escape(e.period)
                    if section=='projects':
                        tech=project_technologies(e,draft.entries)
                        if tech:heading+=' '+r'\textit{('+escape(', '.join(tech))+')}'
                    body.append(heading+r'\par\nopagebreak')
                    details=[v for v in (e.subtitle,e.location) if v]
                    if details:body.append(escape(' · '.join(details))+r'\par\nopagebreak')
                if e.bullets:body.append(r'\begin{resumeBullets}'+''.join(r'\item '+escape(b['text']) for b in e.bullets)+r'\end{resumeBullets}')
                extra=[t for t in e.technologies if t.casefold() not in visible_skills]
                if extra and section!='projects':body.append(r'\textit{Technologies: '+escape(', '.join(extra))+r'}\par')
                links=display_project_links(e) if section=='projects' else e.links
                if links:body.append(r'\noindent '+r'\quad '.join(resume_link(v) for v in links)+r'\par')
                body.append(r'\vspace{1pt}')
        sections.append(r'\begin{rSection}{'+LABELS[section]+'}'+'\n'.join(body)+r'\end{rSection}')
    slots={'NAME':escape(draft.name),'SPACING':str(spacing),'CONTACT':first,'SECTIONS':'\n'.join(sections)}
    return re.sub(r'@@([A-Z]+)@@',lambda m:slots[m.group(1)],(TEMPLATE/'document.tex').read_text())

def expected_text(draft):
    values=[('name',draft.name),('summary',draft.summary),('phone',draft.phone),('email',draft.email),('location',draft.location)]
    for e in draft.entries:
        values.append((e.id+'.title',display_title(e)))
        values.extend((e.id+'.'+k,getattr(e,k)) for k in ('subtitle','period','location','grade'))
        values.extend((e.id+'.technology',v) for v in e.technologies)
        values.extend((e.id+'.bullet',b['text']) for b in e.bullets)
        values.extend((e.id+'.link_label',v['label']) for v in (display_project_links(e) if e.section=='projects' else e.links))
    values.extend(('header.link_label',v['label'].split(': ',1)[-1]) for v in draft.links)
    values.extend((s,LABELS[s]) for s in draft.section_order if s=='summary' or any(e.section==s for e in draft.entries))
    return values

def pdf_quality(path,draft,log=''):
    issues=[]
    try:
        reader=PdfReader(str(path),strict=True)
        if reader.is_encrypted or len(reader.pages)!=1:issues.append('page_overflow')
        text='\n'.join(p.extract_text() or '' for p in reader.pages);plain=normalized(text)
        if FICTION.search(text):issues.append('fictional_or_placeholder_content')
        if re.search(r'[\ufb00-\ufb06]',text):issues.append('typographic_ligature')
        if re.search(r'confirmed_profile\.|/Users/|resume_tailoring\.',text):issues.append('internal_implementation_text')
        if re.search(r'(?m)^\s*(?:Repository|Deployment|Additional link|Project link)\s*(?:\||$)',text):issues.append('generic_link_placeholder')
        for field,value in expected_text(draft):
            if value and normalized(value) not in plain:issues.append('missing_text:'+field)
        if not text.strip():issues.append('text_not_selectable')
        embedded=True;image_count=0;actual_urls=set()
        for page in reader.pages:
            for font in page.get('/Resources',{}).get('/Font',{}).values():
                f=font.get_object();children=f.get('/DescendantFonts');descriptors=[v.get_object().get('/FontDescriptor') for v in children] if children else [f.get('/FontDescriptor')]
                if not all(d and any(k in d.get_object() for k in ('/FontFile','/FontFile2','/FontFile3')) for d in descriptors):embedded=False
            for ann in page.get('/Annots',[]):
                action=ann.get_object().get('/A',{});url=str(action.get('/URI',''))
                if url:actual_urls.add(url)
                if url and not url.startswith('mailto:') and not safe_url(url):issues.append('invalid_url')
        from urllib.parse import quote
        for link in draft.links+[v for e in draft.entries for v in (display_project_links(e) if e.section=='projects' else e.links)]:
            target=safe_url(link['url'])
            if target and quote(target,safe=':/?=&%#+,;@!.-_~') not in actual_urls:issues.append('missing_hyperlink')
        if not embedded:issues.append('fonts_not_embedded')
        with pdfium.PdfDocument(str(path)) as pdf:
            page=pdf[0];w,h=page.get_size();tp=page.get_textpage()
            chars=[(tp.get_text_range(i,1),tp.get_charbox(i)) for i in range(tp.count_chars())]
            boxes=[b for c,b in chars if c.strip()]
            extracted=''.join(c for c,_ in chars)
            name_match=re.search(r'\s+'.join(re.escape(v) for v in draft.name.split()),extracted)
            name_offset=None
            if name_match:
                name_boxes=[b for c,b in chars[name_match.start():name_match.end()] if c.strip()]
                if name_boxes:
                    name_offset=round((min(b[0] for b in name_boxes)+max(b[2] for b in name_boxes))/2-w/2,2)
                    # Allow small italic glyph overhang, but reject layout drift.
                    if abs(name_offset)>2:issues.append('name_not_centered')
            aligned_dates=0
            for e in draft.entries:
                if e.section not in {'experience','education'} or not e.period:continue
                pattern=r'\s+'.join(re.escape(v) for v in e.period.split())
                match=re.search(pattern,extracted)
                if match:
                    rects=[b for c,b in chars[match.start():match.end()] if c.strip()]
                    if rects and max(b[2] for b in rects)<w-62:issues.append('date_not_right_aligned')
                    else:aligned_dates+=1
            used_height=max(b[3] for b in boxes)-min(b[1] for b in boxes) if boxes else 0
            utilization=round(used_height/max(1,h-86.4),3)
            if any(b[0]<40 or b[2]>w-40 or b[1]<30 or b[3]>h-30 for b in boxes):issues.append('text_clipped_or_margin_violation')
            # Detect gross text collisions, excluding adjacent glyph edges. This
            # is deliberately a heuristic in addition to compiler box warnings.
            for i,(char,a) in enumerate(chars):
                if not char.strip():continue
                for j,(other,b) in enumerate(chars[max(0,i-25):i],max(0,i-25)):
                    # Ligatures and italic kerning legitimately share adjacent
                    # glyph bounds. Only non-neighbour collisions are evidence.
                    if i-j<=3:continue
                    if not other.strip():continue
                    dx=min(a[2],b[2])-max(a[0],b[0]);dy=min(a[3],b[3])-max(a[1],b[1])
                    area=max(0,a[2]-a[0])*max(0,a[3]-a[1]);other_area=max(0,b[2]-b[0])*max(0,b[3]-b[1])
                    if dx>1.5 and dy>3 and dx*dy>.45*max(1,min(area,other_area)):issues.append('possible_text_overlap');break
            image_count=sum(obj.type==pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects())
            if image_count:issues.append('unexpected_image')
            page.render(scale=1.5).to_pil().save(Path(path).parent/'preview.png');tp.close();page.close()
        if re.search(r'Overfull \\[hv]box|Missing character:',log):issues.append('layout_or_missing_glyph')
        lines=[normalized(line) for line in text.splitlines()]
        positions=[lines.index(normalized(LABELS[s])) if normalized(LABELS[s]) in lines else -1 for s in draft.section_order if s=='summary' or any(e.section==s for e in draft.entries)]
        if -1 in positions:issues.append('missing_section_heading')
        if positions!=sorted(positions):issues.append('section_reading_order')
        if re.search(r'\{\s*"(?:organization|institution|description|technologies)"\s*:',text):issues.append('raw_json_content')
        if any(e.section=='skills' for e in draft.entries):
            skill_start=text.find(LABELS['skills'])
            next_headings=[text.find(LABELS[s],skill_start+len(LABELS['skills'])) for s in draft.section_order if s!='skills' and (s=='summary' or any(e.section==s for e in draft.entries))]
            skill_end=min([n for n in next_headings if n>=0],default=len(text))
            skill_text=normalized(text[skill_start:skill_end])
            if any(normalized(e.title) not in skill_text for e in draft.entries if e.section=='skills'):issues.append('incomplete_skill_section')
        return {'valid':not issues,'issues':list(dict.fromkeys(issues)),'page_count':len(reader.pages),'selectable_text':bool(text.strip()),'fonts_embedded':embedded,
            'images':image_count,'text_bounds_checked':True,'overlap_check':'Glyph-intersection heuristic and compiler warnings','section_reading_order_checked':True,
            'body_font_pt':11,'content_height_ratio':utilization,'all_selected_entries_checked':True,'skill_coverage_checked':True,'hyperlinks_checked':True,
            'ligature_extraction_checked':True,'readable_link_labels_checked':True,'right_aligned_dates_checked':aligned_dates,
            'name_center_offset_pt':name_offset,'name_centered':name_offset is not None and abs(name_offset)<=2,
            'balance_observation':'Sparse because the confirmed profile is short.' if utilization<.5 else 'Content uses the page within readable margins.',
            'preview_generated':(Path(path).parent/'preview.png').is_file(),'ats_observations':'Normal selectable text, standard headings, visible links and no graphics. Single-column content; user preview review is required. ATS vendor behaviour is not guaranteed.'}
    except Exception:return {'valid':False,'issues':['invalid_pdf_or_preview'],'preview_generated':False}

def reduce_content(draft,level):
    result=draft.model_copy(deep=True);removed=[]
    # Never drop a whole employment, internship, project, skill or education.
    # Only complete source bullets are condensed; every omission is audited.
    for e in result.entries:
        if len(e.bullets)>1:
            removed.extend({'field':b['id'],'reason':'Additional complete source sentence omitted for one-page readability; entry preserved.'} for b in e.bullets[1:])
            e.bullets=e.bullets[:1]
        for b in e.bullets:
            shortest=bullet_wording(b)
            if b['text']!=shortest:
                removed.append({'field':b['id'],'reason':'Used a shorter complete source clause; full original retained in the ledger.'});b['text']=shortest
    return result,removed

def compile_resume(draft,source,facts,job,destination,settings,check=lambda:None):
    destination=Path(destination);attempts=[];corrections=[];current=draft.model_copy(deep=True)
    assets={'resume.cls':TEMPLATE/'resume.cls'}
    if not assets['resume.cls'].is_file():raise RenderFailure('resume_class_missing',{'attempts':[]})
    for index,spacing in enumerate((6,4,3),1):
        validation=validate(current,source,facts,job)
        if not validation['valid']:raise RenderFailure('resume_content_invalid',{'attempts':attempts,'validation':validation})
        summary,log=compile_once(populate(current,spacing),assets,destination,settings,index,check)
        summary['section_spacing_pt']=spacing;attempts.append(summary)
        if summary['exit_code']!=0 or summary['timeout'] or not summary['generated_file_exists']:
            if index<2:continue
            raise RenderFailure('latex_compile_timeout' if summary['timeout'] else 'latex_compile_failed',{'attempts':attempts,'source_retained':True})
        quality=pdf_quality(destination/'document.pdf',current,log);summary['quality']=quality
        if quality['valid']:
            return current,{'attempts':attempts,'quality':quality,'corrections':corrections,'pdf_sha256':sha256((destination/'document.pdf').read_bytes()).hexdigest(),
                'class_sha256':sha256((TEMPLATE/'resume.cls').read_bytes()).hexdigest(),'source_retained':True}
        if index<3:
            # Content compression is justified by overflow, never an unrelated QA failure.
            if 'page_overflow' in quality['issues']:
                current,changes=reduce_content(current,index);corrections+=changes
    raise RenderFailure('resume_insufficient_space' if 'page_overflow' in quality['issues'] else 'resume_pdf_quality_failed',{'attempts':attempts,'quality':quality,'corrections':corrections,'source_retained':True})
