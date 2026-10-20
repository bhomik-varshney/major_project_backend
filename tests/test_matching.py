"""Matching contracts, grounded scoring, real CrewAI tasks and private API lifecycle."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
import json
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.connection import initialize_database,make_engine,session_factory
from app.db.models import CandidateProfile,MatchingRun,MatchingResult,VerificationRecord
from app.providers.jobs import SeededJobProvider
from app.providers.groq import GroqKeyManager,ProviderUnavailable
from app.providers.embeddings import CachedEmbeddings,OllamaEmbeddings,semantic_scores,EmbeddingUnavailable,validate_vectors
from app.schemas.jobs import Job,JobFilters
from app.services.job_normalization import normalized_skills
from app.services.match_normalization import normalize_candidate,normalize_job,canonical_skill,deduplicate,fingerprint
from app.services.match_baseline import TfidfBaseline
from app.services.match_scoring import score_job,validate_ranking,explicit_years
from app.services.matching import filtered_snapshot

BASE='/api/v1/matching'


def job_at(index=0,**updates):
    raw=SeededJobProvider().snapshot().jobs[index].model_dump(mode='json')
    raw.update(updates)
    raw['metadata']['duplicate_group']=None
    raw['metadata']['country']=raw['country'];raw['metadata']['category']=raw['category']
    raw['normalized_skill_tokens']=normalized_skills(raw['required_skills']+raw['preferred_skills'])
    raw['remote']=raw['work_mode']=='remote'
    return Job.model_validate(raw)


def ranked(facts,job=None,semantic=None):
    settings=Settings(_env_file=None)
    candidate=normalize_candidate(facts);job=job or job_at(required_skills=['Python','SQL'],preferred_skills=['Docker'],work_mode='remote',experience_level='student',education_requirements=['Bachelor degree'])
    normalized=normalize_job(job)
    tfidf=TfidfBaseline([candidate['text'],normalized['text']])
    score=tfidf.similarity(tfidf.vector(candidate['text']),tfidf.vector(normalized['text']))
    row=score_job(candidate,normalized,score,score,score,semantic,settings,date(2026,10,19))
    return row,candidate,normalized


def direct_flow(payload,settings,manager,progress=lambda data:None):
    from app.flows.matching import CareerMatchingFlow
    engine=make_engine(settings.database_url)
    try:
        flow=CareerMatchingFlow(payload,settings,session_factory(engine),manager,123,time.monotonic()+settings.matching_timeout_seconds,lambda kind,data:progress(data) if kind=='stage' else None)
        return flow.kickoff()
    finally:engine.dispose()


def confirmed(client,profile):
    saved=client.post('/api/v1/profile',json=profile).json()
    return client.post('/api/v1/profile/verify',json={'expected_revision':saved['revision'],'confirmed':True}).json()


def await_run(client,id):
    end=time.monotonic()+40
    while time.monotonic()<end:
        response=client.get(f'{BASE}/runs/{id}');assert response.status_code==200,response.text
        value=response.json()
        if value['status'] in {'completed','partial','failed','cancelled'}:return value
        time.sleep(.05)
    pytest.fail('matching did not finish')


def start_finished(client,payload=None,real_spawn=False):
    runner=patch('app.services.matching.execute_matching',side_effect=direct_flow)
    if not real_spawn:runner.start()
    try:
        response=client.post(f'{BASE}/runs',json=payload or {'minimum_score':0})
        assert response.status_code==202,response.text
        result=await_run(client,response.json()['id'])
        assert result['status'] in {'completed','partial'},result
        return result
    finally:
        if not real_spawn:runner.stop()


def test_no_profile_or_unconfirmed_drafts_cannot_match(client,profile):
    assert client.get(f'{BASE}/readiness').json()['can_match'] is False
    assert client.post(f'{BASE}/runs',json={}).json()['error']['code']=='confirmed_profile_required'
    client.post('/api/v1/profile',json=profile)
    assert client.get(f'{BASE}/readiness').json()['can_match'] is False
    assert client.post(f'{BASE}/runs',json={}).status_code==409


def test_partial_confirmed_snapshot_excludes_every_unconfirmed_field(client,application,profile):
    client.post('/api/v1/profile',json=profile)
    with application.state.session_factory() as db:
        p=db.scalar(select(CandidateProfile));db.add(VerificationRecord(user_id=p.user_id,profile_id=p.id,profile_revision=p.revision,confirmed_values={'skills':['Python','SQL']},applied_fields=['skills']));db.commit()
    ready=client.get(f'{BASE}/readiness').json()
    assert ready['can_match'] and ready['confirmed_fields']==['skills'] and 'preferred_roles' in ready['excluded_unconfirmed_fields']
    run=start_finished(client,{'filters':{'keyword':'nonexistent synthetic phrase'},'minimum_score':0})
    data=client.get(f"{BASE}/runs/{run['id']}/audit").json()
    assert data['candidate_snapshot']['confirmed_facts']=={'skills':['Python','SQL']}
    assert not data['draft_data_used'] and data['run']['result_count']==0


@pytest.mark.parametrize('left,right', [('JS','JavaScript'),('React.js','React'),('Postgres','PostgreSQL'),('ML','Machine Learning')])
def test_explicit_aliases(left,right):assert canonical_skill(left)==canonical_skill(right)


@pytest.mark.parametrize('left,right',[('Java','JavaScript'),('C','C++'),('C#','C++'),('React','React Native'),('SQL','PostgreSQL')])
def test_distinct_skills_are_not_merged(left,right):assert canonical_skill(left)!=canonical_skill(right)


def test_descriptions_do_not_invent_skill_evidence():
    c=normalize_candidate({'projects':[{'name':'Example','description':'Python React Kubernetes SQL'}]})
    assert not c['skill_tokens'] and not c['skill_mappings']


def test_exact_partial_preferred_and_transferable_skills():
    row,c,j=ranked({'skills':['Python','Postgres','Docker']},job_at(required_skills=['Python','SQL','TypeScript'],preferred_skills=['Docker','Python']))
    assert [s['normalized'] for s in row.matched_required]==['python']
    assert {s['normalized'] for s in row.missing_required}=={'sql','typescript'}
    assert row.components['required_skills'].score==pytest.approx(100/3)
    assert row.components['preferred_skills'].score==100 and len(row.matched_preferred)==1
    assert row.calculation['critical_skill_penalty']==10
    assert validate_ranking([row],c,0,{row.job.id:j})['score_errors']==0
    transfer,_,_=ranked({'skills':['JavaScript']},job_at(required_skills=['TypeScript']))
    assert not transfer.matched_required and transfer.transferable_skills and transfer.transferable_skills[0]['counts_as_requirement_match'] is False


def test_missing_skills_are_unknown_without_penalty_and_capped():
    row,_,_=ranked({'preferred_roles':['Backend Intern']})
    assert row.components['required_skills'].score is None
    assert row.components['required_skills'].state=='missing_information'
    assert row.calculation['critical_skill_penalty']==0
    assert row.hybrid_score<=row.calculation['uncertainty_ceiling']<100


def test_missing_job_skills_are_not_applicable():
    row,_,_=ranked({'skills':['Python']},job_at(required_skills=[],preferred_skills=[]))
    assert row.components['required_skills'].score is None and row.components['required_skills'].state=='not_applicable'


def test_remote_location_education_and_experience_components(profile):
    profile['preferred_locations']=['Remote']
    profile['experience']=[{'organization':'Example','role':'Intern','period':'Jan 2026 - Jun 2026'}]
    row,_,_=ranked(profile)
    assert row.components['location'].score==100 and row.components['education'].score==100
    assert explicit_years(profile['experience'],date(2026,10,19))==.5
    mismatch,_,_=ranked(profile,job_at(work_mode='onsite',location='London',experience_years_min=3,experience_years_max=3,education_requirements=['Master degree']))
    assert mismatch.components['location'].state=='explicit_mismatch'
    assert mismatch.components['experience'].score==0 and mismatch.components['education'].score==0
    unknown,_,_=ranked({'skills':['Python'],'location':'London'})
    assert unknown.components['location'].score is None


def test_baseline_determinism_normalization_and_unrelated_text():
    corpus=['Python SQL backend service','Python SQL backend service','visual typography brand design']
    model=TfidfBaseline(corpus);again=TfidfBaseline(corpus)
    assert model.similarity(model.vector(corpus[0]),model.vector(corpus[1]))==pytest.approx(100)
    assert model.similarity(model.vector(corpus[0]),model.vector(corpus[2]))==0
    assert model.idf==again.idf and model.vector(corpus[0])==again.vector(corpus[0])
    assert model.similarity({},model.vector(corpus[0]))==0


def test_validation_rejects_unsupported_skills_reasons_math_and_order():
    row,c,j=ranked({'skills':['Python','SQL']});mapping={row.job.id:j}
    for mutation,error in [(lambda r:r.matched_required.clear(),'unsupported_matched_skill'),(lambda r:setattr(r,'explanation','You are guaranteed a job'),'contradictory_explanation'),(lambda r:setattr(r,'hybrid_score',99.99),'score_calculation_mismatch'),(lambda r:r.highlighted_reason_ids.append('invented'),'unsupported_explanation'),(lambda r:setattr(r.reasons[0],'text','Invented fact'),'unsupported_reason_catalog')]:
        bad=row.model_copy(deep=True);mutation(bad)
        with pytest.raises(ValueError,match=error):validate_ranking([bad],c,0,mapping)
    with pytest.raises(ValueError,match='duplicate_recommendation'):validate_ranking([row,row],c,0,mapping)


def test_duplicate_selection_stable_and_expired_filters():
    snapshot=SeededJobProvider().snapshot()
    active=filtered_snapshot(snapshot,JobFilters(availability='active'))
    assert all(j.posted_date<=snapshot.reference_date and (not j.closing_date or j.closing_date>=snapshot.reference_date) for j in active)
    expired=filtered_snapshot(snapshot,JobFilters(availability='expired'))
    assert expired and all(j.closing_date<snapshot.reference_date for j in expired)
    unique,duplicates=deduplicate(list(snapshot.jobs));other,repeat=deduplicate(list(reversed(snapshot.jobs)))
    assert [j.id for j in unique]==[j.id for j in other] and duplicates==repeat and duplicates
    assert len(unique)+len(duplicates)==1000


class FakeEmbeddings:
    name='fixture';model='deterministic-test-v1'
    def __init__(self):self.calls=0
    def embed(self,texts,deadline):self.calls+=1;return [[1.,.25] for _ in texts]


def test_real_embedding_interface_cache_and_semantic_components(application):
    factory=application.state.session_factory
    initialize_database(application.state.engine)
    provider=FakeEmbeddings();cache=CachedEmbeddings(provider,factory)
    a=cache.embed(['hello','hello'],'synthetic',time.monotonic()+5)
    b=cache.embed(['hello'],'synthetic',time.monotonic()+5)
    assert a==[b[0],b[0]] and provider.calls==1 and cache.hits==1 and cache.misses==1
    row,c,j=ranked({'skills':['Python']},semantic=100)
    assert row.components['semantic'].score==100 and row.components['lexical_similarity'].score is None
    scores,projects,metadata,warning=semantic_scores(c,[j],Settings(_env_file=None),factory,'fixture',time.monotonic()+5,provider=provider)
    assert scores[0]==pytest.approx(100) and metadata['status']=='active' and warning is None
    cache.embed(['hello'],'different-user',time.monotonic()+5)
    assert provider.calls==4 # candidate, job and distinct user are separate cache scopes.


@pytest.mark.parametrize('vectors',[[[0,0]],[[float('nan'),1]],[[True,1]],[[1],[1,2]],None])
def test_invalid_embeddings_rejected(vectors):
    with pytest.raises(EmbeddingUnavailable):validate_vectors(vectors,1)


def test_provider_retry_and_truthful_fallback(application):
    initialize_database(application.state.engine)
    calls=[]
    def request(req):
        calls.append(req)
        return httpx.Response(503) if len(calls)==1 else httpx.Response(200,json={'embeddings':[[1.,2.]]})
    provider=OllamaEmbeddings(Settings(_env_file=None),lambda **kw:httpx.Client(transport=httpx.MockTransport(request),**kw))
    assert provider.embed(['synthetic'],time.monotonic()+5)==[[1.,2.]] and len(calls)==2
    class Broken(FakeEmbeddings):
        def embed(self,texts,deadline):raise RuntimeError('private provider diagnostic')
    _,candidate,job=ranked({'skills':['Python']})
    scores,_,metadata,warning=semantic_scores(candidate,[job],Settings(_env_file=None),application.state.session_factory,'synthetic',time.monotonic()+5,provider=Broken())
    assert scores is None and metadata['status']=='unavailable' and 'private' not in warning


def test_real_flow_stage_results_empty_and_ai_failure(application):
    initialize_database(application.state.engine)
    payload={'candidate':{'skills':['Python']},'jobs':[],'user_scope':'synthetic','minimum_score':0,'use_ai':True,'as_of':'2026-10-19'}
    empty=direct_flow(payload,application.state.settings,GroqKeyManager((),5))
    assert empty['status']=='completed' and empty['provider_metadata']['ai_review']['status']=='skipped'
    payload['jobs']=[job_at().model_dump(mode='json')]
    result=direct_flow(payload,Settings(_env_file=None,database_url=application.state.settings.database_url),GroqKeyManager((),5))
    assert result['status']=='partial' and len(result['results'])==1
    assert len(result['tasks'])==6 and result['tasks'][4]['status']=='partial'
    assert result['provider_metadata']['ai_review']['error_code']=='groq_not_configured'


def test_full_seeded_run_real_spawn_api_pagination_details_and_profile_preservation(client,profile):
    before=confirmed(client,profile)
    run=start_finished(client,{'filters':{'availability':'all'},'minimum_score':0},real_spawn=True)
    assert run['total_jobs']==1000 and 950<run['eligible_jobs']<1000 and run['validation']['score_errors']==0
    assert len(run['tasks'])==6 and all(t['status']=='completed' for t in run['tasks'])
    assert run['provider_metadata']['embeddings']['status']=='not_configured'
    first=client.get(f"{BASE}/runs/{run['id']}/results",params={'page_size':2}).json()
    second=client.get(f"{BASE}/runs/{run['id']}/results",params={'page_size':2,'page':2}).json()
    assert first['total']==run['result_count'] and not ({r['job']['id'] for r in first['items']}&{r['job']['id'] for r in second['items']})
    row=first['items'][0];jobid=row['job']['id']
    assert client.get(f"{BASE}/runs/{run['id']}/results/{jobid}").json()['normalized_job']['required']
    assert client.get(f"{BASE}/runs/{run['id']}/results/{jobid}/explanation").json()['text']==row['explanation']
    assert client.put(f'{BASE}/bookmarks/{jobid}').status_code==200
    assert client.put(f'{BASE}/bookmarks/{jobid}').status_code==200
    assert len(client.get(f'{BASE}/bookmarks').json())==1
    saved=client.get(f"{BASE}/runs/{run['id']}/results",params={'saved_only':True}).json()
    assert saved['total']==1
    assert client.delete(f'{BASE}/bookmarks/{jobid}').status_code==204
    assert client.get('/api/v1/profile').json()==before
    assert client.get('/api/v1/dashboard/summary').json()['recommended_job_count']==run['result_count']


def test_filters_stale_retry_and_history_deletion(client,profile):
    saved=confirmed(client,profile)
    run=start_finished(client,{'filters':{'work_mode':'remote','availability':'active'},'minimum_score':0})
    rows=client.get(f"{BASE}/runs/{run['id']}/results").json()
    assert rows['items'] and all(r['job']['remote'] for r in rows['items'])
    result=client.get(f"{BASE}/runs/{run['id']}/results",params={'work_mode':'onsite'}).json()
    assert result['total']==0
    client.put('/api/v1/profile',json={**profile,'skills':['Rust'],'expected_revision':saved['revision']})
    assert client.get(f"{BASE}/runs/{run['id']}").json()['stale']
    assert client.get('/api/v1/dashboard/summary').json()['recommended_job_count'] is None
    # Unchanged confirmed preference fields remain eligible; changed skills do not.
    with patch('app.services.matching.execute_matching',side_effect=direct_flow):
        retried=client.post(f"{BASE}/runs/{run['id']}/retry");assert retried.status_code==202
        next_run=await_run(client,retried.json()['id'])
    assert next_run['id']!=run['id']
    audit=client.get(f"{BASE}/runs/{next_run['id']}/audit").json()
    assert 'skills' not in audit['candidate_snapshot']['confirmed_facts']
    assert client.delete(f"{BASE}/runs/{run['id']}").status_code==204
    assert client.get(f"{BASE}/runs/{run['id']}").status_code==404


def test_private_runs_and_ai_consent(client,application,profile):
    confirmed(client,profile)
    assert client.post(f'{BASE}/runs',json={'use_ai':True}).status_code==422
    run=start_finished(client,{'filters':{'keyword':'no such listing phrase'},'minimum_score':0})
    with TestClient(application,headers={'X-Workspace-Client':'careeros-web'}) as other:
        other.post('/api/v1/session')
        assert other.get(f"{BASE}/runs/{run['id']}").status_code==404
        assert other.get(f'{BASE}/runs').json()==[]


def test_concurrent_start_and_cancel_no_publish(client,application,profile):
    confirmed(client,profile)
    with patch.object(application.state.matching_processor,'submit'):
        def start(_):return client.post(f'{BASE}/runs',json={})
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(start,range(2)))
        assert sorted(r.status_code for r in responses)==[202,409]
        run=next(r.json() for r in responses if r.status_code==202)
        assert client.post(f"{BASE}/runs/{run['id']}/cancel").json()['status']=='cancelled'
        assert client.post(f"{BASE}/runs/{run['id']}/cancel").status_code==409
        assert client.get(f"{BASE}/runs/{run['id']}/results").status_code==409
    application.state.matching_processor.capacity.release()


def test_settings_weights_and_minimum_validation():
    with pytest.raises(ValueError):Settings(_env_file=None,matching_weights={'required_skills':1})
    with pytest.raises(ValueError):Settings(_env_file=None,matching_minimum_score=101)


def test_real_crewai_review_accepts_only_grounded_ids(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    from app.crews.matching import review_recommendations
    row,c,j=ranked({'skills':['Python']})
    calls=[]
    class ScriptedTransport:
        settings=Settings(_env_file=None)
        def complete(self,messages,schema,**kwargs):
            calls.append((messages,schema,kwargs))
            return json.dumps({'selections':[{'job_id':row.job.id,'reason_ids':['required','missing','method']}]}) if kwargs['schema_name']=='matching_explanation' else '{"issues":[]}'
    before=row.hybrid_score
    metadata=review_recommendations([row],Settings(_env_file=None),GroqKeyManager((),5),123,time.monotonic()+30,transport=ScriptedTransport())
    assert metadata['engine']=='crewai.Crew' and metadata['reviewed_jobs']==1 and len(calls)==2
    assert row.explanation_method=='crewai_selected_grounded_reasons' and row.hybrid_score==before
    assert validate_ranking([row],c,0,{row.job.id:j})['unsupported_reasons']==0
    assert all(call[1]['additionalProperties'] is False for call in calls)
    class Unsupported(ScriptedTransport):
        def complete(self,messages,schema,**kwargs):
            return json.dumps({'selections':[{'job_id':row.job.id,'reason_ids':['invented']}]}) if kwargs['schema_name']=='matching_explanation' else '{"issues":[]}'
    original=row.explanation
    with pytest.raises(ProviderUnavailable) as exc:review_recommendations([row],Settings(_env_file=None),GroqKeyManager((),5),123,time.monotonic()+30,transport=Unsupported())
    assert exc.value.code=='matching_unsupported_explanation' and row.explanation==original


def test_real_crewai_provider_rotates_three_keys_and_does_not_expose_them(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    from app.crews.matching import review_recommendations
    from app.providers.groq import GroqProvider
    row,_,_=ranked({'skills':['Python']});calls=[]
    def respond(req):
        calls.append(req.headers['authorization'])
        if len(calls)==1:return httpx.Response(429)
        if len(calls)==2:return httpx.Response(503)
        schema=json.loads(req.content)['response_format']['json_schema']['name']
        answer=json.dumps({'selections':[{'job_id':row.job.id,'reason_ids':['required','missing']}]}) if schema=='matching_explanation' else '{"issues":[]}'
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':answer}}]})
    settings=Settings(_env_file=None);manager=GroqKeyManager(['fixture-a','fixture-b','fixture-c'],0)
    transport=GroqProvider(manager,settings,client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(respond),**kw))
    result=review_recommendations([row],settings,manager,123,time.monotonic()+30,transport=transport)
    assert calls[:3]==['Bearer fixture-a','Bearer fixture-b','Bearer fixture-c']
    assert result['status']=='completed' and not any(manager.owners)
    assert not any(key in json.dumps(result) for key in ['fixture-a','fixture-b','fixture-c'])


def matching_holding_child(payload,settings,manager,owner,deadline,scratch,output):
    manager.acquire(owner)
    output.put(('stage',{'stage':'preparing','tasks':[]}))
    time.sleep(60)


def matching_checkpoint_child(payload,settings,manager,owner,deadline,scratch,output):
    output.put(('checkpoint',{'status':'completed','results':[],'tasks':[{'status':'processing'}],'warnings':[],'provider_metadata':{},'candidate':{},'normalized_jobs':{},'validation':{},'duplicate_ids':[]}))
    time.sleep(60)


def test_spawn_timeout_cancel_and_safe_partial_checkpoint(tmp_path):
    from app.orchestration.matching import execute_matching
    settings=Settings(_env_file=None,document_storage_path=tmp_path/'scratch',matching_timeout_seconds=5)
    manager=GroqKeyManager(['fixture-a'])
    with pytest.raises(ProviderUnavailable) as exc:execute_matching({},settings,manager,child_target=matching_holding_child)
    assert exc.value.code=='matching_timeout' and not any(manager.owners)
    assert not list(settings.document_storage_path.iterdir())
    def cancel(stage):
        if stage is not None:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):execute_matching({},settings,manager,progress=cancel,child_target=matching_holding_child)
    result=execute_matching({},settings,manager,child_target=matching_checkpoint_child)
    assert result['status']=='partial' and result['provider_metadata']['ai_review']['error_code']=='matching_timeout'
    assert not any(manager.owners)


def test_restart_recovery_preserves_old_profile_and_matching_tables(application,client,profile):
    before=confirmed(client,profile)
    with patch.object(application.state.matching_processor,'submit'):
        run=client.post(f'{BASE}/runs',json={}).json()
    application.state.matching_processor.capacity.release()
    application.state.matching_processor.recover_interrupted()
    initialize_database(application.state.engine);initialize_database(application.state.engine)
    assert client.get(f"{BASE}/runs/{run['id']}").json()['error_code']=='matching_interrupted'
    assert client.get('/api/v1/profile').json()==before


@pytest.mark.parametrize('mode',['malformed','conflict','busy'])
def test_ai_review_bad_output_conflict_and_all_keys_busy_leave_grounded_results(tmp_path,monkeypatch,mode):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    from app.crews.matching import review_recommendations
    row,_,_=ranked({'skills':['Python']});before=row.model_dump()
    class Transport:
        settings=Settings(_env_file=None)
        def complete(self,messages,schema,**kwargs):
            if mode=='busy':raise ProviderUnavailable('groq_keys_unavailable','No keys currently available.')
            if mode=='malformed':return 'not JSON'
            return json.dumps({'selections':[{'job_id':row.job.id,'reason_ids':['required','missing']}]}) if kwargs['schema_name']=='matching_explanation' else '{"issues":["unknown_reason"]}'
    with pytest.raises(ProviderUnavailable) as exc:review_recommendations([row],Settings(_env_file=None),GroqKeyManager((),5),123,time.monotonic()+30,transport=Transport())
    assert exc.value.code=={'malformed':'matching_invalid_output','conflict':'matching_review_conflict','busy':'groq_keys_unavailable'}[mode]
    assert row.model_dump()==before


def test_stable_equal_scores_use_id_and_preferred_skill_absence_does_not_penalize(application):
    initialize_database(application.state.engine)
    first=job_at(required_skills=['Python'],preferred_skills=[],work_mode='remote')
    second=first.model_dump(mode='json');second['id']='demo-tie-z';second['company']='Unique Example';second['application_url']=second['source_url']='https://example.invalid/jobs/demo-tie-z'
    payload={'candidate':{'skills':['Python']},'jobs':[second,first.model_dump(mode='json')],'user_scope':'synthetic','minimum_score':0,'use_ai':False,'as_of':'2026-10-19'}
    result=direct_flow(payload,application.state.settings,GroqKeyManager((),5))
    assert [r['job']['id'] for r in result['results']]==sorted([first.id,second['id']])
    assert result['results'][0]['hybrid_score']==result['results'][1]['hybrid_score']
    assert all(r['components']['preferred_skills']['state']=='not_applicable' for r in result['results'])


def test_future_work_does_not_count_as_completed_experience():
    assert explicit_years([{'period':'Jan 2027 - Dec 2027'}],date(2026,10,19)) is None
    assert explicit_years([{'period':'Jan 2026 - Dec 2027'}],date(2026,10,19))==pytest.approx(10/12)


def test_different_users_match_concurrently_without_mixing_snapshots(client,application,profile):
    confirmed(client,profile)
    with TestClient(application,headers={'X-Workspace-Client':'careeros-web'}) as other:
        other.post('/api/v1/session')
        confirmed(other,{**profile,'skills':['Figma','Wireframing'],'preferred_roles':['UX Designer']})
        with patch('app.services.matching.execute_matching',side_effect=direct_flow):
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks=[pool.submit(c.post,f'{BASE}/runs',json={'filters':{'category':'backend'},'minimum_score':0}) for c in (client,other)]
                responses=[task.result() for task in tasks]
            assert all(r.status_code==202 for r in responses)
            runs=[await_run(c,r.json()['id']) for c,r in zip((client,other),responses)]
        assert all(r['status']=='completed' for r in runs)
        first=client.get(f"{BASE}/runs/{runs[0]['id']}/audit").json()['candidate_snapshot']['confirmed_facts']
        second=other.get(f"{BASE}/runs/{runs[1]['id']}/audit").json()['candidate_snapshot']['confirmed_facts']
        assert first['skills']==['Python','SQL'] and second['skills']==['Figma','Wireframing']
        assert other.get(f"{BASE}/runs/{runs[0]['id']}/audit").status_code==404
