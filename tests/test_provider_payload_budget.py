import json
import time
from unittest.mock import patch

import httpx
import pytest

from app.core.config import Settings
from app.providers.groq import GroqKeyManager,GroqProvider,ProviderUnavailable
from app.schemas.agents import ResumeClaims,CompactCrewResult,ResumeQualityReport
from app.schemas.intelligence import VisionLayout
from app.services.crew_payload import compact_context,proposal_from_claims,source_batches
from test_resume_crew import crew_runtime


def fact(path,value,block_id=1):
    return {'path':path,'value':value,'block_id':block_id,'confidence':'high'}


def test_compact_context_keeps_source_but_excludes_redundant_layout_and_positions():
    blocks=[{'block_id':i,'text':f'Synthetic line {i}', 'page':1,'position':{'x':20,'y':500,'font_size':12},'cells':['x']*15,'private_unused_metadata':'x'*200} for i in range(1,106)]
    layout=[{'method':'text','id':str(i),'raw_text':'duplicate'*100,'region':{}} for i in range(105)]
    compact=compact_context(blocks,layout)
    assert [b['text'] for b in compact['blocks']]==[b['text'] for b in blocks]
    assert [b['block_id'] for b in compact['blocks']]==list(range(1,106))
    assert not compact['visual_groups']
    assert all(set(b)=={'block_id','text'} for b in compact['blocks'])
    assert len(json.dumps(compact)) < len(json.dumps({'blocks':blocks,'layout':layout}))*.15


def test_source_batches_never_discard_text_or_renumber_sources():
    blocks=[{'block_id':42,'text':'Synthetic '*2000},{'block_id':91,'text':'Python'}]
    batches=source_batches(blocks,1000)
    assert len(batches)>10
    assert ''.join(b['text'] for batch in batches for b in batch if b['block_id']==42)==blocks[0]['text']
    assert [b for batch in batches for b in batch if b['block_id']==91]==[blocks[1]]
    source=[{'block_id':1,'text':'Skills:'},{'block_id':2,'text':'Python'}]
    assert compact_context(source[1:],source_blocks=source)['sections'][0]['field']=='skills'


def test_flat_claims_accept_sparse_indexes_and_withhold_bad_fields():
    raw=ResumeClaims(claims=[fact('skills.9','Python'),fact('skills.15','SQL'),fact('projects.4.name','Campus Portal'),
        fact('projects.4.technologies.7','Python'),fact('email','not-an-email'),fact('verified','true'),fact('skills.9999','Invented')])
    proposal,withheld=proposal_from_claims(raw)
    assert proposal.values.skills==['Python','SQL']
    assert proposal.values.projects[0].name=='Campus Portal'
    assert proposal.values.projects[0].technologies==['Python']
    assert proposal.values.email=='' and withheld>=3
    assert {claim.path for claim in proposal.claims}>={'skills.0','skills.1','projects.0.name','projects.0.technologies.0'}


def test_actual_compact_crew_executes_typed_source_claims_and_validation(crew_runtime):
    from app.crews.resume import build_resume_crew
    from app.services.groq_extraction import strict_schema
    first=crew_runtime(ResumeClaims(claims=[fact('skills.0','Python')]).model_dump_json())
    second=crew_runtime(ResumeQualityReport().model_dump_json())
    crew=build_resume_crew(first,second,compact=True)
    crew.kickoff(inputs={'prepared_document':'{"blocks":[{"block_id":1,"text":"Python"}]}'})
    assert isinstance(crew.tasks[0].output.pydantic,ResumeClaims)
    assert len(first._prompts)==len(second._prompts)==1
    assert len(json.dumps(strict_schema(ResumeClaims)))<1500
    assert 'source' in str(second._prompts).lower()


def test_batched_orchestration_uses_full_source_for_validation_and_bounds_input(tmp_path):
    from app.orchestration.resume import enhance
    blocks=[{'block_id':1,'text':'Mira Example'},{'block_id':2,'text':'Skills:'},{'block_id':3,'text':'Python'}]+[
        {'block_id':i,'text':'Additional source notes. '*15} for i in range(4,12)]
    settings=Settings(_env_file=None,document_storage_path=tmp_path,groq_source_batch_chars=1000)
    calls=[]
    def run(prepared,*args,**kwargs):
        calls.append(prepared)
        claims=[]
        for block in prepared.blocks:
            if block['block_id']==1:claims.append(fact('name','Mira Example',1))
            if block['block_id']==3:claims.append(fact('skills.0','Python',3))
        return CompactCrewResult(extraction=ResumeClaims(claims=claims),quality=ResumeQualityReport())
    with patch('app.orchestration.resume.run_crew',side_effect=run):
        values,evidence,_=enhance(blocks,settings)
    assert len(calls)>1 and all(p.compact_output for p in calls)
    assert values['skills']==['Python'] and values['name']=='Mira Example'
    assert evidence['skills.0']['source']['block_id']==3
    assert ''.join(b['text'] for p in calls for b in p.blocks)==''.join(b['text'] for b in blocks)


