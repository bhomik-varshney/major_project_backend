"""Grounded mapping, document recommendations and validation; never profile writes."""
from copy import deepcopy
from datetime import date
import json
import re
from urllib.parse import urlparse
from app.schemas.applications import ApplicationDraft,ApplicationField,ApplicationForm
from app.services.skill_taxonomy import canonical_skill

ALIASES={
    'name':{'name','fullname','applicantname','candidatename','legalname'},
    'email':{'email','emailaddress','contactemail'},'phone':{'phone','phonenumber','mobile','contactnumber'},
    'location':{'location','currentlocation'},
    'city':{'city','currentcity'},'country':{'country','countryofresidence'},
    'first_name':{'firstname','givenname'},'middle_name':{'middlename'},'last_name':{'lastname','surname','familyname'},
    'education':{'education','academicbackground','qualifications'},
    'experience':{'experience','workhistory','employmenthistory'},
    'skills':{'skills','skillset','technicalskills'},'projects':{'projects','projectsummary'},
    'links':{'links'},'portfolio':{'portfolio','portfoliourl','website'},
    'linkedin':{'linkedin','linkedinurl'},'github':{'github','githuburl'},
    'institution':{'institution','university','college','school'},'degree':{'degree','qualification'},
    'graduation_year':{'graduationyear','graduatingyear','yearofgraduation'},
    'internships':{'internships','internshipexperience'},'certifications':{'certifications','certificates'},
    'relevant_experience':{'relevantexperience','describeyourrelevantexperience'},
    'greatest_achievement':{'greatestachievement','whatisyourgreatestachievement'},
    'motivation':{'whydoyouwanttojoinus','motivation','whyjoin'},
    'availability':{'availability','availablefrom','startdate'},'notice_period':{'noticeperiod'},
    'state':{'state','province'},'address':{'address','streetaddress','residentialaddress'},
    'project_answer':{'projectanswer','tellusaboutaproject','tellmeaboutaproject','describeaproject'},
    'education_answer':{'educationanswer','whatisyoureducation','describeyoureducation'},
    'additional_links':{'additionallinks','otherlinks'},
    **{key:{key,key+'url'} for key in ('gitlab','bitbucket','kaggle','leetcode','hackerrank','stackoverflow','behance','dribbble','scholar','orcid','twitter')},
    'career_objective':{'careerobjective','objective'},
}
SENSITIVE={'workauthorization','workpermission','visastatus','citizenship','nationality','gender','sex','race','ethnicity','religion','disability','veteran','demographic','salary','expectedpay','salaryexpectations','consent','dateofbirth','dob'}
def token(value):return re.sub(r'[^a-z0-9]','',value.casefold())


def semantic(field):
    names={token(field.semantic),token(field.id),token(field.label)}
    if field.sensitive or any(any(word in name for word in SENSITIVE) for name in names):return None
    matches={key for key,aliases in ALIASES.items() if names & aliases}
    if {'location','city'}<=matches and token(field.semantic) in ALIASES['location'] and token(field.label) in ALIASES['location']:matches.remove('city')
    if 'links' in matches and matches & {'portfolio','linkedin','github','gitlab','bitbucket','kaggle','leetcode','hackerrank','stackoverflow','behance','dribbble','scholar','orcid','twitter'}:matches.remove('links')
    # Conflicting labels/metadata are ambiguous, even when each alias is known.
    # Never pick whichever candidate field happens to be first in the map.
    return next(iter(matches)) if len(matches)==1 else None


def field_value(key,facts,job=None):
    """Return value, exact source path and whether interpretation needs review."""
    if key in {'first_name','middle_name','last_name','availability','notice_period'}:
        # The existing profile may not contain these structured values. Do not
        # infer name parts from full-name order, availability or notice periods.
        return facts.get(key),key,False
    if key in {'city','state','country'}:
        from app.services.job_locations import parse_location
        geo=parse_location(facts.get('location',''))
        return (None if geo['conflicting'] else geo.get(key)),'location',True
    if key in {'linkedin','github','portfolio','gitlab','bitbucket','kaggle','leetcode','hackerrank','stackoverflow','behance','dribbble','scholar','orcid','twitter','additional_links','links'}:
        from app.services.application_transform import classify_links
        candidates=classify_links(facts)
        if key=='additional_links':candidates=[link for link in candidates if link.kind=='additional']
        elif key=='links':return ([link.url for link in candidates] or None),'links',True
        else:candidates=[link for link in candidates if link.platform==key and (key not in {'github','gitlab','bitbucket'} or link.kind=='professional_profile')]
        if not candidates:return None,'links',True
        if key=='additional_links':return [link.url for link in candidates],'links',True
        selected=candidates[0]
        return selected.url,selected.source.removeprefix('confirmed_profile.'),len(candidates)>1
    if key in {'institution','degree','graduation_year'}:
        from app.services.application_transform import primary_education
        entries=facts.get('education',[]);index,derived=primary_education(facts)
        if index is None:return None,'education',True
        part={'institution':'institution','degree':'qualification','graduation_year':'end_year'}[key]
        value=entries[index].get(part)
        if key=='graduation_year' and not value:
            value=entries[index].get('expected_graduation');part='expected_graduation'
            years=re.findall(r'\b(?:19|20)\d{2}\b',str(value or ''))
            value=years[0] if len(years)==1 else None
            if not value:
                years=re.findall(r'\b(?:19|20)\d{2}\b',entries[index].get('period',''))
                if len(years)==2:value=years[-1];part='period';derived=True
        return value,f'education.{index}.{part}',derived
    if key=='internships':
        items=[(i,e) for i,e in enumerate(facts.get('experience',[])) if re.search(r'\bintern(?:ship)?\b',e.get('role',''),re.I)]
        return ([e for _,e in items] or None),'experience',True
    if key in {'relevant_experience','greatest_achievement','motivation','project_answer','education_answer'}:
        from app.services.application_transform import prepare_answer
        answer=prepare_answer(key,facts,job or {})
        return answer.concise or None,(answer.source_paths[0].removeprefix('confirmed_profile.') if answer.source_paths else ''),True
    return facts.get(key),key,False


