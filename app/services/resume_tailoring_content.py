"""Confirmed facts -> auditable selection, wording variants and immutable entries.

Models choose existing facts/variants. They never supply replacement organisations,
dates, titles, technologies, links, numbers or qualifications. Source TeX is a
passive reference; it is never compiled or promoted to confirmed profile facts.
"""
from copy import deepcopy
import re
from app.schemas.resume_tailoring import ResumeEntry,ResumeDraft,SECTIONS
from app.services.application_transform import classify_links,safe_url,primary_education
from app.services.cover_letter_content import tokens,fragment

FICTION=re.compile(r'Carl Johnson|Groove Street|Memeology|Unicorn Analytics|Faber College|Westeros University|Rydell High|Dank Memes|Viral News Robotics|Library of Alexandria 2\.0|PigeonScript|Shakespearean insults|baguette related|Couch Potatoes|pleasedonotcontactme|202-555-0100|Lorem ipsum|John Snow|\[insert|\bnull\b',re.I)
from app.services.resume_tailoring_evidence import skill_group, bullets as bullet_variants, technology_mapping, project_links, ledger, summaries, relevance_plan, evidence_tokens, domain_terms, display_title, readable_link, reported_metrics, bullet_wording, skill_rows, project_technologies, display_project_links

def period(value):
    # Preserve explicit source strings; never infer a graduation date or tenure.
    if value.get('period'):return value['period']
    return ' – '.join(str(value[k]) for k in ('start_date','end_date') if value.get(k)) or ' – '.join(str(value[k]) for k in ('start_year','end_year') if value.get(k)) or value.get('expected_graduation','')

def catalog(facts,job,context=None):
    context=context or {};required=evidence_tokens(' '.join(job.get('required_skills',[])))
    preferred=evidence_tokens(' '.join(job.get('preferred_skills',[])))
    target=evidence_tokens(job.get('title',''))|required|preferred
    job_details=evidence_tokens(' '.join([job.get('description',''),*job.get('responsibilities',[])]))
    entries=[];association_reviews=[];technology_mappings=[]
    def score(text):
        values=evidence_tokens(text)
        return round(min(1,(2*len(values&required)+len(values&preferred)+.3*len(values&target)+.15*len(values&domain_terms(job))+.1*len(values&job_details))/max(1,2*len(required)+len(preferred)+.3*len(target))),4)
    for section in SECTIONS[1:]:
        for i,value in enumerate(facts.get(section,[]) or []):
            id=f'{section}-{i}'
            if isinstance(value,str):
                entries.append(ResumeEntry(id=id,section=section,source=f'confirmed_profile.{section}.{i}',title=value,relevance=score(value),skill_group=skill_group(value) if section=='skills' else ''))
                continue
            if not isinstance(value,dict):continue
            title=value.get('organization','') if section=='experience' else value.get('institution','') if section=='education' else value.get('name','')
            subtitle=value.get('role','') if section in {'experience','projects'} else value.get('qualification','')
            technology,review=technology_mapping(value,facts.get('skills',[]))
            association_reviews.extend({'entry_id':id,**v} for v in review)
            declared=list(dict.fromkeys(value.get('technologies',[])+value.get('tools',[])))
            if declared!=technology:technology_mappings.append({'entry_id':id,'before':declared,'after':technology,
                'evidence':f'confirmed_profile.{section}.{i}','reason':'Entry-specific declared technologies and explicit confirmed skill mentions; input/removal mentions excluded.'})
            links=project_links(facts,i) if section=='projects' else []
            bullets=bullet_variants(id,value)
            text=' '.join([title,subtitle,*technology,*[b['text'] for b in bullets]])
            entries.append(ResumeEntry(id=id,section=section,source=f'confirmed_profile.{section}.{i}',title=title,subtitle=subtitle,
                period=period(value),location=value.get('location',''),grade=value.get('grade',''),technologies=technology,links=links,bullets=bullets,relevance=score(text)))
    ranked=sorted(entries,key=lambda e:(-e.relevance,e.id))
    primary,_=primary_education(facts)
    protected=[e.id for e in ranked if e.section in {'experience','projects','skills'} or e.id==f'education-{primary}' or e.section in {'certifications','achievements'} and e.relevance>0]
    if primary is None:protected.extend(e.id for e in ranked if e.section=='education')
    selected=[e.id for section in SECTIONS[1:] for e in ranked if e.section==section and (e.id in protected or e.section in {'certifications','achievements','publications'} and e.relevance>0)]
    summary_options=summaries(facts,job,entries)
    plan=relevance_plan(facts,job,entries,context)
    warnings=[]
    if not entries:warnings.append('No confirmed resume sections are available. Add and confirm education, skills or project evidence first.')
    if not facts.get('experience'):warnings.append('No confirmed work experience; relevant projects/education are used instead.')
    if not job.get('required_skills'):warnings.append('Required skills are unavailable; the selected title and available preferred skills guide ordering.')
    if association_reviews:warnings.append('Some entry technology associations need review; uncertain associations were excluded from entry technology lists.')
    if any(not safe_url(v) for v in facts.get('links',[])):warnings.append('Unsafe or malformed profile links are omitted.')
    return {'entries':[e.model_dump() for e in entries],'summaries':summary_options,'selected_ids':selected,'warnings':warnings,
        'requirements':job.get('required_skills',[]),'preferred_requirements':job.get('preferred_skills',[]),'context':context,
        'required_results':{e.id:[{'value':value,'source':b['source']} for b in e.bullets for value in reported_metrics(b['original'])] for e in entries if e.section in {'experience','projects'}},
        'content_policy_version':3,'ledger':ledger(facts,entries,protected),'protected_ids':protected,'relevance_plan':plan,'technology_reviews':association_reviews,'technology_mappings':technology_mappings,
        'inventory_counts':{section:sum(e.section==section for e in entries) for section in SECTIONS[1:]},
        'template':'supplied-navy-v1','confidence':'medium','selection_method':'Required/preferred/title token evidence; matching and gap context are advisory, never facts.'}