def test_input_limit_splits_a_changed_payload_instead_of_replaying_same_request(tmp_path):
    from app.orchestration.resume import enhance
    settings=Settings(_env_file=None,document_storage_path=tmp_path,groq_source_batch_chars=1000)
    blocks=[{'block_id':1,'text':'Mira Example'},{'block_id':2,'text':'Synthetic source paragraph. '*25}]
    calls=[]
    def run(prepared,*args,**kwargs):
        calls.append(prepared)
        if len(calls)==1:raise ProviderUnavailable('groq_input_limit','Input too large')
        return CompactCrewResult(extraction=ResumeClaims(),quality=ResumeQualityReport())
    with patch('app.orchestration.resume.run_crew',side_effect=run):
        _,evidence,_=enhance(blocks,settings)
    assert 2<len(calls)<=4
    assert any('smaller source batches' in w for w in evidence.warnings)
    assert calls[0].blocks!=calls[1].blocks


def test_opt_in_rate_wait_is_bounded_and_respects_shared_cooldown():
    calls=[];waits=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(429,headers={'retry-after':'.01'})
        body=json.loads(request.content)
        assert body['reasoning_effort']=='low' and body['max_completion_tokens']==600
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    settings=Settings(_env_file=None)
    provider=GroqProvider(GroqKeyManager(['synthetic'],cooldown=0),settings,client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw))
    provider.on_wait=lambda:waits.append(1)
    assert provider.complete([],{},allow_wait=True,max_output_tokens=600,deadline=time.monotonic()+2)=='{}'
    assert len(calls)==2 and len(waits)==1


def test_new_vision_model_payload_uses_non_thinking_mode_and_bounded_completion():
    seen=[]
    def handler(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    provider=GroqProvider(GroqKeyManager(['synthetic']),Settings(_env_file=None),client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw))
    provider.complete([],{},model='qwen/qwen3.8-27b',json_mode=True,max_output_tokens=2200)
    assert seen[0]['reasoning_effort']=='none' and seen[0]['max_completion_tokens']==2200


def test_multiple_pages_use_one_image_per_request_and_unique_block_ids(tmp_path):
    from app.orchestration.vision import analyse_pages,VisionResult
    calls=[]
    def run(prepared,*args,**kwargs):
        data=prepared.model_dump();calls.append((data,kwargs));page=data['pages'][0]['page']
        assert len(data['pages'])==1
        return VisionResult(layout=VisionLayout.model_validate({'blocks':[{'id':'heading1','page':page,'raw_text':'Skills'}]}),
            model='qwen/qwen3.8-27b',attempted_models=['qwen/qwen3.8-27b'],warnings=[],completed_pages=[page])
    pages=[dict(page=i,width=100,height=200,content=b'synthetic') for i in (1,2,3)]
    with patch('app.orchestration.resume.run_crew',side_effect=run):
        result=analyse_pages(pages,Settings(_env_file=None,document_storage_path=tmp_path),GroqKeyManager(['synthetic']))
    assert result.completed_pages==[1,2,3]
    assert len({b.id for b in result.layout.blocks})==3
    assert len({kw['deadline'] for _,kw in calls})==1


@pytest.mark.parametrize('value,source,accepted',[('Python','Tools: PYTHON',True),('Rust','Tools: Python',False)])
def test_server_resolves_original_quotes_and_withholds_unsupported_claims(value,source,accepted):
    from app.services.resume_structure import anchored_ai_values
    blocks=[{'block_id':1,'text':'Skills:'},{'block_id':2,'text':source}]
    proposal,_=proposal_from_claims(ResumeClaims(claims=[fact('skills.0',value,2)]),blocks)
    values,evidence,withheld=anchored_ai_values(proposal,blocks)
    assert bool(values['skills']) is accepted
    if accepted:assert evidence['skills.0']['source']['quote']=='PYTHON'
    else:assert withheld==1
