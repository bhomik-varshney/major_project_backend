"""Local, extractive resume evidence. Relevance changes emphasis, never ownership.

These transforms deliberately keep model-generated factual prose out of the PDF.
The full originals and every shortening remain available in the private ledger.
"""
from copy import deepcopy
import re
from app.services.application_transform import classify_links, primary_education, safe_url
from app.services.cover_letter_content import tokens
from app.services.skill_taxonomy import normalize_phrases, canonical_skill, relationship

def evidence_tokens(text):
    return tokens(normalize_phrases(text)) - {'intern','engineer','developer','senior','junior'}

def domain_terms(job):
    title=job.get('title','').lower()
    if re.search(r'nlp|natural language',title):return evidence_tokens('spaCy Transformers BERTScore PyTorch text classification language retrieval QLoRA')
    if re.search(r'\bai\b|machine learning',title):return evidence_tokens('PyTorch TensorFlow Transformers PEFT QLoRA LangGraph CrewAI machine learning')
    return set()

GROUPS = {
    'Programming Languages': r'^(python|java|javascript|typescript|c(/c\+\+)?|c\+\+|go|rust|sql|r|kotlin|swift)$',
    'AI/ML and NLP': r'nlp|machine learning|deep learning|llm|pytorch|tensorflow|scikit|transformers|peft|qlora|bert|spacy|opencv|mediapipe|tesseract|bitsandbytes|lang|crewai|agents sdk|mcp|model context|generative|computer vision|groq',
    'Mobile and Frontend': r'react|three\.js|typescript|javascript|html|css|next\.js|angular|vue|tanstack',
    'Backend and APIs': r'fastapi|flask|django|spring|node|rest|graphql',
    'Databases': r'postgres|mysql|mongodb|sqlite|redis|elastic',
    'Cloud and DevOps': r'aws|azure|gcp|docker|kubernetes|terraform|ci/cd|linux|render',
    'Frameworks and Libraries': r'numpy|pandas|scipy|matplotlib|seaborn',
}

def skill_group(label):
    return next((k for k, pattern in GROUPS.items() if re.search(pattern, label, re.I)), 'Tools')

def sentences(text):
    return [v.strip(' •') for v in re.split(r'(?<=[.!?])\s+(?=[A-Z])', ' '.join(text.split())) if v.strip(' •')]

def bullets(entry_id, value):
    """Split complete sentences even in short descriptions; ignore OCR line duplicates.

    A shorter variant is only a complete source clause before a semicolon, or a
    sentence without a non-numeric parenthetical. No metrics or words are invented.
    """
    raw=[]
    description=' '.join(value.get('description', '').split())
    if description:raw.append(('description', description))
    for key in ('responsibilities', 'achievements', 'outcomes'):
        for i, text in enumerate(value.get(key, []) or []):
            text=' '.join(text.split()).strip('• ')
            if text and text not in description:raw.append((f'{key}.{i}', text))
    result=[];seen=set()
    for path,text in raw:
        for i,piece in enumerate(sentences(text)):
            if piece in seen:continue
            seen.add(piece);choices=[piece]
            if piece.startswith('I '):choices.append(piece[2:3].upper()+piece[3:])
            clause=piece.split(';')[0].strip()
            if clause!=piece and len(clause.split())>=8:choices.append(clause.rstrip('.')+'.')
            short=re.sub(r'\s*\([^()\d]*\)', '', clause)
            if short!=clause and len(short.split())>=8:choices.append(short.rstrip('.')+'.')
            result.append({'id':f'{entry_id}:{path}:{i}', 'text':piece, 'variants':list(dict.fromkeys(choices)),
                           'source':f'confirmed_profile.{entry_id.replace("-", ".", 1)}.{path}', 'original':piece})
    return result