def assemble(facts,job,source,selection=None):
    selection=selection or {};known={e['id']:e for e in source['entries']};ids=selection.get('entry_ids',source['selected_ids'])
    if len(ids)!=len(set(ids)) or set(ids)-known.keys():raise ValueError('resume_unknown_entry')
    summary=next((v for v in source['summaries'] if v['id']==selection.get('summary_id','focused')),None)
    if not summary:raise ValueError('resume_unknown_summary')
    entries=[ResumeEntry.model_validate(deepcopy(known[id])) for id in ids]
    requested=selection.get('bullet_ids')
    available={b['id'] for e in entries for b in e.bullets}
    if requested is not None and (len(requested)!=len(set(requested)) or set(requested)-available):raise ValueError('resume_unknown_bullet')
    emphasis=evidence_tokens(' '.join(job.get('required_skills',[])+[job['title']]))
    for e in entries:
        choices=[b for b in e.bullets if requested is None or b['id'] in requested or reported_metrics(b['original'])]
        # Complete sentence/clause variants, relevance first. Full originals remain in the ledger.
        def bullet_priority(b):
            numeric=bool(re.search(r'\d+(?:\.\d+)?\s*%|\b(?:F1|accuracy|tests|listings)\b',b['original'],re.I))
            boilerplate=bool(re.search(r'certificate|completion certificate',b['original'],re.I))
            return (-int(numeric),int(boilerplate),-len(evidence_tokens(b['text']) & emphasis),len(b['text']))
        choices.sort(key=bullet_priority)
        covered={id for b in choices for id in b.get('covers_ids',[])}
        choices=[b for b in choices if b['id'] not in covered]
        e.bullets=choices[:2]
        for b in e.bullets:b['text']=bullet_wording(b)
    links=[{'url':v.original_url,'label':readable_link(v.url,v.kind,v.platform),'source':v.source} for v in classify_links(facts) if v.primary and v.platform in {'linkedin','github','portfolio'} and v.kind not in {'repository','deployment'}][:3]
    order=selection.get('section_order',list(SECTIONS))
    return ResumeDraft(name=facts['name'],phone=facts.get('phone',''),email=facts.get('email',''),location=facts.get('location',''),
        links=links,target_job=job['title'],summary=summary['text'],summary_id=summary['id'],summary_evidence=summary['evidence'],entries=entries,section_order=order)

