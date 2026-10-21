"""Continuation regressions using only synthetic facts and real stdio tools."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import time
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.db.models import Document,VerificationRecord,ApplicationRecord,MCPAction
from app.integrations.application_mcp import call,MCPUnavailable
from app.mcp.application_server import scenario_form,digest,journal
from app.schemas.applications import ApplicationForm
from app.services.application_preparation import map_fields
from test_applications import BASE,create,act,review,confirmed,select_job


def wait(client,record):
    end=time.monotonic()+45
    while time.monotonic()<end:
        record=client.get(f'{BASE}/{record["id"]}').json()
        if not record['operation_active']:return record
        time.sleep(.05)
    raise AssertionError('Application operation timed out')


def scenario(client,profile,name):
    confirmed(client,profile);job=select_job(client)
    response=client.post(BASE,json={'job_id':job['id'],'demo_scenario':name})
    assert response.status_code==201,response.text
    return wait(client,act(client,response.json(),'prepare'))


def approved(client,record):
    return act(client,review(client,record),'approve_prefill',confirmed=True)


def test_extended_mapping_distinguishes_links_and_requires_source_confirmation(profile):
    facts={**profile,'location':'Bengaluru, India','links':['https://linkedin.com/in/example','https://github.com/example','https://portfolio.example'],
        'education':[{'institution':'Example University','qualification':'B.Tech','end_year':'2027'}],
        'experience':[{'role':'Software Intern','organization':'Example Lab','description':'Built APIs'}],
        'certifications':[{'name':'Example certificate'}]}
    fields={f.id:f for f in map_fields(ApplicationForm.model_validate(scenario_form('graduate')),facts)}
    assert fields['linkedin'].value=='https://linkedin.com/in/example'
    assert fields['github'].value=='https://github.com/example'
    assert fields['portfolio_url'].value=='https://portfolio.example'
    assert fields['city'].value=='Bengaluru' and fields['country'].value=='India'
    assert fields['institution'].value=='Example University'
    assert fields['relevant_experience'].suggested_answer and fields['relevant_experience'].confirmation_required
    assert facts['career_objective'] in fields['motivation'].value and fields['motivation'].suggested_answer
    assert all(fields[key].value is None for key in ['first_name','middle_name','last_name','notice_period','demographics','work_permission'])
    assert not any(field.approved for field in fields.values())


def test_partial_prefill_retry_preserves_completed_fields_and_no_submission(client,application,profile):
    record=approved(client,scenario(client,profile,'partial'))
    record=wait(client,act(client,record,'prefill',confirmed=True))
    assert record['status']=='partially_completed',record
    first=deepcopy(record['receipt']);assert len(first['skipped_fields'])==1 and not first['submitted']
    assert 'review' not in record['allowed_actions'] and 'retry_prefill' in record['allowed_actions']
    assert not record['milestones'].get('prefill_completed_at')
    record=wait(client,act(client,record,'retry_prefill',confirmed=True))
    assert record['status']=='prefill_complete' and not record['receipt']['submitted']
    with application.state.session_factory() as db:
        attempts=db.scalars(select(MCPAction).where(MCPAction.application_id==record['id'],MCPAction.tool=='prefill_application').order_by(MCPAction.created_at)).all()
        assert len(attempts)==2
        assert attempts[1].request_evidence==attempts[0].request_evidence
    audit=client.get(f'{BASE}/{record["id"]}/audit').json()
    assert len([a for a in audit['actions'] if a['tool']=='prefill_application'])==2 and audit['approvals'][0]['kind']=='prefill'
    assert record['milestones']['prepared_at'] and record['milestones']['prefill_completed_at']
    assert 'submitted_by_user_at' not in record['milestones']
    events=client.get(f'{BASE}/{record["id"]}/timeline').json()
    assert {'FIELD_PREFILL_PROGRESS','RETRY','USER_APPROVAL_REQUEST'}<={e['type'] for e in events}


@pytest.mark.parametrize('name,state',[('authentication','waiting_for_user'),('unavailable','external_site_unavailable'),('unsupported','unsupported_form')])
def test_blocked_form_local_fallback_and_explicit_reconnect(client,profile,name,state):
    record=scenario(client,profile,name)
    assert record['status']==state and record['draft']
    assert 'approve_prefill' not in record['allowed_actions']
    if name=='unsupported':
        field=next(f for f in record['draft']['form']['fields'] if f['id']=='custom_challenge')
        assert field['type']=='unsupported' and field['original_type']=='captcha'
        record=act(client,record,'continue_manually',confirmed=True)
        assert record['status']=='needs_submission_approval' and not record['receipt']
    else:
        failed=client.post(f'{BASE}/{record["id"]}/actions',json={'action':'resume','expected_revision':record['revision']})
        assert failed.status_code==422
        record=wait(client,act(client,record,'resume',confirmed=True))
        assert record['status']=='needs_user_review' and record['draft']['connection']['status']=='connected'


def test_opened_prefilled_submitted_tracked_and_typed_schedule_are_distinct(client,profile):
    record=wait(client,act(client,create(client,profile),'prepare'))
    record=wait(client,act(client,record,'open_external',confirmed=True))
    assert record['milestones']['external_form_opened_at'] and not record['receipt']
    assert 'prefill_completed_at' not in record['milestones']
    record=wait(client,act(client,approved(client,record),'prefill',confirmed=True))
    record=act(client,record,'approve_submission',confirmed=True)
    assert 'submitted_by_user_at' not in record['milestones']
    record=act(client,record,'record_submission',confirmed=True)
    assert record['milestones']['submitted_by_user_at'] and 'tracking_started_at' not in record['milestones']
    record=act(client,record,'start_tracking')
    due='2027-01-15T10:00:00+00:00'
    for kind in ['note','interview','deadline','follow_up']:
        response=client.post(f'{BASE}/{record["id"]}/tracking',json={'expected_revision':record['revision'],'kind':kind,'text':'Synthetic stage','due_at':due} if kind!='note' else {'expected_revision':record['revision'],'kind':kind,'text':'Synthetic note'})
        assert response.status_code==200,response.text
        record=response.json()
    assert len(record['tracking']['tasks'])==2 and len(record['tracking']['interviews'])==1
    tasks=client.get(f'{BASE}/schedule').json();assert len(tasks)==2
    completed=client.post(f'{BASE}/{record["id"]}/tasks/{tasks[0]["id"]}/complete',json={'expected_revision':record['revision']})
    assert completed.status_code==200 and completed.json()['tracking']['tasks'][0]['completed']
    audit=client.get(f'{BASE}/{record["id"]}/audit').json()
    assert audit['external_session']['status']=='opened' and audit['status_history']
    assert {'prefill','submission_review','submission_report'}<={a['kind'] for a in audit['approvals']}


def test_schema_change_fails_closed_and_requires_new_review(client,profile):
    record=approved(client,scenario(client,profile,'schema_change'))
    record=wait(client,act(client,record,'prefill',confirmed=True))
    assert record['status']=='unsupported_form' and record['error_code']=='form_schema_changed'
    assert not record['receipt'] and not record['approval']
    assert not record['recovery']['reconcile_required']
    record=wait(client,act(client,record,'prepare'))
    assert record['draft']['form']['version']=='2.0'
    assert not any(f['approved'] for f in record['draft']['fields'])
    record=wait(client,act(client,approved(client,record),'prefill',confirmed=True))
    assert record['status']=='prefill_complete'


def test_interrupted_operation_reconciliation_uses_hashes_without_repeating_writes(application,tmp_path,monkeypatch):
    monkeypatch.setenv('ASCENDRA_MCP_STATE_DIR',str(tmp_path/'application-mcp'))
    id=str(uuid4());operation=str(uuid4());values={'name':'Synthetic Example','email':'synthetic@example.com'}
    expected={'packet_digest':'a'*64,'job_id':'synthetic-job','fields':{key:digest(value) for key,value in values.items()},'documents':{}}
    journal(id,lambda state:state.update(operations={operation:expected},fields={'name':digest(values['name'])}))
    settings=application.state.settings.model_copy(update={'data_dir':tmp_path})
    result=call(settings,'demo','application_status',{'application_id':id,'operation_id':operation})['result']
    assert result['outcome_known'] and result['receipt']['applied_field_ids']==['name']
    assert result['receipt']['skipped_fields']==[{'field_id':'email','code':'interrupted','retryable':True}]
    assert not result['receipt']['submitted']
    raw=(tmp_path/'application-mcp'/'journal.db').read_bytes()
    assert all(value.encode() not in raw for value in values.values())


def test_retry_packet_excludes_completed_upload_bytes_and_fields(client,application,profile,monkeypatch):
    """Inspect host packet after a known partial receipt without another export."""
    from app.services import applications as service
    record=approved(client,scenario(client,profile,'partial'))
    record=wait(client,act(client,record,'prefill',confirmed=True))
    old=set(record['receipt']['applied_field_ids']);captured={}
    real=service.execute_application
    def capture(payload,*args):
        captured.update(payload['packet']);return real(payload,*args)
    monkeypatch.setattr(service,'execute_application',capture)
    record=wait(client,act(client,record,'retry_prefill',confirmed=True))
    assert set(captured['values']).isdisjoint(old)
    assert {item['field_id'] for item in captured['completed_fields']}==old
    assert record['status']=='prefill_complete'

@pytest.mark.parametrize('name',['partial','upload_failure'])
def test_document_retries_send_only_unfinished_uploads(client,application,profile,monkeypatch,name):
    from app.services import applications as service
    from test_document_processing import upload,finish,verify_payload,BASE as DOCS
    confirmed(client,profile)
    application.state.settings.max_document_bytes=100000
    doc=upload(client)
    assert finish(client,doc)['status']=='awaiting_review'
    extraction=client.get(f'{DOCS}/{doc}/extracted-data').json()
    assert client.post(f'{DOCS}/{doc}/verify',json=verify_payload(extraction,fields=['skills'])).status_code==200
    job=select_job(client)
    record=client.post(BASE,json={'job_id':job['id'],'demo_scenario':name}).json()
    record=review(client,wait(client,act(client,record,'prepare')))
    record=act(client,record,'review',documents=[{'document_id':doc,'role':'resume','approved':True}])
    record=act(client,record,'approve_prefill',confirmed=True)
    record=wait(client,act(client,record,'prefill',confirmed=True))
    assert record['status']=='partially_completed'
    first=deepcopy(record['receipt']);captured={};real=service.execute_application
    def capture(payload,*args):captured.update(payload['packet']);return real(payload,*args)
    monkeypatch.setattr(service,'execute_application',capture)
    record=wait(client,act(client,record,'retry_prefill',confirmed=True))
    assert record['status']=='prefill_complete' and len(record['receipt']['attached_documents'])==1
    if name=='partial':
        assert first['attached_documents'] and not captured['documents']
        assert captured['completed_documents']==first['attached_documents']
        assert 'content_base64' not in json.dumps(captured['completed_documents'])
    else:
        assert first['failed_documents'] and len(captured['documents'])==1
        assert not captured['values'] and captured['completed_fields']


def test_pause_before_worker_start_reconciles_without_claiming_a_prefill(client,application,profile,monkeypatch):
    record=approved(client,wait(client,act(client,create(client,profile),'prepare')))
    processor=application.state.application_processor;submit=processor.submit
    monkeypatch.setattr(processor,'submit',lambda id,op:processor.capacity.release())
    record=act(client,record,'prefill',confirmed=True)
    record=act(client,record,'pause',confirmed=True)
    assert record['status']=='waiting_for_user' and record['recovery']['reconcile_required']
    assert 'retry_prefill' not in record['allowed_actions']
    assert 'prepare' not in record['allowed_actions']
    monkeypatch.setattr(processor,'submit',submit)
    record=wait(client,act(client,record,'reconcile',confirmed=True))
    assert record['status']=='needs_user_review' and not record['recovery']['reconcile_required']
    assert not record['receipt'] and not record['approval']


def test_readonly_retry_is_bounded_and_writes_are_never_automatically_retried(application,monkeypatch):
    from app.integrations import application_mcp as mcp
    attempts=[]
    async def unavailable(*args):attempts.append(args[2]);raise MCPUnavailable('application_mcp_unavailable',retryable=True)
    monkeypatch.setattr(mcp,'_session',unavailable)
    with pytest.raises(MCPUnavailable):mcp.call(application.state.settings,'demo','inspect_application')
    assert attempts==['inspect_application','inspect_application']
    attempts.clear()
    with pytest.raises(MCPUnavailable):mcp.call(application.state.settings,'demo','prefill_application')
    assert attempts==['prefill_application']


def test_credential_controls_are_unsupported_even_when_a_provider_calls_them_text(profile):
    for label in ['Login password','API key','Session token','One-time code']:
        form=ApplicationForm.model_validate({'id':'untrusted','version':'1','fields':[{'id':'secret','label':label,'type':'text','semantic':'name','required':True}]})
        assert form.fields[0].type=='unsupported'
        field=map_fields(form,profile)[0]
        assert field.value is None and not field.source and not field.auto_filled


def test_manual_tracking_remains_available_after_profile_change_and_permanent_partial(client,application,profile):
    from app.services.application_preparation import prepare_draft
    from app.services import applications as service
    from app.mcp.application_server import form_spec
    record=create(client,profile)
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id']);row.draft=prepare_draft(form_spec(),row.candidate_snapshot,row.job_snapshot,[]).model_dump()
        row.status='partially_completed';row.receipt={'applied_field_ids':['applicant_name'],'skipped_fields':[{'field_id':'contact_email','code':'manual_step_required','retryable':False}]}
        row.approval={'digest':service.approval_hash(row)};db.commit()
    record=client.get(f'{BASE}/{record["id"]}').json()
    assert 'retry_prefill' not in record['allowed_actions'] and 'continue_manually' in record['allowed_actions']
    current=client.get('/api/v1/profile').json()
    assert client.put('/api/v1/profile',json={**profile,'name':'Updated Example','expected_revision':current['revision']}).status_code==200
    record=client.get(f'{BASE}/{record["id"]}').json();assert record['stale']
    record=act(client,record,'continue_manually',confirmed=True)
    record=act(client,record,'record_submission',confirmed=True)
    assert record['status']=='submitted_by_user' and record['stale']


def test_session_receipt_discards_unrecognized_provider_properties(client,application,profile,monkeypatch):
    from app.services.application_preparation import prepare_draft
    from app.services import applications as service
    from app.mcp.application_server import form_spec
    record=create(client,profile)
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id']);row.draft=prepare_draft(form_spec(),row.candidate_snapshot,row.job_snapshot,[]).model_dump();row.status='needs_user_review';db.commit()
    processor=application.state.application_processor
    monkeypatch.setattr(processor,'submit',lambda id,op:processor.capacity.release())
    monkeypatch.setattr(service,'execute_application',lambda *args:{'result':{'opened':True,'is_demo':True,'job_id':record['job_id'],'application_id':record['id'],'external_url':'ui://ascendra/application','provider_cookie':'SYNTHETIC_PRIVATE_MARKER'}})
    record=client.get(f'{BASE}/{record["id"]}').json();record=act(client,record,'open_external',confirmed=True)
    with application.state.session_factory() as db:operation=db.get(ApplicationRecord,record['id']).active_operation
    processor.capacity.acquire();processor._run(record['id'],operation)
    audit=client.get(f'{BASE}/{record["id"]}/audit')
    assert audit.status_code==200 and 'SYNTHETIC_PRIVATE_MARKER' not in audit.text
    assert audit.json()['actions'][0]['status']=='completed'


def test_local_fallback_review_retains_recovery_controls(client,application,profile):
    from app.services.application_preparation import prepare_draft
    from app.mcp.application_server import form_spec
    record=create(client,profile)
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,record['id']);draft=prepare_draft(form_spec(),row.candidate_snapshot,row.job_snapshot,[])
        draft.connection={'status':'unavailable','form_source':'local_fallback','error_code':'application_mcp_unavailable'}
        row.draft=draft.model_dump();row.status='failed';db.commit()
    record=client.get(f'{BASE}/{record["id"]}').json();record=review(client,record)
    assert record['status']=='failed' and 'continue_manually' in record['allowed_actions']
    assert 'approve_prefill' not in record['allowed_actions']
