"""Evidence-constrained prose. Agents select supported sentences, never author facts.

This deliberately trades unrestricted paraphrasing for verifiable claims. User edits
are separately attributed to the user, require explicit attestation, and never become
confirmed profile facts. No numeric score or skill-gap implies acquired experience.
"""
import re
from app.schemas.cover_letters import Evidence, Sentence, LetterContent, SECTION_KEYS

PLACEHOLDERS = re.compile(r'lorem\s+ipsum|john\s+snow|youremail@email|xxx[^a-z]{0,4}xxx|yourlink|target\s+job|add_your_portfolio|\bnull\b|\[insert|\{\{', re.I)

def tokens(text):
    return set(re.findall(r'[a-z0-9+#]+', str(text).casefold())) - {'the','and','with','for','in','a','of','to','on'}

def fragment(text, limit=60):
    text=' '.join(str(text).split())
    if len(text.split())<=limit:return text.rstrip('. ')
    # Keep a complete source sentence where available; never invent a result.
    sentences=re.split(r'(?<=[.!?;])\s+',text)
    return sentences[0].rstrip('.; ') if len(sentences[0].split())<=limit else ''

def research(facts,job,context):
    target=tokens(' '.join([job.get('title',''), *job.get('required_skills',[]), *job.get('preferred_skills',[])]))
    evidence=[]
    for key in ('education','experience','projects','achievements','skills','career_objective'):
        values=facts.get(key,[])
        if isinstance(values,str):values=[values]
        for i,value in enumerate(values):
            if isinstance(value,dict):
                label=value.get('name') or value.get('role') or value.get('qualification') or key
                text=' '.join(str(v) if not isinstance(v,list) else ', '.join(v) for k,v in value.items() if k not in {'url','links','repository','deployment_url'} and v)
            else:label=key.replace('_',' ').title();text=str(value)
            score=len(tokens(text)&target)/max(1,len(target))
            evidence.append(Evidence(id=f'{key}-{i}',source=f'confirmed_profile.{key}.{i}' if key!='career_objective' else 'confirmed_profile.career_objective',label=label,text=text,relevance=round(score,3)))
    ranked=sorted(evidence,key=lambda e:(-e.relevance,e.id))
    selected=[e.id for key,n in [('experience',1),('projects',2),('education',1),('skills',5),('career_objective',1),('achievements',1)] for e in [v for v in ranked if v.id.startswith(key+'-')][:n] if e.relevance>0 or key in {'education','career_objective'}]
    warnings=[]
    if not job.get('company'):warnings.append('Company is unavailable. Generic salutation retained; no company facts invented.')
    if not facts.get('experience'):warnings.append('No confirmed professional experience. Only relevant academic/project evidence is used.')
    from app.services.application_transform import safe_url
    if any(not safe_url(url) for url in facts.get('links',[])):warnings.append('Malformed or unsafe profile links were omitted from the header; correct them in your profile if needed.')
    if not context.get('matching'):warnings.append('Matching explanation unavailable for this application; requirements are read directly from its job snapshot.')
    if not context.get('skill_gaps'):warnings.append('No current skill-gap result for this job/profile. Missing skills are never presented as acquired skills.')
    return {'evidence':[e.model_dump() for e in evidence],'selected_evidence_ids':selected,
        'job_requirements':job.get('required_skills',[]),'context':context,
        'excluded':[{'id':e.id,'reason':'Lower relevance or selection limit; not needed for this letter.'} for e in evidence if e.id not in selected],
        'missing':[k for k in ('name','email') if not facts.get(k)],'warnings':warnings,'confidence':'medium'}

