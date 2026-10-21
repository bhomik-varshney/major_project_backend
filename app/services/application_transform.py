"""Confirmed facts -> readable application values. No network, inference of legal facts or profile writes."""
from copy import deepcopy
from datetime import date
import re
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from app.schemas.application_preview import (ApplicationPreview,ApplicationSection,PreviewField,RepeatedProfileEntry,
    LinkClassification,PreparedAnswer,MissingField,MCPFieldMapping,PreviewDocument)
from app.services.match_normalization import fingerprint

SECTIONS={'personal':'Personal information','location':'Location','links':'Professional links','education':'Education',
    'experience':'Experience','internships':'Internships','projects':'Projects','skills':'Skills',
    'certifications':'Certifications and achievements','questions':'Application questions'}
PLATFORMS={'linkedin.com':('linkedin','LinkedIn'),'github.com':('github','GitHub'),'gitlab.com':('gitlab','GitLab'),
    'bitbucket.org':('bitbucket','Bitbucket'),'kaggle.com':('kaggle','Kaggle'),'leetcode.com':('leetcode','LeetCode'),
    'hackerrank.com':('hackerrank','HackerRank'),'stackoverflow.com':('stackoverflow','Stack Overflow'),
    'behance.net':('behance','Behance'),'dribbble.com':('dribbble','Dribbble'),
    'scholar.google.com':('scholar','Google Scholar'),'orcid.org':('orcid','ORCID'),'x.com':('twitter','X/Twitter'),'twitter.com':('twitter','X/Twitter')}
LABELS={'name':'Full name','email':'Email','phone':'Phone','location':'Location','education':'Education',
    'experience':'Experience','projects':'Projects','skills':'Skills','links':'Professional links',
    'career_objective':'Career objective','achievements':'Achievements','certifications':'Certifications'}


def safe_url(value):
    """Never invent a scheme, follow URLs, or retain credential-bearing URLs."""
    if not isinstance(value,str) or re.search(r'[\s\x00-\x1f\x7f\\]',value):return None
    try:
        parsed=urlsplit(value)
        if parsed.scheme.lower() not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:return None
        port=parsed.port
        if any(key.casefold() in {'token','access_token','api_key','apikey','password','secret','session','auth','authorization','signature','code'} for key,_ in parse_qsl(parsed.query,keep_blank_values=True)):return None
        host=parsed.hostname.lower().encode('idna').decode()
        if '.' not in host:return None
        authority=host+(f':{port}' if port is not None else '')
        # Remove tracking only; retain all destination-relevant parameters/path/fragment.
        pairs=parse_qsl(parsed.query,keep_blank_values=True)
        query=urlencode([(key,val) for key,val in pairs if not key.lower().startswith('utm_') and key.lower() not in {'fbclid','gclid'}]) if any(key.lower().startswith('utm_') or key.lower() in {'fbclid','gclid'} for key,_ in pairs) else parsed.query
        return urlunsplit((parsed.scheme.lower(),authority,parsed.path,query,parsed.fragment))
    except (ValueError,UnicodeError):return None


