"""Explicit normalization with source paths. Descriptions never invent skill facts."""
from hashlib import sha256
import json
import re
import unicodedata

from app.services.skill_taxonomy import ALIASES, canonical_skill, normalize_label, normalize_phrases
from app.schemas.profile import ProfileInput
from app.services.job_roles import derived_topics
from app.services.job_locations import parse_location

MATCH_FIELDS=('skills','career_objective','projects','experience','education','preferred_roles','preferred_locations','location','experience_level')

STOP={'a','an','the','and','or','to','of','in','for','with','on','as','is','are','be','you','your','our','we','will','from','this','that','at'}
ROLE_STOP=STOP|{'intern','internship','junior','senior','trainee','graduate','fresher','entry','level','engineer','developer','engineering','assistant','specialist','associate'}


def tokens(text):
    text=normalize_phrases(text)
    for old,new in [('back-end','backend'),('front-end','frontend'),('full-stack','fullstack'),('full stack','fullstack')]:text=text.replace(old,new)
    words=re.findall(r'[\w]+(?:\+\+|#)?(?:[./][\w+#]+)*',text)
    expanded=' '.join(ALIASES.get(word,word) for word in words).split()
    return [word for word in expanded if (word not in STOP and len(word)>1) or word in {'c','r'}]


def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def matching_facts(confirmed):
    # Exclude identity/contact fields and all document draft data by construction.
    return {field:confirmed[field] for field in MATCH_FIELDS if field in confirmed}


def flatten_text(value):
    if isinstance(value,dict):return ' '.join(flatten_text(v) for k,v in value.items() if k not in {'url','links','repository','deployment_url'})
    if isinstance(value,list):return ' '.join(flatten_text(v) for v in value)
    return str(value or '')


def normalize_candidate(facts):
    facts=matching_facts(facts)
    canonical=ProfileInput.model_validate({'name':'_',**facts}).model_dump()
    mappings=[]
    for i,skill in enumerate(canonical['skills']):
        mappings.append(dict(original=skill,normalized=canonical_skill(skill),source_path=f'skills.{i}'))
    for field in ('projects','experience'):
        for i,entry in enumerate(canonical[field]):
            for source in ('technologies','tools'):
                for j,skill in enumerate(entry.get(source,[])):
                    mappings.append(dict(original=skill,normalized=canonical_skill(skill),source_path=f'{field}.{i}.{source}.{j}'))
    skill_tokens=sorted({m['normalized'] for m in mappings if m['normalized']})
    projects=flatten_text(canonical['projects']+canonical['experience'])
    # Only explicitly confirmed preferences are treated as preferences. Home city
    # stays available as context, never an invented relocation constraint.
    return dict(facts=facts,derived_role_topics=derived_topics(facts),location_details=parse_location(canonical['location']),remote_preference=any('remote' in p.casefold() for p in canonical['preferred_locations']),normalized_metadata={'preferred_roles':[normalize_label(v) for v in canonical['preferred_roles']],'preferred_locations':[normalize_label(v) for v in canonical['preferred_locations']],'location':normalize_label(canonical['location']),'experience_level':normalize_label(canonical['experience_level']),'education':[normalize_label(e['qualification']) for e in canonical['education']]},skill_mappings=mappings,skill_tokens=skill_tokens,text=flatten_text(facts),
        project_experience_text=projects,preferred_roles=canonical['preferred_roles'],preferred_locations=canonical['preferred_locations'],
        interests_text=flatten_text([canonical['preferred_roles'],canonical['career_objective']]),
        education=canonical['education'],experience=canonical['experience'],experience_level=canonical['experience_level'],
        warnings=[f'{field.replace("_"," ")} is not confirmed or is empty.' for field in ('skills','preferred_roles','preferred_locations','education','experience_level') if not canonical[field]])


def normalize_job(job):
    raw=job.model_dump(mode='json') if hasattr(job,'model_dump') else dict(job)
    required=[dict(original=v,normalized=canonical_skill(v),source_path=f'required_skills.{i}') for i,v in enumerate(raw.get('required_skills',[])) if v.strip()]
    preferred=[dict(original=v,normalized=canonical_skill(v),source_path=f'preferred_skills.{i}') for i,v in enumerate(raw.get('preferred_skills',[])) if v.strip()]
    # A preferred skill repeated as required is one requirement, not two credits.
    required_tokens={m['normalized'] for m in required}
    preferred=[m for m in preferred if m['normalized'] not in required_tokens]
    return dict(original=raw,location_details=parse_location(raw.get('location',''),raw.get('country',''),raw.get('work_mode','')),required=required,preferred=preferred,
        text=flatten_text({k:raw.get(k) for k in ('title','category','description','responsibilities','required_skills','preferred_skills','education_requirements','experience_level')}),
        role_text=normalize_label(raw.get('title','')+' '+raw.get('category','').replace('_',' ')),
        normalized_metadata={k:normalize_label(raw.get(k,'')) for k in ('title','category','industry','location','country','experience_level','work_mode')},
        project_text=flatten_text([raw.get('description',''),raw.get('responsibilities',[])]))


def deduplicate(jobs):
    seen=set();output=[];duplicates=[]
    for job in sorted(jobs,key=lambda j:j.id):
        group=job.metadata.duplicate_group
        signature=fingerprint([job.title.casefold(),job.company.casefold(),job.location.casefold(),job.work_mode,sorted(canonical_skill(s) for s in job.required_skills)])
        key=('group',group) if group else ('content',signature)
        if key in seen:duplicates.append(job.id)
        else:seen.add(key);output.append(job)
    return output,duplicates
