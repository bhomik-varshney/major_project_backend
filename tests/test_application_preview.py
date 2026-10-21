"""Application-only regressions using an anonymised version of the screenshot structure."""
from copy import deepcopy
import json
import time
import pytest
from pydantic import ValidationError
from app.schemas.applications import ApplicationForm,FormField,ApplicationDraft
from app.services.application_preparation import prepare_draft,map_fields,valid_value
from app.services.application_transform import classify_links,safe_url,build_preview,prepare_answer,primary_education
from app.integrations.application_mcp import preview,call,MCPUnavailable
from app.mcp.application_server import scenario_form
from app.db.models import ApplicationRecord
from test_applications import create,act,review,BASE
from test_application_recovery import wait

FACTS={'name':'Student Example','email':'student@example.com','location':'Bengaluru, India',
    'education':[{'institution':'Example Engineering University','qualification':'B.Tech Computer Engineering','start_year':'2023','end_year':'2027','grade':'8.2'},
        {'institution':'Example Senior School','qualification':'Senior Secondary XII','end_year':'2023'},
        {'institution':'Example School','qualification':'Secondary X','end_year':'2021'}],
    'experience':[{'organization':'Example Software','role':'Developer Intern','description':'Built mobile application components.','technologies':['React Native']},
        {'organization':'Example Training','role':'ML Trainee','description':'Built a text classifier.','technologies':['Python','NLP']}],
    'projects':[{'name':'Career Helper','description':'Built a retrieval service.','role':'Developer','technologies':['Python','SQL'],'outcomes':['Evaluated retrieval quality'],'repository':'https://github.com/student/career-helper','deployment_url':'https://career-helper.vercel.app'}],
    'skills':['Python','SQL','NLP'],'career_objective':'Build reliable AI applications.',
    'certifications':['Machine Learning Fundamentals'],'achievements':['Student hackathon finalist'],
    'links':['https://www.LinkedIn.com/in/student?utm_source=resume','https://github.com/student','https://portfolio.example.com','https://kaggle.com/student','https://leetcode.com/student','https://gitlab.com/student','https://stackoverflow.com/users/123/student','https://unknown.example/article/42']}
JOB={'id':'synthetic-job','title':'AI Engineer','company':'Example AI','source':'seeded_demo','application_url':'https://example.invalid/apply','required_skills':['Python','NLP']}


def draft(facts=FACTS):return prepare_draft(scenario_form('graduate'),facts,JOB,[])


def test_arrays_become_typed_readable_cards_and_primary_education():
    value=draft();view=build_preview(value,FACTS,JOB)
    groups={section.id:section for section in view.sections}
    assert len(groups['education'].entries)==3 and groups['education'].entries[0].title=='Example Engineering University'
    assert len(groups['experience'].entries)==2 and len(groups['internships'].entries)==1
    assert groups['projects'].entries[0].title=='Career Helper'
    fields={f.id:f for f in value.fields}
    assert fields['institution'].value=='Example Engineering University' and fields['degree'].value=='B.Tech Computer Engineering'
    assert fields['graduation_year'].value=='2027' and not fields['institution'].approved
    assert next(field for field in groups['education'].fields if field.field_id=='institution').status=='user_confirmation_required'
    for field in value.fields:
        assert not isinstance(field.value,str) or not field.value.lstrip().startswith(('[{','{'))
    clean=view.model_dump_json()
    assert 'confirmed_profile.' not in clean and '"organization":' not in clean
    assert view.content_hash and value.presentation_version=='1.0'


@pytest.mark.parametrize('domain,platform',[('linkedin.com','linkedin'),('github.com','github'),('gitlab.com','gitlab'),('bitbucket.org','bitbucket'),('kaggle.com','kaggle'),('leetcode.com','leetcode'),('hackerrank.com','hackerrank'),('stackoverflow.com','stackoverflow'),('behance.net','behance'),('dribbble.com','dribbble'),('scholar.google.com','scholar'),('orcid.org','orcid'),('x.com','twitter'),('twitter.com','twitter')])
def test_platform_classification(domain,platform):
    links=classify_links({'links':[f'https://{domain}/student']})
    assert len(links)==1 and links[0].platform==platform