def classify_links(facts):
    sources=[]
    for i,url in enumerate(facts.get('links',[])):sources.append((url,f'links.{i}','Confirmed professional link',''))
    for i,project in enumerate(facts.get('projects',[])):
        for key in ('repository','deployment_url','url'):
            if project.get(key):sources.append((project[key],f'projects.{i}.{key}',f"Project: {project.get('name','Project')}",key))
        for j,url in enumerate(project.get('links',[])):sources.append((url,f'projects.{i}.links.{j}',f"Project: {project.get('name','Project')}",'project'))
    for key in ('portfolio','personal_website'):
        if facts.get(key):sources.append((facts[key],key,'Explicit personal website','portfolio'))
    result=[];seen=set();primary=set()
    for original,path,evidence,context in sources:
        url=safe_url(original)
        if not url:continue
        parsed=urlsplit(url);host=parsed.hostname;platform='additional';label='Additional link';kind='additional'
        for domain,(name,title) in PLATFORMS.items():
            if host==domain or host.endswith('.'+domain):platform=name;label=title;kind='professional_profile';break
        parts=[p for p in parsed.path.split('/') if p]
        if platform in {'github','gitlab','bitbucket'} and (len(parts)>=2 and parts[0] not in {'users','u'} or context=='repository'):
            kind='repository'
        if context=='deployment_url' and kind!='repository':kind='deployment'
        if context=='repository':kind='repository'
        hosted=any(host==domain or host.endswith('.'+domain) for domain in ('vercel.app','netlify.app','onrender.com','github.io'))
        if platform=='additional':
            if context=='portfolio' or not path.startswith('projects.') and ('portfolio' in host+parsed.path.lower() or parsed.path in {'','/','/about','/about/'} and any(part.lower() in host.rsplit('.',1)[0] for part in facts.get('name','').split() if len(part)>=4)):
                platform='portfolio';label='Portfolio / personal website';kind='portfolio'
            elif hosted:kind='deployment';label='Deployment'
        if kind=='repository':label+=' repository'
        if kind=='deployment':label='Project deployment'
        if platform in {'scholar','orcid'}:kind='publication'
        key=(url,kind)
        if key in seen:continue
        seen.add(key)
        # A repository never becomes a primary professional profile for that platform.
        primary_key=(platform,kind);is_primary=primary_key not in primary;primary.add(primary_key)
        result.append(LinkClassification(original_url=original,url=url,platform=platform,kind=kind,label=label,
            primary=is_primary,source=f'confirmed_profile.{path}',evidence=evidence))
    return result


def primary_education(facts):
    entries=facts.get('education',[])
    if len(entries)==1:return 0,False
    def level(entry):
        text=entry.get('qualification','').lower()
        for pattern,rank in [(r'ph\.?d|doctor',5),(r'm\.?\s?tech|master|m\.?sc|mba',4),(r'b\.?\s?tech|bachelor|b\.?e\b|b\.?sc|bca|bba',3),(r'diploma',2),(r'12|senior secondary|higher secondary',1)]:
            if re.search(pattern,text):return rank
        return 0
    levels=[level(e) for e in entries]
    if levels and max(levels)>0 and levels.count(max(levels))==1:return levels.index(max(levels)),True
    return None,True


def readable_date(value):
    if not value:return ''
    try:return date.fromisoformat(str(value)).strftime('%d %b %Y')
    except ValueError:return str(value)


def repeated_entries(key,values):
    result=[]
    for i,item in enumerate(values or []):
        if isinstance(item,str):result.append(RepeatedProfileEntry(title=item,source=f'confirmed_profile.{key}.{i}'));continue
        if not isinstance(item,dict):continue
        title=item.get('institution') if key=='education' else item.get('name') if key=='projects' else item.get('role')
        subtitle=item.get('qualification') if key=='education' else item.get('organization') if key in {'experience','internships'} else item.get('role')
        details={}
        labels={'location':'Location','specialization':'Field of study','grade':'Grade / CGPA','period':'Period',
            'start_year':'Start year','end_year':'Graduation year','expected_graduation':'Expected graduation',
            'start_date':'Start date','end_date':'End date','current_status':'Current status','employment_type':'Employment type'}
        for field,label in labels.items():
            if item.get(field):details[label]=readable_date(item[field])
        bullets=[]
        if item.get('description'):bullets.append(item['description'])
        for field in ('responsibilities','achievements','outcomes'):
            bullets.extend(value for value in item.get(field,[]) if isinstance(value,str) and value)
        technologies=list(dict.fromkeys(item.get('technologies',[])+item.get('tools',[])))
        if technologies:details['Technologies']=', '.join(technologies)
        links=classify_links({'projects':[item]}) if key=='projects' else []
        result.append(RepeatedProfileEntry(title=title or 'Confirmed entry',subtitle=subtitle or '',details=details,
            bullets=list(dict.fromkeys(bullets)),links=links,source=f'confirmed_profile.{key}.{i}'))
    return result