def validate(draft,source,facts,job):
    issues=[];known={e['id']:e for e in source['entries']};ids=[e.id for e in draft.entries]
    for key in ('name','phone','email','location'):
        if getattr(draft,key)!=facts.get(key,''):issues.append({'field':key,'reason':'Immutable confirmed identity changed.'})
    if draft.target_job!=job['title']:issues.append({'field':'target_job','reason':'Selected job changed.'})
    if draft.links!=assemble(facts,job,source).links:issues.append({'field':'links','reason':'Confirmed links changed.'})
    summaries={s['id']:s for s in source['summaries']}
    if draft.summary_id not in summaries or draft.summary!=summaries[draft.summary_id]['text'] or draft.summary_evidence!=summaries[draft.summary_id]['evidence']:
        issues.append({'field':'summary','reason':'Summary must use a supported wording variant; new claims require confirmed profile evidence.'})
    if len(ids)!=len(set(ids)):issues.append({'field':'entries','reason':'Duplicate selected entries.'})
    if len(draft.section_order)!=len(set(draft.section_order)) or set(draft.section_order)-set(SECTIONS) or 'summary' not in draft.section_order:
        issues.append({'field':'section_order','reason':'Duplicate/unsupported sections or missing summary.'})
    for e in draft.entries:
        original=known.get(e.id)
        if not original:issues.append({'field':e.id,'reason':'Unknown source evidence.'});continue
        for key in ('section','source','title','subtitle','period','location','grade','technologies','links','relevance','skill_group'):
            if getattr(e,key)!=original[key]:issues.append({'field':f'{e.id}.{key}','reason':'Immutable source fact changed.'})
        bullets={b['id']:b for b in original['bullets']}
        if e.section in {'experience','projects'} and bullets and not e.bullets:issues.append({'field':e.id,'reason':'A selected work/project entry must retain at least one supported responsibility or outcome.'})
        if len({b.get('id') for b in e.bullets})!=len(e.bullets):issues.append({'field':e.id,'reason':'Duplicate bullet.'})
        for b in e.bullets:
            expected=bullets.get(b.get('id'))
            if not expected or b.get('text') not in expected['variants'] or {k:v for k,v in b.items() if k!='text'}!={k:v for k,v in expected.items() if k!='text'}:
                issues.append({'field':e.id+'.bullets','reason':'Unsupported bullet wording or altered source evidence.'})
        if e.section not in draft.section_order:issues.append({'field':e.id,'reason':'Selected evidence would be hidden by the section order.'})
    for e in draft.entries:
        for result in source.get('required_results',{}).get(e.id,[]):
            if result['value'] not in ' '.join(b['text'] for b in e.bullets):issues.append({'field':e.id+'.result','reason':'A confirmed measurable result must remain visible in this entry.'})
    if not draft.entries:issues.append({'field':'entries','reason':'At least one confirmed education, skill, project or experience entry is needed.'})
    for id in source.get('protected_ids',[]):
        if id not in ids:issues.append({'field':id,'reason':'Protected confirmed content must be preserved; shorten its bullets instead of omitting the entry.'})
    if not source.get('relevance_plan',{}).get('valid',True):issues.append({'field':'relevance_plan','reason':'A valid job relevance plan is required before tailoring.'})
    text=' '.join([draft.name,draft.phone,draft.email,draft.summary,*[str(e.model_dump()) for e in draft.entries]])
    if FICTION.search(text):issues.append({'field':'content','reason':'Fictional sample or placeholder content detected.'})
    visible=' '.join([draft.summary,*[e.title+' '+e.subtitle+' '+' '.join(b['text'] for b in e.bullets) for e in draft.entries]])
    if re.search(r'\{\s*"(?:organization|institution|description|technologies)"\s*:',visible):issues.append({'field':'content','reason':'Raw structured JSON cannot appear as resume prose.'})
    if any(ord(c)<32 and c not in '\n\t' for c in text):issues.append({'field':'content','reason':'Unsupported control characters.'})
    return {'valid':not issues,'issues':issues,'immutable_fields_checked':True,'claims_source_checked':True,
        'preservation_checked':True,'relevance_plan_checked':True,'one_page':'pending_pdf','ats':'Selectable text, standard headings and visible links; final PDF checks and human review required.'}

def edit_draft(draft,source,edits):
    value=draft.model_copy(deep=True)
    if 'summary' in edits:
        text=edits['summary']
        selected=next((s for s in source['summaries'] if s['text']==text),None)
        value.summary=text
        if selected:value.summary_id=selected['id'];value.summary_evidence=selected['evidence']
    if 'entry_ids' in edits:
        ids=edits['entry_ids'];known={e['id']:e for e in source['entries']}
        if not isinstance(ids,list) or len(ids)>240 or len(ids)!=len(set(ids)) or set(ids)-known.keys():raise ValueError('resume_edit_invalid_entries')
        previous={e.id:e for e in value.entries}
        value.entries=[previous[id] if id in previous else ResumeEntry.model_validate(known[id]) for id in ids]
    if 'section_order' in edits:value.section_order=edits['section_order']
    bullet_text=edits.get('bullet_text',{})
    if not isinstance(bullet_text,dict) or len(bullet_text)>60:raise ValueError('resume_edit_invalid_bullets')
    for id,text in bullet_text.items():
        found=False
        for e in value.entries:
            for b in e.bullets:
                if b['id']==id:
                    if not isinstance(text,str) or len(text)>4000:raise ValueError('resume_edit_invalid_bullets')
                    b['text']=text;found=True
        if not found:raise ValueError('resume_edit_unknown_bullet')
    return value