def test_link_normalization_context_multiple_and_additional_are_preserved():
    facts=deepcopy(FACTS);facts['links']+=['https://linkedin.com/in/second','https://linkedin.com.evil.example/in/spoof','https://unknown.example/']
    links=classify_links(facts)
    profiles=[link for link in links if link.platform=='linkedin']
    assert len(profiles)==2 and sum(link.primary for link in profiles)==1
    assert profiles[0].url=='https://www.linkedin.com/in/student' and 'utm_' in profiles[0].original_url
    assert any(link.kind=='repository' and link.url.endswith('/career-helper') for link in links)
    assert any(link.kind=='deployment' and 'vercel.app' in link.url for link in links)
    assert any(link.platform=='additional' and link.url=='https://unknown.example/' for link in links)
    assert not any(link.platform=='linkedin' and 'evil' in link.url for link in links)
    assert safe_url('https://example.com/path?a=keep&utm_source=test#section')=='https://example.com/path?a=keep#section'
    for url in ['github.com/student','javascript:alert(1)','https://user:pass@example.com','https://example.com/?token=private','https://example.com\\@evil.example']:
        assert safe_url(url) is None
    fields={f.id:f for f in map_fields(ApplicationForm(id='links',version='1',fields=[FormField(id='linkedin',label='LinkedIn'),FormField(id='github',label='GitHub'),FormField(id='portfolio',label='Portfolio')]),facts)}
    assert fields['linkedin'].value==profiles[0].url
    assert fields['github'].value=='https://github.com/student'
    assert fields['portfolio'].value=='https://portfolio.example.com'
    assert not valid_value(FormField(id='github',label='GitHub'), 'javascript:alert(1)')


def test_grounded_questions_have_variants_and_missing_legal_answers_stay_empty():
    for kind in ['relevant_experience','project_answer','education_answer','motivation','greatest_achievement']:
        answer=prepare_answer(kind,FACTS,JOB)
        assert answer.concise and len(answer.concise)<=600 and len(answer.detailed)<=2400
        assert answer.evidence and answer.source_paths and answer.requires_confirmation
        assert '{' not in answer.concise and '"organization"' not in answer.detailed
    assert 'Example Training' in prepare_answer('relevant_experience',FACTS,JOB).concise
    assert 'Career Helper' in prepare_answer('project_answer',FACTS,JOB).concise
    for kind in ['unknown_question','availability','work_authorization','salary','relocation']:
        assert not prepare_answer(kind,FACTS,JOB).concise
    facts={**FACTS,'achievements':['One achievement','Another achievement']}
    assert not prepare_answer('greatest_achievement',facts,JOB).concise


def test_ambiguous_education_and_missing_name_parts_are_explicit():
    facts={**FACTS,'education':[FACTS['education'][0],{**FACTS['education'][0],'institution':'Second University'}]}
    assert primary_education(facts)==(None,True)
    value=draft(facts);view=build_preview(value,facts,JOB)
    fields={f.field_id:f for section in view.sections for f in section.fields}
    assert fields['institution'].status=='conflict' and fields['first_name'].status=='missing'
    assert fields['work_permission'].status=='sensitive' and fields['work_permission'].display_value is None
    unsupported=prepare_draft(scenario_form('unsupported'),FACTS,JOB,[])
    assert any(f.status=='unsupported' for section in build_preview(unsupported,FACTS,JOB).sections for f in section.fields)


def test_real_stdio_preview_and_unavailable_fallback_share_the_same_schema(application,monkeypatch):
    settings=application.state.settings;value=draft()
    result=preview(settings,value,FACTS,JOB)
    assert result.engine=='local_mcp' and result==build_preview(value,FACTS,JOB)
    import app.integrations.application_mcp as mcp
    def fail(*args,**kwargs):raise MCPUnavailable('application_mcp_unavailable')
    monkeypatch.setattr(mcp,'call',fail)
    fallback=preview(settings,value,FACTS,JOB)
    assert fallback.engine=='local_fallback' and fallback.sections==result.sections


def test_legacy_draft_presentation_is_read_only_and_does_not_reuse_old_write_approval(client,application,profile):
    record=create(client,profile)
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id']);value=prepare_draft(scenario_form('graduate'),FACTS,row.job_snapshot,[])
        value.presentation_version='';value.fields[4].value=json.dumps(FACTS['education']);value.preview=None
        row.draft=value.model_dump();row.candidate_snapshot=FACTS;row.status='approved_for_prefill';row.approval={'digest':'old-approval'};db.commit()
        before=deepcopy(row.draft)
    response=client.get(f'{BASE}/{record["id"]}').json()
    assert response['draft']['preview']['engine']=='legacy_local'
    assert 'prefill' not in response['allowed_actions']
    assert 'confirmed_profile.' not in json.dumps(response['draft']['preview'])
    with application.state.session_factory() as db:assert db.get(ApplicationRecord,record['id']).draft==before


