"""Synthetic-only application evidence, genuine CrewAI and real stdio regressions."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import sys
import time
from unittest.mock import Mock
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select
from app.core.config import Settings
from app.db.models import ApplicationRecord,ApplicationEvent
from app.integrations.application_mcp import call,MCPUnavailable
from app.mcp.application_server import form_spec
from app.schemas.applications import ApplicationForm,FormField,TrackingInput,ApplicationAction
from app.services.application_preparation import map_fields,prepare_draft,validate_draft,valid_value
from app.services.match_normalization import fingerprint

BASE='/api/v1/applications'
HEADERS={'X-Workspace-Client':'careeros-web'}


def confirmed(client,profile):
    response=client.post('/api/v1/profile',json={**profile,'email':'candidate@example.com','phone':'+91 9000000000'})
    assert response.status_code==201,response.text
    assert client.post('/api/v1/profile/verify',json={'expected_revision':response.json()['revision'],'confirmed':True}).status_code==200


def select_job(client):
    jobs=client.get('/api/v1/jobs',params={'category':'backend','location':'Bengaluru','availability':'active','page_size':2}).json()['items']
    job=jobs[0]
    assert client.put(f'/api/v1/matching/bookmarks/{job["id"]}').status_code==200
    return job


def create(client,profile):
    confirmed(client,profile);job=select_job(client)
    response=client.post(BASE,json={'job_id':job['id']})
    assert response.status_code==201,response.text
    return response.json()


def act(client,record,action,**extra):
    response=client.post(f'{BASE}/{record["id"]}/actions',json={'action':action,'expected_revision':record['revision'],**extra})
    assert response.status_code==200,response.text
    return response.json()


def finished(client,record):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        record=client.get(f'{BASE}/{record["id"]}').json()
        if record['status'] not in {'preparing','prefilling'}:return record
        time.sleep(.05)
    raise AssertionError('Application did not finish')


def review(client,record):
    fields={f['id']:{'value':f['value'],'approved':f['value'] is not None} for f in record['draft']['fields'] if f['id'] not in {'resume_file','cover_letter_file'}}
    fields['work_permission']={'value':'Yes','approved':True}
    return act(client,record,'review',fields=fields)


@pytest.mark.parametrize('alias,expected',[('Candidate Name','name'),('Email Address','email'),('mobile','phone'),('Current City','location'),('academic_background','education'),('employment_history','experience'),('technical_skills','skills'),('project_summary','projects'),('Portfolio URL','links.0'),('career_objective','career_objective')])
def test_mapping_aliases_are_source_grounded(alias,expected,profile):
    facts={**profile,'phone':'5551234','links':['https://portfolio.example.com'],
        'experience':[{'organization':'Synthetic Lab','role':'Intern','description':'Built an API'}]}
    form=ApplicationForm(id='alternate',version='1',fields=[FormField(id='custom',label=alias,type='textarea')])
    field=map_fields(form,facts)[0]
    assert field.source==f'confirmed_profile.{expected}'
    assert field.value is not None and not field.approved


@pytest.mark.parametrize('label',['Gender','Nationality','Work Authorization','Salary Expectations','Religion','Date of Birth','Disability','Visa status','Race','Consent'])
def test_sensitive_fields_never_infer_even_with_deceptive_semantic(label,profile):
    field=FormField(id='custom',label=label,semantic='name')
    result=map_fields(ApplicationForm(id='sensitive',version='1',fields=[field]),profile)[0]
    assert result.value is None and not result.source and not result.approved


@pytest.mark.parametrize('type,value,options,valid',[
    ('checkbox',False,[],True),('checkbox','Yes',[],False),('radio','Yes',['Yes','No'],True),
    ('select','Unsupported',['Remote'],False),('multiselect',['Remote','Hybrid'],['Remote','Hybrid'],True),
    ('multiselect',['Remote','Remote'],['Remote'],False),('date','2027-02-30',[],False),
    ('date','2027-02-01',[],True),('email','invalid',[],False),('file','/etc/passwd',[],False),
])
def test_supported_field_types(type,value,options,valid):
    assert valid_value(FormField(id='x',label='X',type=type,options=options),value)==valid


def test_mapping_does_not_split_names_or_choose_unsupported_options(profile):
    form=ApplicationForm(id='f',version='1',fields=[FormField(id='first_name',label='First name'),
        FormField(id='choice',label='Skills',type='select',options=['Invented skill'])])
    fields=map_fields(form,profile)
    assert all(f.value is None for f in fields)


def test_conflicting_form_semantics_require_manual_input(profile):
    form=ApplicationForm(id='ambiguous',version='1',fields=[FormField(id='email',label='Full name',semantic='email')])
    result=map_fields(form,profile)[0]
    assert result.value is None and result.classification=='unavailable' and not result.approved


def test_no_confirmed_profile_and_unsaved_job_are_rejected(client,profile):
    assert client.post(BASE,json={'job_id':'demo-any'}).status_code==409
    confirmed(client,profile)
    assert client.post(BASE,json={'job_id':'demo-any'}).status_code==404
    assert client.post(BASE,json={'job_id':'demo-any','use_ai':True}).status_code==422


def test_complete_real_stdio_application_lifecycle_and_profile_immutability(client,application,profile):
    record=create(client,profile);before=client.get('/api/v1/profile').json()
    record=finished(client,act(client,record,'prepare'))
    assert record['status']=='needs_user_review',record
    assert len(record['draft']['fields'])==17
    assert len(record['tasks'])==7 and all(t['status']=='completed' for t in record['tasks'])
    assert record['draft']['ai_review']['status']=='not_selected'
    assert not record['draft']['documents']
    assert client.post(f'{BASE}/{record["id"]}/actions',json={'action':'approve_prefill','expected_revision':record['revision'],'confirmed':True}).status_code==409
    # Saving a partial review is valid; missing required inputs remain visible.
    record=act(client,record,'review',fields={})
    assert 'work_permission' in record['draft']['missing_inputs']
    record=review(client,record)
    assert record['draft']['validation']['ready_for_prefill']
    record=act(client,record,'approve_prefill',confirmed=True)
    assert record['approval']['scope']=='prefill only; no submission'
    record=finished(client,act(client,record,'prefill',confirmed=True))
    assert record['status']=='prefill_complete',record
    assert record['receipt']['submitted'] is False
    assert record['receipt']['applied_field_ids'] and not record['receipt']['attached_documents']
    record=act(client,record,'approve_submission',confirmed=True)
    assert record['status']=='needs_submission_approval'
    record=act(client,record,'record_submission',confirmed=True,reference='synthetic-demo')
    assert record['receipt']['submission']['source']=='user_reported' and record['receipt']['submission']['is_demo']
    record=act(client,record,'start_tracking')
    response=client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],'kind':'interview','text':'Technical round','due_at':'2027-01-15T10:00:00+05:30'})
    assert response.status_code==200;record=response.json();assert record['status']=='interview'
    for status in ['offer','accepted','closed']:
        response=client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],'kind':'status','status':status})
        assert response.status_code==200,response.text;record=response.json()
    assert client.get('/api/v1/profile').json()==before
    timeline=client.get(f'{BASE}/{record["id"]}/timeline').json()
    assert [e['seq'] for e in timeline]==list(range(1,len(timeline)+1))
    assert any(e['payload'].get('text')=='Technical round' for e in timeline)
    replay=client.get(f'{BASE}/{record["id"]}/events?follow=false&after=2')
    assert replay.status_code==200 and 'text/event-stream' in replay.headers['content-type']
    wires=[json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith('data: ')]
    assert wires[0]['seq']==3 and wires[-1]['seq']==timeline[-1]['seq']
    assert 'candidate@example.com' not in replay.text
    summary=client.get('/api/v1/dashboard/summary').json()
    assert summary['application_status_summary']['closed']==1


def test_revision_duplicate_owner_and_forbidden_tools(client,application,profile):
    record=create(client,profile)
    assert client.post(BASE,json={'job_id':record['job_id']}).status_code==409
    assert client.post(f'{BASE}/{record["id"]}/actions',json={'action':'record_submission','confirmed':True,'expected_revision':record['revision']}).status_code==409
    assert client.post(f'{BASE}/{record["id"]}/mcp/call',json={'name':'submit_application','expected_revision':record['revision']}).status_code==422
    assert client.post(f'{BASE}/{record["id"]}/actions',json={'action':'prepare','expected_revision':999}).status_code==409
    with TestClient(application,headers=HEADERS) as other:
        other.post('/api/v1/session')
        for suffix in ['', '/timeline','/surface','/mcp/resource','/mcp/tools','/events?follow=false']:
            assert other.get(f'{BASE}/{record["id"]}{suffix}').status_code==404
        assert other.post(f'{BASE}/{record["id"]}/actions',json={'action':'prepare','expected_revision':record['revision']}).status_code==404


def test_edits_revoke_approval_and_stale_profile_cannot_prefill(client,profile):
    record=finished(client,act(client,create(client,profile),'prepare'));record=review(client,record)
    record=act(client,record,'approve_prefill',confirmed=True)
    record=act(client,record,'review',fields={'contact_email':{'value':'updated@example.com','approved':True}})
    assert record['status']=='needs_user_review' and not record['approval']
    assert client.post(f'{BASE}/{record["id"]}/actions',json={'action':'prefill','expected_revision':record['revision'],'confirmed':True}).status_code==409
    current=client.get('/api/v1/profile').json()
    updated={k:current[k] for k in profile};updated['skills']=['New skill']
    assert client.put('/api/v1/profile',json={**updated,'expected_revision':current['revision']}).status_code==200
    stale=client.get(f'{BASE}/{record["id"]}').json()
    assert stale['stale'] and 'approve_prefill' not in stale['allowed_actions']


def test_no_blanket_approval_or_unconfirmed_resume_or_invalid_fields(client,profile):
    record=finished(client,act(client,create(client,profile),'prepare'))
    for fields in [{'unknown':{'value':'test','approved':True}}, {'work_permission':{'value':'Invented','approved':True}}, {'resume_file':{'value':'/etc/passwd','approved':True}}]:
        assert client.post(f'{BASE}/{record["id"]}/actions',json={'action':'review','expected_revision':record['revision'],'fields':fields}).status_code==422
    uploaded=client.post('/api/v1/documents/upload',files={'file':('resume.txt',b'Unconfirmed resume','text/plain')}).json()
    result=client.post(f'{BASE}/{record["id"]}/actions',json={'action':'review','expected_revision':record['revision'],'documents':[{'document_id':uploaded['id'],'role':'resume','approved':True}]})
    assert result.status_code==409


def test_cancelled_preparation_does_not_publish_and_retry_is_explicit(client,application,profile,monkeypatch):
    processor=application.state.application_processor
    monkeypatch.setattr(processor,'submit',lambda id,operation:processor.capacity.release())
    record=act(client,create(client,profile),'prepare')
    record=act(client,record,'cancel')
    assert record['status']=='cancelled' and not record['approval']
    with application.state.session_factory() as db:
        assert db.get(ApplicationRecord,record['id']).active_operation is None
    record=act(client,record,'retry')
    assert record['status']=='preparing'
    act(client,record,'cancel')


def test_adapter_failure_is_honest_and_recoverable(client,application,profile):
    record=create(client,profile)
    application.state.settings.application_mcp_command=[sys.executable,'-c','raise SystemExit(1)']
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id']);row.provider='configured';db.commit()
    record=finished(client,act(client,record,'prepare'))
    assert record['status']=='failed' and not record['receipt']
    assert 'retry' in record['allowed_actions']


def test_real_mcp_discovery_resource_and_no_submission_capability():
    settings=Settings(_env_file=None)
    discovered=call(settings,'demo','discover')
    names={t['name'] for t in discovered['tools']}
    assert names=={'inspect_application','prefill_application','application_status','open_application','build_application_preview'}
    inspect=next(t for t in discovered['tools'] if t['name']=='inspect_application')
    assert inspect['_meta']['ui']['resourceUri']=='ui://ascendra/application'
    resource=call(settings,'demo','resource')
    assert resource['mime_type']=='text/html;profile=mcp-app'
    assert resource['html'].lower().startswith('<!doctype html')
    assert '<script type="module">' in resource['html']
    with pytest.raises(MCPUnavailable):call(settings,'demo','submit_application')


def test_mcp_timeout_and_missing_sdk_or_command():
    settings=Settings(_env_file=None,application_mcp_timeout_seconds=1,
        application_mcp_command=[sys.executable,'-c','import time; time.sleep(10)'])
    began=time.monotonic()
    with pytest.raises(MCPUnavailable):call(settings,'configured','discover')
    assert time.monotonic()-began<9
    with pytest.raises(MCPUnavailable):call(Settings(_env_file=None),'configured','discover')


def test_actual_six_crewai_agents_only_review_redacted_catalog(application,profile,tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    from app.crews.applications import review_application
    settings=application.state.settings
    draft=prepare_draft(form_spec(),{**profile,'email':'private@example.com'}, {'required_skills':['Python'],'id':'demo-synthetic'},[])
    messages=[]
    class Transport:
        def __init__(self):self.settings=settings
        def complete(self,messages_,schema,**kwargs):
            messages.extend(messages_)
            return json.dumps({'flagged_field_ids':[],'recommended_document_ids':[],'issues':[]})
    manager=application.state.processor.key_manager
    result=review_application(draft,[{'name':'inspect_application'}],settings,manager,42,time.monotonic()+30,transport=Transport())
    assert result['status']=='completed' and len(result['tasks'])==6
    assert all(t['status']=='completed' for t in result['tasks'])
    exported=json.dumps(messages)
    assert 'private@example.com' not in exported and profile['name'] not in exported
    assert not draft.documents and all(not f.approved for f in draft.fields)


def test_rejects_unsupported_agent_references(application,profile,tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    from app.crews.applications import review_application
    from app.providers.groq import ProviderUnavailable
    draft=prepare_draft(form_spec(),profile,{'id':'demo-synthetic','required_skills':[]},[])
    class Transport:
        settings=application.state.settings
        def complete(self,*args,**kwargs):return json.dumps({'flagged_field_ids':['made_up'],'recommended_document_ids':[],'issues':[]})
    with pytest.raises(ProviderUnavailable):review_application(draft,[],application.state.settings,application.state.processor.key_manager,42,time.monotonic()+30,transport=Transport())


def test_tracking_validation_and_no_status_leap(client,profile):
    record=create(client,profile)
    for payload in [dict(kind='status',status='accepted'),dict(kind='interview',due_at='2027-01-01T00:00:00Z')]:
        assert client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],**payload}).status_code==409
    for payload in [dict(kind='note'),dict(kind='deadline'),dict(kind='interview',due_at='2027-01-01T00:00:00')]:
        assert client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],**payload}).status_code==422
    for kind in ['note','deadline','follow_up']:
        result=client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],'kind':kind,'text':'Prepare for review',**({'due_at':'2027-02-01T12:00:00Z'} if kind!='note' else {})})
        assert result.status_code==200;record=result.json()


def test_pagination_and_independent_source_schema_validation(client,profile):
    record=create(client,profile)
    assert client.get(f'{BASE}?page_size=0').status_code==422
    assert client.get(f'{BASE}?page=2').json()['items']==[]
    assert client.get(f'{BASE}?status=closed').json()['total']==0
    assert client.get(BASE).json()['total']==1
    with pytest.raises(ValueError):ApplicationForm(id='x',version='1',fields=[FormField(id='same',label='A'),FormField(id='same',label='B')])


def test_confirmed_document_attachment_is_bound_to_job_and_hash_and_rechecked(client,application,profile):
    from test_document_processing import upload,finish,verify_payload,BASE as DOCS
    from app.db.models import Document
    record=create(client,profile)
    application.state.settings.max_document_bytes=100000
    doc=upload(client)
    assert finish(client,doc)['status']=='awaiting_review'
    data=client.get(f'{DOCS}/{doc}/extracted-data').json()
    assert client.post(f'{DOCS}/{doc}/verify',json=verify_payload(data,fields=['skills'])).status_code==200
    record=finished(client,act(client,record,'prepare'));record=review(client,record)
    option=next(d for d in record['draft']['document_options'] if d['id']==doc)
    assert option['confirmed_resume'] and not record['draft']['documents']
    record=act(client,record,'review',documents=[{'document_id':doc,'role':'resume','approved':True}])
    selected=record['draft']['documents'][0]
    assert selected['job_id']==record['job_id'] and selected['sha256']==option['sha256']
    record=act(client,record,'approve_prefill',confirmed=True)
    record=finished(client,act(client,record,'prefill',confirmed=True))
    assert record['status']=='prefill_complete' and record['receipt']['attached_documents']==[{'document_id':doc,'sha256':option['sha256']}]
    # A completed prefill is immutable. A fresh preparation requires new approval.
    record=finished(client,act(client,record,'prepare'))
    record=review(client,record)
    record=act(client,record,'review',documents=[{'document_id':doc,'role':'resume','approved':True}])
    record=act(client,record,'approve_prefill',confirmed=True)
    assert client.delete(f'{DOCS}/{doc}').status_code==204
    result=client.post(f'{BASE}/{record["id"]}/actions',json={'action':'prefill','expected_revision':record['revision'],'confirmed':True})
    assert result.status_code==409


def test_queue_cancellation_cannot_publish_late_results(client,application,profile,monkeypatch):
    record=create(client,profile)
    processor=application.state.application_processor
    monkeypatch.setattr(processor,'submit',lambda id,operation:processor.capacity.release())
    record=act(client,record,'prepare')
    with application.state.session_factory() as db:operation=db.get(ApplicationRecord,record['id']).active_operation
    record=act(client,record,'cancel')
    # Even a worker scheduled late must not restore its cancelled operation.
    processor.capacity.acquire();processor._run(record['id'],operation)
    assert client.get(f'{BASE}/{record["id"]}').json()['status']=='cancelled'


def test_provider_cannot_claim_submission_in_a_prefill_receipt(client,application,profile,monkeypatch):
    from app.services import applications as service
    record=create(client,profile)
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id'])
        draft=prepare_draft(form_spec(),row.candidate_snapshot,row.job_snapshot,[])
        for field in draft.fields:
            if field.id=='work_permission':field.value='Yes';field.source='user_input'
            if field.value is not None:field.approved=True
        row.draft=draft.model_dump();row.status='approved_for_prefill';row.stage=row.status
        row.approval={'digest':service.approval_hash(row),'approved_revision':row.revision};db.commit()
    record=client.get(f'{BASE}/{record["id"]}').json()
    processor=application.state.application_processor
    monkeypatch.setattr(processor,'submit',lambda id,operation:processor.capacity.release())
    record=act(client,record,'prefill',confirmed=True)
    def claimed_submission(payload,*args):
        packet=payload['packet']
        return {'result':{'accepted':True,'submitted':True,'is_demo':True,'job_id':record['job_id'],
            'operation_id':packet['operation_id'],'packet_digest':fingerprint(packet),
            'applied_field_ids':sorted(packet['values']),'attached_documents':[]}}
    monkeypatch.setattr(service,'execute_application',claimed_submission)
    with application.state.session_factory() as db:operation=db.get(ApplicationRecord,record['id']).active_operation
    processor.capacity.acquire();processor._run(record['id'],operation)
    result=client.get(f'{BASE}/{record["id"]}').json()
    assert result['status']=='failed' and not result['receipt'] and not result['approval']


def test_optional_ai_failure_retains_source_grounded_draft(client,profile):
    confirmed(client,profile);job=select_job(client)
    record=client.post(BASE,json={'job_id':job['id'],'use_ai':True,'allow_external_processing':True}).json()
    record=finished(client,act(client,record,'prepare'))
    assert record['status']=='needs_user_review'
    assert record['draft']['ai_review']['status']=='unavailable'
    assert record['draft']['fields'] and all(not f['approved'] for f in record['draft']['fields'])


def test_recovery_is_additive_and_does_not_clear_history(client,application,profile,monkeypatch):
    record=create(client,profile);processor=application.state.application_processor
    monkeypatch.setattr(processor,'submit',lambda id,operation:processor.capacity.release())
    record=act(client,record,'prepare')
    processor.recover_interrupted()
    recovered=client.get(f'{BASE}/{record["id"]}').json()
    assert recovered['status']=='failed' and recovered['error_code']=='application_interrupted'
    events=client.get(f'{BASE}/{record["id"]}/timeline').json()
    assert events[0]['payload']['status']=='job_selected' and events[-1]['type']=='RUN_ERROR'


def test_operator_process_receives_no_backend_secrets(tmp_path):
    # The server command is operator configuration; this synthetic server reports
    # only whether credential variables exist, never their contents.
    script=tmp_path/'inspect_env.py'
    script.write_text('''import os\nfrom mcp.server.fastmcp import FastMCP\nm=FastMCP("env-check")\n@m.tool()\ndef inspect_application(job_id:str)->dict:\n return {"secret_present":any(k.startswith("GROQ_") or k in {"SESSION_SECRET","DATABASE_URL"} for k in os.environ)}\nm.run(transport="stdio")\n''')
    settings=Settings(_env_file=None,application_mcp_command=[sys.executable,str(script)])
    result=call(settings,'configured','inspect_application',{'job_id':'synthetic'})
    assert result['result']['secret_present'] is False
