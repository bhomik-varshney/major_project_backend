"""Completed, source-linked extraction must survive a failing AI quality task."""
import json
import time
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.orchestration.resume import enhance,run_crew
from app.providers.groq import GroqKeyManager,GroqProvider,ProviderUnavailable
from app.schemas.agents import CompactCrewResult,ResumeClaims,ResumeCrewInput,CrewTaskFailure,CompactQualityReport
from app.services.groq_extraction import EnhancementEvidence
from test_document_processing import upload,finish,BASE
from test_resume_crew import crew_runtime


def claims():
    return ResumeClaims(claims=[{'path':'skills.0','value':'Python','block_id':2,'confidence':'high'}],warnings=[])


def checkpoint_then_failure(prepared,settings,manager,owner,deadline,scratch,output):
    output.put(('checkpoint',claims().model_dump()))
    output.put(('stage','crew_validation'))
    output.put(('error',{'code':'groq_invalid_output','message':'Quality output was invalid.'}))


def checkpoint_then_timeout(prepared,settings,manager,owner,deadline,scratch,output):
    output.put(('checkpoint',claims().model_dump()))
    time.sleep(30)


@pytest.mark.parametrize('target,code',[(checkpoint_then_failure,'groq_invalid_output'),(checkpoint_then_timeout,'crew_timeout')])
def test_completed_claims_survive_quality_failure_and_hard_timeout(tmp_path,target,code):
    settings=Settings(_env_file=None,document_storage_path=tmp_path,crew_timeout_seconds=2)
    manager=GroqKeyManager(['synthetic'])
    result=run_crew(ResumeCrewInput(file_type='txt',blocks=[],compact_output=True),settings,manager,
        result_model=CompactCrewResult,child_target=target)
    assert result.extraction==claims() and result.quality is None
    assert result.quality_error.code==code
    assert not any(manager.owners) and not list(tmp_path.iterdir())


def test_quality_failure_keeps_source_checks_and_records_failed_stage(tmp_path):
    result=CompactCrewResult(extraction=claims(),quality_error=CrewTaskFailure(code='groq_output_truncated',message='Incomplete quality response.'))
    blocks=[{'block_id':1,'text':'Skills:'},{'block_id':2,'text':'Python'}]
    with patch('app.orchestration.resume.run_crew',return_value=result) as run:
        values,evidence,withheld=enhance(blocks,Settings(_env_file=None,document_storage_path=tmp_path))
    assert run.call_count==1 and values['skills']==['Python'] and withheld==0
    assert evidence['skills.0']['source']['quote']=='Python'
    assert evidence.agent_statuses=={'extraction':'completed','validation':'failed'}
    assert evidence.agent_results['validation']['failures'][0]['code']=='groq_output_truncated'


def test_partial_success_is_visible_in_api_without_saving_profile(client):
    doc=upload(client)
    evidence=EnhancementEvidence({},['AI quality review failed; completed extraction retained.'])
    evidence.agent_results={'extraction':{'completed_batches':1},'validation':{'failures':[{'code':'groq_invalid_output','message':'Invalid quality output.'}]}}
    evidence.agent_statuses={'extraction':'completed','validation':'failed'}
    evidence.error_code='groq_invalid_output'
    with patch('app.services.document_processing.resume_intelligence.enhance',return_value=({},evidence,0)):
        result=finish(client,doc,use_groq=True,allow_external_processing=True)
    assert result['status']=='awaiting_review' and result['method']=='groq_enhanced'
    assert result['agent_run']['status']=='partial'
    assert {t['name']:t['status'] for t in result['agent_run']['tasks']}=={
        'document_preparation':'completed','resume_intelligence':'completed','crew_quality':'failed','quality_validation':'completed'}
    audit=client.get(f'{BASE}/{doc}/audit').json()
    assert {a['name']:a['status'] for a in audit['agents']}['extraction']=='completed'
    assert {a['name']:a['status'] for a in audit['agents']}['validation']=='failed'
    assert client.get('/api/v1/profile').status_code==404