def technology_mapping(value, skills):
    """Keep declared entry technologies, but flag mentions of data being stripped.

    Explicit longer confirmed names in the entry (React Native versus React) take
    precedence. A skill used elsewhere is never assigned to this entry.
    """
    prose=' '.join([value.get('description',''), *value.get('responsibilities',[]), *value.get('achievements',[]), *value.get('outcomes',[])])
    declared=list(dict.fromkeys(value.get('technologies',[])+value.get('tools',[])))
    accepted=[];review=[]
    def appears(label):return bool(re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',prose,re.I))
    for tech in declared:
        if re.search(re.escape(tech)+r'.{0,16}(?:stripping|removal|sanitiz)|(?:strip|remove|sanitize)\w*\s+'+re.escape(tech),prose,re.I):
            review.append({'technology':tech,'reason':'Mention describes removed/input data, not evidence of using the technology.'});continue
        longer=next((s for s in skills if s.casefold().startswith(tech.casefold()+' ') and appears(s)),None)
        accepted.append(longer or tech)
    # An explicit confirmed skill mentioned in this very entry is safe evidence.
    for tech in skills:
        if appears(tech) and tech not in declared and not any(v['technology']==tech for v in review):
            if not any(tech.casefold() in v.casefold() for v in accepted):accepted.append(tech)
    return list(dict.fromkeys(accepted)), review

def project_links(facts, index):
    result=[];seen=set();labels={}
    # Classify within the project: a duplicate global link must not steal the
    # project's repository/deployment association during cross-profile dedup.
    for link in classify_links({'projects':[facts['projects'][index]]}):
        key=safe_url(link.url).rstrip('/')
        if key in seen:continue
        seen.add(key)
        label='Repository' if link.kind=='repository' else 'Deployment' if link.kind=='deployment' else link.label if link.platform!='additional' else 'Additional link'
        labels[label]=labels.get(label,0)+1
        if labels[label]>1:label+=f' {labels[label]}'
        result.append({'url':link.original_url,'label':label,'source':link.source.replace('projects.0.',f'projects.{index}.')})
    return result

def display_title(entry):
    # An em-dash suffix supplies assessment/team context, not the project name.
    # Keep the entire original title in the ledger and comparison report.
    return entry.title.split(' — ',1)[0] if entry.section=='projects' else entry.title

def ledger(facts, entries, protected):
    result=[];by_source={e.source:e for e in entries}
    def walk(value,path):
        entry=next((e for source,e in by_source.items() if path==source or path.startswith(source+'.')),None)
        if isinstance(value,dict):
            if path in by_source:
                result.append({'original':deepcopy(value),'normalized':entry.title+(' — '+entry.subtitle if entry.subtitle else ''),
                    'source_evidence':path,'confidence':'user_confirmed_not_independently_verified','entry_id':entry.id,
                    'relevance_status':'direct_evidence' if entry.relevance>0 else 'transferable_or_context',
                    'omission_allowed':entry.id not in protected})
            for key,item in value.items():walk(item,path+'.'+key)
        elif isinstance(value,list):
            for i,item in enumerate(value):walk(item,path+'.'+str(i))
        elif value not in (None,''):
            detail=any('.'+key in path for key in ('description','responsibilities','outcomes','achievements'))
            result.append({'original':deepcopy(value),'normalized':canonical_skill(value) if path.startswith('confirmed_profile.skills.') else ' '.join(str(value).split()),'source_evidence':path,
                'confidence':'user_confirmed_not_independently_verified','entry_id':entry.id if entry else None,
                'relevance_status':'direct_evidence' if entry and entry.relevance>0 else 'transferable_or_context',
                'omission_allowed':detail or not entry or entry.id not in protected})
            if isinstance(value,str):
                for metric in re.finditer(r'\b\d+(?:[,.]\d+)*(?:\s?%|\s?(?:messages|tests|listings|servers|weeks|MCP servers))?',value):
                    result.append({'original':metric[0],'normalized':metric[0],'source_evidence':path,
                        'confidence':'source_reported_not_independently_verified','entry_id':entry.id if entry else None,
                        'relevance_status':'source_metric_or_date','omission_allowed':True})
    walk(facts,'confirmed_profile')
    return result

def summaries(facts, job, entries):
    ranked=sorted(entries,key=lambda e:(-e.relevance,e.id))
    skills=[e for e in ranked if e.section=='skills'][:4]
    primary,_=primary_education(facts)
    degree=next((e for e in entries if e.id==f'education-{primary}'),None)
    work=next((e for e in ranked if e.section=='experience'),None)
    project=next((e for e in ranked if e.section=='projects'),None)
    title=job['title'].lower()
    domain=next((v for pattern,v in [(r'nlp|natural language','natural language processing'),(r'machine learning|\bml\b|\bai\b|artificial intelligence','AI and machine learning'),(r'backend|python','backend development'),(r'frontend|react|mobile','frontend and mobile development')] if re.search(pattern,title)),job['title'])
    identity=(degree.subtitle+' '+('candidate' if facts.get('experience_level')=='student' or 'expected' in degree.period.lower() else 'background')) if degree else work.subtitle if work and work.subtitle else 'Technical candidate'
    capability=', '.join(e.title for e in skills)
    first=f'{identity} bringing {capability} to {domain} work.' if capability else f'{identity} pursuing {domain} opportunities.'
    # Refer to a real role/project, not invented employer motivation or expertise.
    example=f'Experience includes work as {work.subtitle} at {work.title}.' if work else f'Project work includes {project.title}.' if project else f'Seeking to contribute to a {job["title"]} position.'
    refs=([degree.id] if degree else [])+[e.id for e in skills]+([work.id] if work else [project.id] if project else [])+['job.title']
    concise_skills=', '.join(e.title for e in skills[:2])
    concise=f'{identity} focused on {domain}'+(f' with {concise_skills}.' if concise_skills else '.')+' '+example
    return [{'id':'focused','text':first+' '+example,'evidence':refs},{'id':'concise','text':concise,'evidence':refs}]

def relevance_plan(facts, job, entries, context):
    def supported(label):
        query=evidence_tokens(label)
        return [{'id':e.id,'source':e.source,'kind':'confirmed_skill' if e.section=='skills' else 'project_supported' if e.section=='projects' else 'experience_supported'}
                for e in entries if query and query<=evidence_tokens(' '.join([e.title,e.subtitle,*e.technologies,*[b['original'] for b in e.bullets]]))]
    requirements=[{'skill':s,'evidence':supported(s),'status':'supported' if supported(s) else 'missing_evidence'} for s in job.get('required_skills',[])]
    preferred=[{'skill':s,'evidence':supported(s),'status':'supported' if supported(s) else 'missing_evidence'} for s in job.get('preferred_skills',[])]
    has_match=bool(context.get('matching')) and context['matching'].get('status')!='unavailable'
    has_gap=bool(context.get('skill_gaps'))
    return {'valid':bool(job.get('title') and entries),'status':'existing_context_and_local_evidence' if has_match and has_gap else 'computed_during_tailoring',
        'message':'Relevance analysis calculated for this tailoring run',
        'matching_context':'available' if has_match else 'unavailable', 'skill_gap_context':'available' if has_gap else 'unavailable',
        'limitation':None if has_gap else 'Skill-gap context unavailable; tailoring used confirmed job requirements and profile evidence.',
        'review_recommended':not(has_match and has_gap),'required':requirements,'preferred':preferred,
        'role_terms':sorted(evidence_tokens(job.get('title',''))),'related_role_terms':sorted(domain_terms(job)),
        'transferable_skills':[{'skill':e.title,'requirement':r,'relationship':relationship(e.title,r)} for e in entries if e.section=='skills' for r in job.get('required_skills',[]) if relationship(e.title,r) in {'related','transferable'}],
        'method':'Canonical required/preferred/title token overlap and advisory domain vocabulary; not a hybrid score or inferred preference.'}