def readable_value(value,key=''):
    if isinstance(value,bool) or value is None:return value
    if isinstance(value,str):return value
    if isinstance(value,(int,float)):return str(value)
    if isinstance(value,list):
        if all(isinstance(v,str) for v in value):return ', '.join(value)
        entries=repeated_entries(key,value)
        return '\n\n'.join('\n'.join([entry.title+(' — '+entry.subtitle if entry.subtitle else ''),
            *[f'{label}: {text}' for label,text in entry.details.items()],*[f'• {bullet}' for bullet in entry.bullets],
            *[f'{link.label}: {link.url}' for link in entry.links]]) for entry in entries)
    if isinstance(value,dict):return readable_value([value],key)
    return ''


def prepare_answer(kind,facts,job):
    evidence=[];paths=[];concise='';detailed='';reason=''
    relevant=set(re.findall(r'\w+', ' '.join(job.get('required_skills',[])+[job.get('title','')]).casefold()))
    def rank(item):return len(relevant & set(re.findall(r'\w+',str(item).casefold())))
    experience=sorted(enumerate(facts.get('experience',[])),key=lambda pair:(-rank(pair[1]),pair[0]))
    projects=sorted(enumerate(facts.get('projects',[])),key=lambda pair:(-rank(pair[1]),pair[0]))
    if kind=='relevant_experience':
        parts=[]
        for index,item in experience[:2]:
            text=f"{item.get('role','Experience')} at {item.get('organization','the confirmed organisation')}."
            if item.get('description'):text+=' '+item['description']
            parts.append(text);paths.append(f'confirmed_profile.experience.{index}');evidence.append(f"{item.get('role','Experience')} — {item.get('organization','')}")
        if not parts and projects:
            index,item=projects[0];parts.append(f"Project: {item.get('name','Project')}. {item.get('description','')}");paths.append(f'confirmed_profile.projects.{index}');evidence.append(item.get('name','Project'))
        detailed='\n\n'.join(parts)
    elif kind=='project_answer' and projects:
        index,item=projects[0];detailed=f"{item.get('name','Project')}. {item.get('description','')}"
        if item.get('role'):detailed+=f" My role: {item['role']}."
        if item.get('technologies'):detailed+=' Technologies: '+', '.join(item['technologies'])+'.'
        if item.get('outcomes'):detailed+=' Outcomes: '+'; '.join(item['outcomes'])+'.'
        paths=[f'confirmed_profile.projects.{index}'];evidence=[item.get('name','Project')]
    elif kind=='motivation':
        # Job alignment, not invented enthusiasm about an employer or its culture.
        skills=[skill for skill in facts.get('skills',[]) if skill.casefold() in {s.casefold() for s in job.get('required_skills',[])}]
        parts=[]
        if facts.get('career_objective'):parts.append('My stated career objective: '+facts['career_objective']);paths.append('confirmed_profile.career_objective');evidence.append('Confirmed career objective')
        if skills:parts.append(f"For the {job.get('title','selected')} role, I can draw on my confirmed skills in {', '.join(skills[:6])}.");paths.append('confirmed_profile.skills');evidence.append('Confirmed skills matching the listed requirements')
        if projects:
            index,item=projects[0]
            if rank(item)>0:parts.append(f"Relevant project: {item.get('name','Project')}. {item.get('description','')}");paths.append(f'confirmed_profile.projects.{index}');evidence.append(item.get('name','Project'))
        detailed='\n\n'.join(parts);reason='Add your own employer-specific reasons; company motivation has not been inferred.'
    elif kind=='greatest_achievement':
        achievements=facts.get('achievements',[])
        if len(achievements)==1:detailed=readable_value(achievements[0]);paths=['confirmed_profile.achievements.0'];evidence=['Confirmed achievement']
        elif achievements:reason='Several achievements are confirmed. Choose which achievement you consider greatest.'
    elif kind=='education_answer':
        detailed=readable_value(facts.get('education',[]),'education');paths=['confirmed_profile.education'] if detailed else [];evidence=['Confirmed education'] if detailed else []
    elif kind in {'availability','notice_period'}:
        detailed=facts.get(kind,'');paths=[f'confirmed_profile.{kind}'] if detailed else [];evidence=['Explicit confirmed '+kind.replace('_',' ')] if detailed else []
    else:reason='Provide an explicit answer; no supported confirmed evidence answers this question.'
    def shorten(text,limit):
        if len(text)<=limit:return text
        return text[:limit-1].rsplit(' ',1)[0]+'…'
    detailed=shorten(detailed,2400);concise=shorten(detailed.replace('\n\n',' '),600)
    if not detailed and not reason:reason='No confirmed evidence is available. Please supply an answer.'
    return PreparedAnswer(question_type=kind,concise=concise,detailed=detailed,evidence=evidence,source_paths=paths,
        confidence=.8 if detailed else 0,reason=reason)