def text_value(value,key=''):
    from app.services.application_transform import readable_value
    return readable_value(value,key)


def valid_value(field,value):
    if value is None or value=='' or value==[]:return not field.required
    if field.type=='checkbox':return isinstance(value,bool)
    if semantic(field) in {'links','additional_links','portfolio','linkedin','github','gitlab','bitbucket','kaggle','leetcode','hackerrank','stackoverflow','behance','dribbble','scholar','orcid','twitter'}:
        from app.services.application_transform import safe_url
        links=value if isinstance(value,list) else re.split(r'[,\n]+',value) if isinstance(value,str) else []
        if not links or not all(safe_url(link.strip()) for link in links if isinstance(link,str)):return False
    if field.type=='unsupported':return False
    if field.type=='multiselect':return isinstance(value,list) and all(isinstance(v,str) and v in field.options for v in value) and len(value)==len(set(value))
    if not isinstance(value,str) or len(value)>20000:return False
    if field.type in {'select','radio'}:return value in field.options
    if field.type=='email':return bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value))
    if field.type=='date':
        try:date.fromisoformat(value)
        except ValueError:return False
    return field.type!='file'


def map_fields(form,facts,job=None):
    output=[]
    for field in form.fields:
        result=ApplicationField(id=field.id)
        if field.type=='unsupported':
            result.warning='Unsupported form control. Complete it on the external site; no value will be sent.'
        elif field.type=='file':
            result.warning='Select and explicitly approve a document below; paths are never inferred.'
        else:
            key=semantic(field)
            value,path,derived=field_value(key,facts,job) if key else (None,'',False)
            if value:
                candidate=list(value) if field.type=='multiselect' and isinstance(value,list) else text_value(value,key)
                if valid_value(field,candidate):
                    result.value=candidate;result.source=f'confirmed_profile.{path}';result.auto_filled=True
                    result.evidence=f'Supported by confirmed {path}; review the source value before use.'
                    result.confidence='high';result.classification='safe_to_prefill'
                    if derived or key in {'links','location'}:
                        result.classification='user_confirmation_required';result.confidence='medium'
                        result.warning='Derived from confirmed information. Verify the selected value before use.'
                    if key in {'relevant_experience','greatest_achievement','motivation','project_answer','education_answer'}:
                        result.suggested_answer=True;result.warning='Source-grounded draft excerpt. Edit it to answer the question; no employer-specific motivation or achievement ranking was inferred.'
                else:result.warning='Confirmed value is incompatible with the form options/type; supply the correct value.'
            else:result.warning='User input required; no supported confirmed value. Sensitive/legal/demographic fields are never inferred.'
        output.append(result)
    return output


def prepare_draft(form,facts,job,documents):
    form=ApplicationForm.model_validate(form)
    known={canonical_skill(s) for s in facts.get('skills',[])}
    required=job.get('required_skills',[])
    recommendations=[d['id'] for d in documents if d['confirmed_resume']]
    draft=ApplicationDraft(form=form,fields=map_fields(form,facts,job),presentation_version="1.0",document_options=documents,
        requirements={'required_skills':required,'preferred_skills':job.get('preferred_skills',[]),
            'matched_skills':[s for s in required if canonical_skill(s) in known],
            'missing_skill_evidence':[s for s in required if canonical_skill(s) not in known],
            'education':job.get('education_requirements',[]),'responsibilities':job.get('responsibilities',[]),
            'recommended_resume_ids':recommendations,
            'document_recommendation':'Review the confirmed resume or generate a separate job-specific version in Resume tailoring. Every attachment requires your review.',
            'cover_letter':'required' if any(f.required and token(f.semantic or f.label)=='coverletter' for f in form.fields) else 'optional_or_unspecified'},
        warnings=['All listings in the current provider are synthetic. Demo preparation does not contact employers.',
                  'Document generation is available in the dedicated resume and cover-letter workspaces. Attach only approved documents you have reviewed.'])
    draft.validation=validate_draft(draft)
    draft.missing_inputs=draft.validation['missing_required']
    return draft