def report(draft,source,facts):
    ids={e.id for e in draft.entries};keywords=[]
    skills={e.title.casefold() for e in draft.entries if e.section=='skills'}
    for label in source['requirements']+source['preferred_requirements']:
        if label.casefold() in skills:keywords.append(label)
    return {'selected_job':draft.target_job,'keywords_addressed':list(dict.fromkeys(keywords)),
        'selected':[{'id':e.id,'section':e.section,'title':e.title,'source':e.source,'relevance':e.relevance,'reason':'Preserved confirmed history; job relevance determines order and bullet emphasis.'} for e in draft.entries],
        'omitted':[{'id':e['id'],'title':e['title'],'section':e['section'],'reason':'Secondary education condensed to the primary qualification.' if e['section']=='education' else 'Optional content excluded by relevance or explicit user selection; full evidence remains in the ledger.'} for e in source['entries'] if e['id'] not in ids],
        'preserved':[{'id':e.id,'title':e.title,'section':e.section} for e in draft.entries],
        'coverage':{section:{'available':sum(e['section']==section for e in source['entries']),'preserved':sum(e.section==section for e in draft.entries)} for section in SECTIONS[1:]},
        'relevance_plan':source.get('relevance_plan',{}),
        'section_order_reason':'Standard summary, experience, projects, skills and education flow.' if draft.section_order==list(SECTIONS) else 'Explicit agent/user ordering retained; all selected sections and protected evidence remain visible.',
        'bullet_omissions':[{'entry_id':e.id,'source':b['source'],'original':b['original'],'reason':'Detail condensed into an implementation/outcome bullet; main action retained.' if b['id'] in {id for selected in e.bullets for id in selected.get('covers_ids',[])} else 'Additional source sentence omitted for concise emphasis; entry retained.'} for e in draft.entries for b in next(v for v in source['entries'] if v['id']==e.id)['bullets'] if b['id'] not in {v['id'] for v in e.bullets}],
        'condensed':[{'entry_id':e.id,'source':b['source'],'before':b['original'],'after':b['text'],'reason':'Complete extractive clause; original evidence retained.'} for e in draft.entries for b in e.bullets if b['text']!=b['original']],
        'title_condensation':[{'entry_id':e.id,'before':e.title,'after':display_title(e),'reason':'Assessment/team qualifier retained in the ledger; main project title shown in the PDF.'} for e in draft.entries if display_title(e)!=e.title],
        'bullet_changes':[{'entry_id':e.id,'before':b['original'],'after':b['text'],'source':b['source']} for e in draft.entries for b in e.bullets if b['text']!=b['original']],
        'link_changes':[{'entry_id':e.id,'label':link['label'],'url':link['url'],'source':link['source'],'reason':'Visible destination text; exact project-specific hyperlink retained.'} for e in draft.entries for link in display_project_links(e)]+[{'entry_id':'header','label':link['label'],'url':link['url'],'source':link['source'],'reason':'Professional profile URL stays in the header.'} for link in draft.links],
        'omitted_links':[{'entry_id':e.id,'label':link['label'],'url':link['url'],'reason':'Additional project link retained in review and evidence ledger; repository and demo prioritised in the one-page PDF.'} for e in draft.entries if e.section=='projects' for link in e.links if link['url'] not in {v['url'] for v in display_project_links(e)}],
        'skill_groups':skill_rows(draft.entries),
        'skill_presentation':{'confirmed_labels':sum(e.section=='skills' for e in draft.entries),'visible_labels':sum(len(v) for v in skill_rows(draft.entries).values()),'reason':'Explicit MCP alias and React version labels consolidated; full confirmed inventory preserved.'},
        'project_technology_emphasis':[{'entry_id':e.id,'shown':project_technologies(e,draft.entries),'full':e.technologies,'reason':'Entry-specific emphasis; remaining confirmed technologies stay in Technical Skills and evidence audit.'} for e in draft.entries if e.section=='projects'],
        'required_results':source.get('required_results',{}),
        'changes':[
{'section':'summary','before':facts.get('career_objective',''),'after':draft.summary,'evidence':draft.summary_evidence},
            *[{'section':s,'before':[e['title'] for e in source['entries'] if e['section']==s],'after':[e.title for e in draft.entries if e.section==s],
                'evidence':[e.source for e in draft.entries if e.section==s]} for s in SECTIONS[1:]]],
        'warnings':source['warnings'],'confidence':'medium','ats_observations':'No hidden keywords or proficiency claims; standard text headings, visible links and normal text. ATS vendor behaviour is not guaranteed.'}
