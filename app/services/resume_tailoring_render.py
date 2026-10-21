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
from app.services.resume_tailoring_evidence import display_title

TEMPLATE=Path(__file__).resolve().parents[1]/'templates'/'resume_tailoring'
LABELS={'summary':'Professional Summary','experience':'Experience','projects':'Projects','skills':'Technical Skills','education':'Education','certifications':'Certifications','achievements':'Achievements','publications':'Publications'}

def readiness(settings):return {'engine_available':bool(engine_path(settings)),'template':'supplied-navy-v1','template_available':all((TEMPLATE/f).is_file() for f in ('resume.cls','original.tex','document.tex')),'paper':'Letter (supplied class)','body_font_pt':11}

def escaped_email(value):
    from urllib.parse import quote
    return r'\href{mailto:'+escape(quote(value,safe='@.+-'))+'}{'+escape(value)+'}'

def populate(draft,spacing=6):
    contact=[]
    if draft.location:contact.append(escape(draft.location))
    if draft.email:contact.append(escaped_email(draft.email))
    if draft.phone:contact.append(escape(draft.phone))
    # Multiple centered lines retain personalInfo's identity while wrapping within
    # the text width; no birth dates, gender, icons or photos enter the mapping.
    first=r'\personalInfo{'+r'\parbox{\linewidth}{\centering '+r'\infoSeparator '.join(contact)+'}}'
    links=r'\infoSeparator '.join(href(v['url'],v['label']) for v in draft.links)
    if links:first+=r'\personalInfo{'+links+'}'
    sections=[]
    visible_skills={e.title.casefold() for e in draft.entries if e.section=='skills'}
    for section in draft.section_order:
        entries=[e for e in draft.entries if e.section==section]
        if section=='summary':sections.append(r'\begin{rSection}{Professional Summary}'+escape(draft.summary)+r'\end{rSection}');continue
        if not entries:continue
        body=[]
        if section=='skills':
            groups={}
            for e in entries:groups.setdefault(e.skill_group,[]).append(e.title)
            body=[r'\textbf{'+escape(group)+':} '+escape(', '.join(values))+r'\par' for group,values in groups.items()]
        else:
            for e in entries:
                # Single full-width reading flow; long project names no longer
                # consume a narrow table column alongside a paragraph wall.
                heading=r'\textbf{'+escape(display_title(e))+'}'
                if e.period and section!='education':heading+=' '+r'\hfill '+escape(e.period)
                if e.section=='projects' and e.links:heading+=' '+r'\hfill '+' | '.join(href(v['url'],v['label']) for v in e.links)
                body.append(heading+r'\par\nopagebreak')
                details=[v for v in (e.subtitle,e.location) if v]
                if section=='education' and e.grade:details.append('Grade / CGPA: '+e.grade)
                if details:
                    detail_line=r'\textit{'+escape(' | '.join(details))+'}'
                    if section=='education' and e.period:detail_line+=r'\hfill '+escape(e.period)
                    body.append(detail_line+r'\par\nopagebreak')
                elif section=='education' and e.period:body.append(escape(e.period)+r'\par')
                if e.grade and section!='education':body.append(escape('Grade / CGPA: '+e.grade)+r'\par')
                if e.bullets:
                    body.append(r'\begin{resumeBullets}'+''.join(r'\item '+escape(b['text']) for b in e.bullets)+r'\end{resumeBullets}')
                # Skills already visible in the grouped inventory need not be
                # repeated under each entry. Entry associations stay in review.
                extra_tech=[t for t in e.technologies if t.casefold() not in visible_skills]
                if extra_tech:body.append(r'\textit{Technologies: '+escape(', '.join(extra_tech))+r'}\par')
                if e.links and e.section!='projects':body.append(' | '.join(href(v['url'],v['label']) for v in e.links)+r'\par')
                body.append(r'\vspace{2pt}')
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
    values.extend((s,LABELS[s]) for s in draft.section_order if s=='summary' or any(e.section==s for e in draft.entries))
    return values

def pdf_quality(path,draft,log=''):
    issues=[]
    try:
        reader=PdfReader(str(path),strict=True)
        if reader.is_encrypted or len(reader.pages)!=1:issues.append('page_overflow')
        text='\n'.join(p.extract_text() or '' for p in reader.pages);plain=normalized(text)
        if FICTION.search(text):issues.append('fictional_or_placeholder_content')
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
        for link in draft.links+[v for e in draft.entries for v in e.links]:
            target=safe_url(link['url'])
            if target and quote(target,safe=':/?=&%#+,;@!.-_~') not in actual_urls:issues.append('missing_hyperlink')
        if not embedded:issues.append('fonts_not_embedded')
        with pdfium.PdfDocument(str(path)) as pdf:
            page=pdf[0];w,h=page.get_size();tp=page.get_textpage()
            chars=[(tp.get_text_range(i,1),tp.get_charbox(i)) for i in range(tp.count_chars())]
            boxes=[b for c,b in chars if c.strip()]
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
            shortest=min(b['variants'],key=len)
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