def validate_draft(draft):
    fields={f.id:f for f in draft.form.fields};seen=set();errors=[];missing=[];unreviewed=[]
    for value in draft.fields:
        field=fields.get(value.id)
        if field is None or value.id in seen:errors.append(value.id);continue
        seen.add(value.id)
        if field.type=='file':
            role='cover_letter' if token(field.semantic or field.label)=='coverletter' else 'resume' if 'resume' in token(field.semantic or field.label) else 'supporting'
            if field.required and not any(d['role']==role and d['approved'] for d in draft.documents):missing.append(field.id)
            continue
        if field.required and (value.value is None or value.value=='' or value.value==[]):missing.append(field.id)
        elif not valid_value(field,value.value):errors.append(field.id)
        if value.value is not None and value.value!='' and value.value!=[] and not value.approved:unreviewed.append(field.id)
        if value.source.startswith('confirmed_profile.') and semantic(field) is None:errors.append(field.id)
    if seen!=set(fields):errors.append('__field_set__')
    return {'missing_required':missing,'invalid_fields':sorted(set(errors)),'unreviewed_fields':unreviewed,
            'ready_for_prefill':not missing and not errors and not unreviewed,'unsupported_claims':0,
            'sensitive_values_inferred':False,'profile_mutations':0}


def surface(application,*,approved_only=False):
    """A2UI 0.8 interaction subset: allowlisted components bound to typed state."""
    draft=application.draft
    if not draft:return {'version':'0.8','messages':[]}
    components=[]
    for field in draft['form']['fields']:
        kind={'checkbox':'CheckBox','select':'MultipleChoice','radio':'MultipleChoice',
              'multiselect':'MultipleChoice','date':'DateTimeInput','file':'Text','unsupported':'Text'}.get(field['type'],'TextField')
        components.append({'id':field['id'],'component':{kind:{'label':{'literalString':field['label']},
            'value':{'path':f'/fields/{field["id"]}'},'required':field['required'],'options':field.get('options',[])}}})
    auxiliary=[
        {'id':'application-summary','component':{'Text':{'text':{'literalString':f"{application.job_snapshot.get('title','Application')} · {application.job_snapshot.get('company','')}"}}}},
        {'id':'application-status','component':{'Text':{'text':{'literalString':application.status}}}},
        {'id':'missing-information','component':{'Text':{'text':{'literalString':'Required input: '+', '.join(draft.get('missing_inputs',[]))}}}},
        {'id':'document-selection','component':{'Text':{'text':{'literalString':f"{len(draft.get('documents',[]))} selected documents. Review and approve in the host workspace."}}}},
        {'id':'approval-control','component':{'Text':{'text':{'literalString':'Every field and document requires host approval. Submission is performed by the user.'}}}},
        {'id':'application-timeline','component':{'Text':{'text':{'literalString':'Operational timeline and scheduled follow-ups are available in the host workspace.'}}}},
    ]
    if draft.get('preview'):
        sections=[]
        for group in draft['preview']['sections']:
            heading='heading-'+group['id'];container='section-'+group['id']
            sections.append({'id':heading,'component':{'Text':{'text':{'literalString':group['label']}}}})
            ids=[field['field_id'] for field in group['fields']]
            sections.append({'id':container,'component':{'Column':{'children':{'explicitList':[heading,*ids]}}}})
        components=auxiliary+sections+components
    else:components=auxiliary+components
    components.insert(0,{'id':'root','component':{'Column':{'children':{'explicitList':[c['id'] for c in auxiliary]+['section-'+g['id'] for g in draft['preview']['sections']] if draft.get('preview') else [c['id'] for c in components]}}}})
    values=[deepcopy(v) for v in draft['fields']]
    if approved_only:
        for value in values:
            if not value['approved']:value['value']=None
    bindings=[]
    for value in values:
        item={'key':value['id']};content=value['value']
        if isinstance(content,bool):item['valueBoolean']=content
        elif isinstance(content,str):item['valueString']=content
        elif isinstance(content,list):item['valueMap']=[{'key':str(i),'valueString':v} for i,v in enumerate(content)]
        else:continue
        bindings.append(item)
    return {'version':'0.8','catalog':'ascendra-application-safe-subset','messages':[
        {'surfaceUpdate':{'surfaceId':application.id,'components':components}},
        {'dataModelUpdate':{'surfaceId':application.id,'path':'/','contents':[{'key':'fields','valueMap':bindings}]}},
        {'beginRendering':{'surfaceId':application.id,'root':'root'}}],
        'preview':draft.get('preview'),'form':draft['form'],'fields':values,'revision':application.revision,
        'application_id':application.id,'actions':['review_application'] if not application.active_operation and application.status in {'needs_user_review','draft_ready','approved_for_prefill','waiting_for_user','external_site_unavailable','unsupported_form','failed'} and not application.receipt.get('applied_field_ids') else [],'is_demo':application.provider=='demo','status':application.status}