def sentence_catalog(facts,job,plan,tone='formal'):
    selected=set(plan['selected_evidence_ids']);out=[]
    def add(id,section,text,refs):out.append(Sentence(id=id,section=section,text=text,evidence_ids=refs))
    title=job.get('title','');company=job.get('company','')
    destination=f' at {company}' if company else ''
    opening={'formal':f'Please consider my application for the {title} position{destination}.',
        'concise':f'I am applying for the {title} position{destination}.',
        'warm':f'I would welcome the opportunity to contribute in the {title} position{destination}.'}[tone]
    add('opening','opening',opening,['job.title']+(['job.company'] if company else []))
    add('approach','opening','I would welcome the opportunity to discuss how my background could contribute to the responsibilities described in the position. The projects and experience outlined below provide specific examples of the work I would bring to that discussion.',['job.title'])
    requirements=job.get('required_skills',[])[:4]
    if requirements:
        add('requirements','motivation',f'The position’s emphasis on {", ".join(requirements)} is of particular interest to me. I would value the opportunity to apply my relevant background to these responsibilities and develop further through practical work, feedback and collaboration.',['job.required_skills'])
    for e in plan['evidence']:
        if e['id'] not in selected:continue
        key,index=e['id'].rsplit('-',1);i=int(index)
        if key in {'experience','projects'}:
            v=facts[key][i];desc=fragment(v.get('description',''),65)
            if key=='experience':lead=f'My experience includes work as {v["role"]} at {v["organization"]}.'
            else:lead=f'One relevant project is {v["name"]}.'
            if desc:
                if re.match(r'^(Built|Developed|Designed|Implemented|Created|Evaluated|Led|Contributed|Worked|Tested|Deployed|Improved)\b',desc):lead+=' I '+desc[0].lower()+desc[1:]+'.'
                else:lead+=f' This work is described in my background as: {desc}.'
            tech=v.get('technologies',[])[:4]
            if tech:lead+=f' This work used {", ".join(tech)}.'
            add(e['id'],'experience',lead,[e['id']])
        elif key=='education':
            v=facts[key][i]
            add(e['id'],'opening',f'My education includes {v["qualification"]} at {v["institution"]}.',[e['id']])
        elif key=='achievements':add(e['id'],'experience',f'My achievements include: {facts[key][i]}.',[e['id']])
    skill_refs=[e for e in plan['evidence'] if e['id'] in selected and e['id'].startswith('skills-')]
    if skill_refs:
        add('skills','skills_alignment',f'My skills include {", ".join(e["text"] for e in skill_refs)}. I would use these foundations to approach the work described in the vacancy, clarify expectations early and evaluate my contribution against the requirements of each task. I would be glad to explain the scope of my experience with these skills in an interview.',[e['id'] for e in skill_refs])
    objective=fragment(facts.get('career_objective',''),45)
    if objective and 'career_objective-0' in selected and re.match(r'^(Build|Develop|Find|Pursue|Create|Work|Learn|Contribute|Seek)\b',objective,re.I):
        add('objective','motivation',f'My career aim is to {objective[0].lower()+objective[1:]}.',['career_objective-0'])
    add('closing','closing','Thank you for considering my application. I would appreciate the opportunity to discuss the role and walk through the examples above in more detail. Please contact me using the details provided; I would be happy to answer questions about my projects, background and suitability for the position.',['job.title'])
    return out

def assemble(facts,job,options,catalog,ids):
    known={s.id:s for s in catalog}
    if len(ids)!=len(set(ids)) or not set(ids)<=set(known):raise ValueError('unsupported_claim')
    selected=[known[id] for id in ids]
    if not {'opening','closing'}<={s.section for s in selected}:raise ValueError('incomplete_letter')
    sections={key:' '.join(s.text for s in selected if s.section==key) for key in SECTION_KEYS}
    return LetterContent(date=options['document_date'],company_name=job.get('company',''),job_title=job['title'],
        signature_name=facts['name'],signature_phone=facts.get('phone',''),signature_email=facts.get('email',''),claims=selected,**sections)

def validate_content(content,catalog,facts,job,*,user_sections=None,retained_sections=()):
    issues=[];known={s.id:s for s in catalog};user_sections=user_sections or {}
    for claim in content.claims:
        if claim.id not in known or claim!=known[claim.id]:issues.append('Unsupported factual claim: '+claim.id)
    for key in SECTION_KEYS:
        expected=' '.join(s.text for s in content.claims if s.section==key)
        if key in user_sections:expected=user_sections[key]
        if getattr(content,key)!=expected:issues.append('Unattributed paragraph: '+key)
    if content.signature_name!=facts.get('name') or content.signature_email!=facts.get('email','') or content.signature_phone!=facts.get('phone',''):issues.append('Candidate identity changed.')
    if content.job_title!=job.get('title') or content.company_name!=job.get('company',''):issues.append('Job identity changed.')
    if content.salutation!='Dear Hiring Manager,' or content.recipient_name:issues.append('Unsupported recipient or salutation.')
    text=' '.join(getattr(content,key) for key in SECTION_KEYS)
    if PLACEHOLDERS.search(text+' '+content.signature_name):issues.append('Template placeholder detected.')
    if content.company_name and content.company_name not in text:issues.append('Keep the selected company name in the letter body.')
    if any(ord(c)<32 and c not in '\n\t' for c in text):issues.append('Unsupported control character.')
    words=len(text.split())
    if not content.opening or not content.closing:issues.append('Opening and closing are required.')
    if words<80 or words>650:issues.append('Letter must contain 80–650 words; aim for 300–450 with sufficient evidence.')
    warnings=[]
    if words<300:warnings.append('Fewer than 300 words: source evidence is limited or a concise version was requested. No padding or invented experience added.')
    edits=[key for key in user_sections if key not in retained_sections]
    if edits:warnings.append('Edited paragraphs are explicitly user-supplied and attested, not independently verified profile evidence. Review every claim before approval.')
    return {'valid':not issues,'issues':issues,'warnings':warnings,'word_count':words,'grounding':'catalogue-checked agent claims; separately attributed user text','user_sections':edits,'retained_sections':list(retained_sections)}