def test_quality_retry_is_bounded_and_does_not_repeat_extraction(crew_runtime):
    from app.agents.resume import RotatingGroqLLM
    from app.crews.resume import build_resume_crew
    calls=[]
    class Transport:
        settings=Settings(_env_file=None)
        def complete(self,messages,schema,**kw):
            calls.append((messages,schema,kw))
            if kw['schema_name']=='resume_extraction':return claims().model_dump_json()
            if len(calls)==2:raise ProviderUnavailable('groq_output_truncated','Output limit reached')
            return CompactQualityReport(issues=[],warnings=[]).model_dump_json()
    transport=Transport();deadline=time.monotonic()+30
    crew=build_resume_crew(RotatingGroqLLM(transport,ResumeClaims,1,deadline,'resume_extraction'),
        RotatingGroqLLM(transport,CompactQualityReport,1,deadline,'resume_quality'),compact=True)
    crew.kickoff(inputs={'prepared_document':'{"blocks":[{"block_id":2,"text":"Python"}]}'})
    assert len(calls)==3 and [c[2]['schema_name'] for c in calls]==['resume_extraction','resume_quality','resume_quality']
    assert [c[2]['max_output_tokens'] for c in calls[1:]]==[1600,3200]
    assert calls[1][0]!=calls[2][0]
    assert calls[1][1]['properties']['issues']['maxItems']==8


def test_provider_distinguishes_truncation_from_schema_rejection():
    for response,code in [(httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"issues":['}}]}),'groq_output_truncated'),
        (httpx.Response(400,json={'error':{'code':'json_validate_failed','failed_generation':'PRIVATE'}}),'groq_invalid_output')]:
        provider=GroqProvider(GroqKeyManager(['synthetic']),Settings(_env_file=None),
            client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(lambda request:response),**kw))
        with pytest.raises(ProviderUnavailable) as exc:provider.complete([],{})
        assert exc.value.code==code and 'PRIVATE' not in str(exc.value)
        if code=='groq_invalid_output':assert 'token budget' not in str(exc.value)


def test_vision_failure_cannot_report_the_whole_ai_run_as_ready(client,application):
    from test_document_processing import make_pdf,RESUME
    application.state.settings.max_document_bytes=100000
    doc=upload(client,make_pdf(RESUME),'vision-review.pdf','application/pdf')
    with patch('app.services.document_processing.analyse_pages',side_effect=ProviderUnavailable('groq_output_truncated','Layout response truncated.')), \
         patch('app.services.document_processing.resume_intelligence.enhance',return_value=({}, {}, 0)):
        result=finish(client,doc,use_groq=True,allow_external_processing=True,use_vision=True,allow_image_processing=True)
    assert result['status']=='awaiting_review' and result['agent_run']['status']=='partial'
    assert result['agent_run']['error_code']=='groq_output_truncated'
    tasks={t['name']:t['status'] for t in result['agent_run']['tasks']}
    assert tasks['vision_layout']=='failed' and tasks['resume_intelligence']=='completed'
    assert tasks['crew_quality']=='completed' and tasks['quality_validation']=='completed'
    assert client.get('/api/v1/profile').status_code==404


def test_real_crew_emits_completed_extraction_before_quality_exception(tmp_path,monkeypatch,crew_runtime):
    from queue import Queue
    from app.orchestration.resume import _crew_child
    calls=[];settings=Settings(_env_file=None)
    class Transport:
        settings=Settings(_env_file=None)
        def available_models(self,**kwargs):return {settings.text_model}
        def complete(self,messages,schema,**kwargs):
            calls.append(kwargs['schema_name'])
            if kwargs['schema_name']=='resume_extraction':return claims().model_dump_json()
            raise ProviderUnavailable('groq_invalid_output','Quality response failed validation.')
    monkeypatch.setattr('app.orchestration.resume.GroqProvider',lambda *args:Transport())
    monkeypatch.setattr('app.orchestration.resume.logging.disable',lambda *args:None)
    output=Queue()
    _crew_child(ResumeCrewInput(file_type='txt',blocks=[],compact_output=True,context={'blocks':[]}).model_dump(),
        settings,GroqKeyManager(['synthetic']),1,time.monotonic()+20,str(tmp_path),output)
    events=[]
    while not output.empty():events.append(output.get_nowait())
    checkpoint=next(i for i,event in enumerate(events) if event[0]=='checkpoint')
    failure=next(i for i,event in enumerate(events) if event[0]=='error')
    assert checkpoint<failure and ResumeClaims.model_validate(events[checkpoint][1])==claims()
    assert events[failure][1]['code']=='groq_invalid_output'
    assert calls==['resume_extraction','resume_quality','resume_quality']