def section_for(key):
    if key in {'name','first_name','middle_name','last_name','email','phone'}:return 'personal'
    if key in {'location','city','state','country','address','relocation'}:return 'location'
    if key in {'links','portfolio','additional_links',*{name for name,_ in PLATFORMS.values()}}:return 'links'
    if key in {'education','institution','degree','graduation_year','start_year','grade','specialization'}:return 'education'
    if key in {'experience','internships','projects','skills'}:return key
    if key in {'certifications','achievements'}:return 'certifications'
    return 'questions'


def display_fields(draft,facts=None,job=None):
    """Read-only compatibility for old drafts: never mutate their approved packets."""
    import json
    fields=deepcopy(draft.fields)
    specs={f.id:f for f in draft.form.fields}
    from app.services.application_preparation import semantic,map_fields
    proposed={f.id:f for f in map_fields(draft.form,facts,job)} if facts is not None and draft.presentation_version!='1.0' else {}
    for index,field in enumerate(fields):
        if field.id in proposed and not field.edited_by_user:
            replacement=proposed[field.id]
            if replacement.value!=field.value:replacement.approved=False;fields[index]=replacement;field=replacement
        if isinstance(field.value,str) and field.source.startswith('confirmed_profile.') and field.value.lstrip().startswith(('[','{')):
            try:structured=json.loads(field.value)
            except ValueError:continue
            if isinstance(structured,(list,dict)):field.value=readable_value(structured,semantic(specs[field.id]) or '')
    return fields