def test_preview_regeneration_preserves_review_and_never_prefills(client,profile):
    record=wait(client,act(client,create(client,profile),'prepare'))
    assert record['draft']['preview']['engine']=='local_mcp'
    record=review(client,record);record=act(client,record,'approve_prefill',confirmed=True)
    approved=record['approval']['digest']
    record=wait(client,act(client,record,'generate_preview'))
    assert record['status']=='approved_for_prefill' and record['approval']['digest']==approved
    assert not record['receipt']
    assert record['draft']['preview']['content_hash']


def test_tracker_search_sort_alias_filters_and_new_statuses(client,profile):
    record=wait(client,act(client,create(client,profile),'prepare'))
    assert client.get(BASE,params={'q':record['job']['company']}).json()['total']==1
    assert client.get(BASE,params={'q':'not-present-name','sort':'deadline'}).json()['total']==0
    assert client.get(BASE,params={'status':'user_review'}).json()['total']==1
    record=review(client,record);record=wait(client,act(client,act(client,record,'approve_prefill',confirmed=True),'prefill',confirmed=True))
    record=act(client,act(client,record,'approve_submission',confirmed=True),'record_submission',confirmed=True)
    record=act(client,record,'start_tracking')
    for status in ['recruiter_review','assessment','interview']:
        response=client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],'kind':'status','status':status,'text':'Explicit user update'})
        assert response.status_code==200,response.text
        record=response.json();assert record['status']==status
    audit=client.get(f'{BASE}/{record["id"]}/audit').json()
    assert {'recruiter_review','assessment','interview'}<={row['status'] for row in audit['status_history']}


def test_malformed_preview_and_form_use_a_safe_typed_fallback(application,monkeypatch):
    import app.integrations.application_mcp as mcp
    monkeypatch.setattr(mcp,'call',lambda *args,**kwargs:{'result':{'preview':{'invalid':True}}})
    assert mcp.preview(application.state.settings,draft(),FACTS,JOB).engine=='local_fallback'
    import app.flows.applications as flows
    monkeypatch.setattr(flows,'call',lambda *args,**kwargs:{'result':{'job_id':JOB['id'],'form':{'fields':[]}},'tools':[]})
    flow=flows.ApplicationFlow({'facts':FACTS,'job':JOB,'documents':[],'provider':'demo','use_ai':False},application.state.settings,None,1,time.monotonic()+30)
    result=flow.kickoff()
    assert result['draft']['connection']['form_source']=='local_fallback'
    assert result['draft']['preview']['sections']


@pytest.mark.parametrize('period,expected,year', [('2023–2027','','2027'),('2023–Present','',''),('','Expected May 2027','2027')])
def test_graduation_year_uses_only_explicit_dated_evidence(period,expected,year):
    facts={**FACTS,'education':[{'institution':'Example University','qualification':'B.Tech','period':period,'expected_graduation':expected}]}
    fields={f.id:f for f in draft(facts).fields}
    assert fields['graduation_year'].value==(year or None)


def test_missing_reasons_explain_boundaries_and_skill_groups_preserve_values():
    facts={**FACTS,'location':'India','links':[]}
    value=draft(facts);view=build_preview(value,facts,JOB)
    fields={f.field_id:f for section in view.sections for f in section.fields}
    assert 'name order' in fields['first_name'].reason and 'does not identify a city' in fields['city'].reason
    assert 'repositories' in fields['github'].reason
    skills=next(section for section in view.sections if section.id=='skills')
    assert {entry.title for entry in skills.entries}=={'Programming languages','Databases','AI / ML'}
    assert ', '.join(entry.details['Skills'] for entry in skills.entries)=='Python, SQL, NLP'


def test_country_or_city_location_is_not_invented_as_a_street_address():
    form=ApplicationForm(id='address',version='1',fields=[FormField(id='address',label='Address'),FormField(id='city',label='City')])
    fields={field.id:field for field in map_fields(form,{'location':'Bengaluru, India'})}
    assert fields['address'].value is None and fields['city'].value=='Bengaluru'