def build_preview(draft,facts,job,engine='local_mcp'):
    from app.services.application_preparation import semantic,SENSITIVE,token
    sections={key:ApplicationSection(id=key,label=label) for key,label in SECTIONS.items()}
    values={f.id:f for f in display_fields(draft,facts,job)};missing=[];mappings=[]
    for spec in draft.form.fields:
        value=values[spec.id];key=semantic(spec)
        sensitive=spec.sensitive or any(word in token(spec.label)+' '+token(spec.semantic) for word in SENSITIVE)
        section=section_for(key);present=value.value is not None and value.value!='' and value.value!=[]
        answer=prepare_answer(key,facts,job) if key in {'motivation','relevant_experience','greatest_achievement','project_answer','education_answer'} and not value.edited_by_user else None
        status='unsupported' if spec.type=='unsupported' else 'sensitive' if sensitive else 'generated_draft' if value.suggested_answer and present else 'user_confirmation_required' if present and value.classification=='user_confirmation_required' else 'safe_to_prefill' if present else 'conflict' if key in {'institution','degree','graduation_year'} and len(facts.get('education',[]))>1 else 'missing'
        if value.approved and present:status='ready'
        source='Your application edit' if value.edited_by_user else 'Confirmed '+LABELS.get((value.source.removeprefix('confirmed_profile.').split('.')[0]),'profile') if value.source else 'User input needed'
        reason='' if present else 'Optional — provide only if applicable.' if not spec.required else 'Required before prefilling.'
        if spec.type=='file':
            selected=any(d.get('role')==('cover_letter' if 'cover' in spec.id else 'resume') for d in draft.documents)
            status='ready' if selected else 'missing';reason='Choose a reviewed document in Form view.'
        elif status=='conflict':reason='Multiple education entries are available. Choose the appropriate qualification.'
        elif status=='unsupported':reason='This employer control must be completed manually.'
        elif sensitive:reason='Never inferred. Enter and explicitly confirm your answer.'
        elif not present and key in {'first_name','middle_name','last_name'} and facts.get('name'):reason='Full name is confirmed. Enter this name part explicitly; name order is not assumed.'
        elif not present and key=='city':reason='The confirmed location does not identify a city. Enter your current city.'
        elif not present and key=='github':reason='A personal GitHub profile is not confirmed. Project repositories, if available, are shown separately.'
        view=PreviewField(field_id=spec.id,label=spec.label,input_type=spec.type,display_value=value.value,status=status,
            source_label=source,confidence={'high':.95,'medium':.75,'unknown':0}[value.confidence],approved=value.approved,
            required=spec.required,auto_filled=value.auto_filled,sensitive=sensitive,reason=reason,
            evidence=answer.evidence if answer else [source] if present else [],answer=answer)
        sections[section].fields.append(view)
        mappings.append(MCPFieldMapping(field_id=spec.id,label=spec.label,source_label=source,status=status,required=spec.required))
        if not present and spec.type!='file':missing.append(MissingField(field_id=spec.id,label=spec.label,required=spec.required,reason=reason))
    for key in ('education','experience','projects','certifications'):
        sections[key].entries=repeated_entries(key,facts.get(key,[]))
    sections['internships'].entries=repeated_entries('experience',[entry for entry in facts.get('experience',[]) if re.search(r'\bintern(?:ship)?\b',entry.get('role',''),re.I)])
    sections['certifications'].entries+=repeated_entries('achievements',facts.get('achievements',[]))
    categories={
        'Programming languages':{'python','javascript','typescript','java','c','c++','c#','go','rust','kotlin','swift','r'},
        'Frameworks':{'react','react native','next.js','django','fastapi','flask','spring','angular','vue'},
        'Databases':{'sql','postgresql','mysql','sqlite','mongodb','redis'},
        'Cloud':{'aws','azure','gcp','google cloud','cloud computing'},
        'AI / ML':{'ai','ml','machine learning','deep learning','nlp','llm','pytorch','tensorflow','scikit-learn','generative ai','computer vision'},
        'Tools':{'git','docker','kubernetes','linux','github','gitlab','postman','figma'},
        'Soft skills':{'communication','teamwork','leadership','problem solving','time management'}}
    grouped={label:[] for label in [*categories,'Other skills']}
    for skill in facts.get('skills',[]):
        label=next((label for label,values in categories.items() if skill.casefold() in values),'Other skills')
        grouped[label].append(skill)
    sections['skills'].entries=[RepeatedProfileEntry(title=label,details={'Skills':', '.join(values)},source='Confirmed profile') for label,values in grouped.items() if values]
    sections['links'].links=classify_links(facts)
    # Provenance paths belong to audit contracts; never send them as visible labels.
    for section in sections.values():
        for entry in section.entries:
            entry.source='Confirmed profile'
            for link in entry.links:link.source='Confirmed project'
        for link in section.links:link.source='Confirmed profile'
        for field in section.fields:
            if field.answer:field.answer.source_paths=[]
    documents=[PreviewDocument(label=d['filename'],role=d['role'],content_type=d['content_type'],size_bytes=d['size_bytes'],approved=d.get('approved',False)) for d in draft.documents]
    result=ApplicationPreview(title=job.get('title','Application'),company=job.get('company',''),source=job.get('source',''),
        application_url=safe_url(job.get('application_url','')) or '',sections=list(sections.values()),documents=documents,
        missing_fields=missing,mappings=mappings,engine=engine,is_mock=draft.form.is_demo)
    result.content_hash=fingerprint(result.model_dump(exclude={'content_hash'}))
    return result
