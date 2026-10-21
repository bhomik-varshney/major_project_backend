"""Skill-gap grounding, real Flow, lifecycle, isolation and safe fallback regressions."""
from copy import deepcopy
from datetime import date
import json,time
from threading import Event
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,inspect,func
from app.core.config import Settings
from app.db.connection import initialize_database
from app.db.models import CandidateProfile,SkillGapRun,SkillGapReview
from app.providers.groq import GroqKeyManager,ProviderUnavailable
from app.schemas.skill_gaps import GapStart,GapResults
from app.services.gap_taxonomy import vocabulary,mentions
from app.services.skill_gap_analysis import skill_inventory,job_requirements,compare,aggregate_gaps,validate_analysis
from app.services.skill_taxonomy import canonical_skill,relationship
from test_matching import job_at,confirmed,start_finished

BASE='/api/v1/skill-gaps'
SETTINGS=Settings(_env_file=None)
AS_OF=date(2026,10,19)

def analysis(facts,jobs,**overrides):
    from app.flows.skill_gaps import SkillGapFlow
    payload={'candidate_snapshot':{'facts':facts,'proofs':{},'profile_source':'confirmed_fields_only','draft_data_used':False},'jobs':[j.model_dump(mode='json') for j in jobs],
        'selection':{},'mode':'selected','top_k':10,'as_of':str(AS_OF),'corpus_counts':{},'corpus_count':10000,'vocabulary':vocabulary(jobs),'use_ai':False,**overrides}
    flow=SkillGapFlow(payload,SETTINGS,GroqKeyManager([]),123,time.monotonic()+60)
    return GapResults.model_validate(flow.kickoff())

@pytest.mark.parametrize('left,right',[('JS','JavaScript'),('TS','TypeScript'),('React.js','React'),('Node','Node.js'),('Postgres','PostgreSQL'),('ML','Machine Learning'),('NLP','Natural Language Processing'),('AI','Artificial Intelligence'),('Amazon Web Services','AWS')])
def test_skill_equivalence(left,right):
    assert canonical_skill(left)==canonical_skill(right)
    assert relationship(left,right)=='synonym'

@pytest.mark.parametrize('facts,skill,classification,relation,credit',[
    ({'skills':['Python']},'Python','confirmed_match','exact',True),
    ({'skills':['JS']},'JavaScript','confirmed_match','synonym',True),
    ({'skills':['Python']},'Java','partial_match','related',False),
    ({'skills':['AWS']},'Azure','strong_related_match','related',False),
    ({'skills':['JavaScript']},'TypeScript','transferable_match','transferable',False),
    ({'skills':['Python']},'Docker','missing','unrelated',False),
    ({'education':[{'institution':'Example','qualification':'B.Tech'}]},'Docker','unknown','unknown',False),
    ({'career_objective':'Learn Docker'},'Docker','unknown','weak_association',False),
    ({'projects':[{'name':'Example','technologies':['Docker']}]},'Docker','partial_match','exact',True),
    ({'projects':[{'name':'Example','description':'Used Docker in a project'}]},'Docker','partial_match','weak_association',False),
    ({'skills':['Python'],'career_objective':'No experience with Rust'},'Rust','explicitly_mismatched','exact',False),
    ({'skills':['Rust'],'career_objective':'No experience with Rust'},'Rust','unknown','unknown',False),
])
def test_comparisons_preserve_evidence_distinctions(facts,skill,classification,relation,credit):
    job=job_at(required_skills=[skill],preferred_skills=[],tools_and_technologies=[])
    inv=skill_inventory(facts,{},vocabulary([job]));req,_=job_requirements([job]);result=compare(inv,req)[0]
    assert (result.classification,result.relationship,result.counts_as_requirement_match)==(classification,relation,credit)

def test_inventory_confidence_certifications_narrative_and_term_boundaries():
    job=job_at(required_skills=['ML','Java','Docker'],preferred_skills=[])
    facts={'skills':['Python'],'projects':[{'name':'Web','description':'Built HTML and JavaScript pages','technologies':['React']}],
        'experience':[{'organization':'Example','role':'Intern','technologies':['Docker']}], 'certifications':['AWS foundational certificate'],'career_objective':'Interested in machine learning'}
    inv=skill_inventory(facts,{'skills.0':{'confidence':'high','source':{'text':'Python'}}},vocabulary([job]))
    rows={s.normalized:s for s in inv}
    assert rows['python'].evidence[0].confidence=='high'
    assert rows['react'].evidence[0].strength=='supported_by_project'
    assert rows['docker'].evidence[0].strength=='supported_by_experience'
    assert rows['aws'].evidence[0].strength=='supported_by_certification'
    assert not rows['aws'].explicitly_confirmed
    assert not any(e.origin=='project' for e in rows['machine learning'].evidence)
    assert 'java' not in rows
    assert all('Not established' in s.proficiency for s in inv)

def test_required_preferred_duplicates_and_missing_fields():
    jobs=[job_at(required_skills=['JS','JavaScript'],preferred_skills=['JavaScript','Docker'],tools_and_technologies=[]),job_at(1,required_skills=[],preferred_skills=[],tools_and_technologies=[],description='')]
    req,warnings=job_requirements(jobs)
    assert [(r.normalized,r.type) for r in req[:2]]==[('javascript','required'),('docker','preferred')]
    assert req[-1].type=='unknown' and warnings
    assert compare([],req)[-1].classification=='unknown'

def test_multijob_priority_frequency_rare_gap_and_real_scorer_simulation():
    jobs=[job_at(i,required_skills=['Docker'],preferred_skills=['Kubernetes'] if i==0 else [],tools_and_technologies=[]) for i in range(5)]
    facts={'skills':['Python']};before=deepcopy(facts);r=analysis(facts,jobs)
    gaps={g.normalized:g for g in r.gaps}
    assert facts==before and r.validation['profile_modified'] is False
    assert gaps['docker'].required_count==5 and gaps['kubernetes'].preferred_count==1
    assert gaps['docker'].priority_score>gaps['kubernetes'].priority_score
    assert gaps['docker'].average_score_impact>0
    assert all(abs(i.simulated_score-i.current_score-i.delta)<.00011 for i in gaps['docker'].impact)
    assert len(r.gaps)==2 and r.validation['unsupported_claims']==0

def test_missing_evidence_is_not_lack_and_conflict_is_preserved():
    job=job_at(required_skills=['Rust'],preferred_skills=[])
    unknown=analysis({'education':[{'institution':'Example','qualification':'B.Tech'}]},[job])
    assert unknown.comparisons[0].classification=='unknown'
    assert unknown.gaps[0].confidence=='low'
    conflict=analysis({'skills':['Rust'],'career_objective':'No experience with Rust'},[job])
    assert conflict.status=='needs_review' and conflict.conflicts
    assert conflict.gaps[0].review_status=='needs_review'

@pytest.mark.parametrize('mutation,error',[
    ('priority','priority_conflict'),('explanation','unsupported_gap_output'),('impact','impact_calculation'),('guidance','unsupported_guidance_text'),('duplicate','duplicate_gap'),('omitted','missing_or_unsupported_gap')])
def test_validation_rejects_unsupported_and_contradictory_outputs(mutation,error):
    facts={'skills':['Python']};jobs=[job_at(required_skills=['Docker'],preferred_skills=[])]
    inv=skill_inventory(facts,{},vocabulary(jobs));req,_=job_requirements(jobs);cmp=compare(inv,req)
    gaps,_=aggregate_gaps(facts,inv,cmp,jobs,{},10000,SETTINGS,AS_OF)
    if mutation=='priority':gaps[0].priority_score=99
    elif mutation=='explanation':gaps[0].explanation='Candidate is certified and guaranteed a job.'
    elif mutation=='impact':gaps[0].impact[0].delta=99
    elif mutation=='guidance':gaps[0].guidance[0]['text']='Completed a course automatically.'
    elif mutation=='duplicate':gaps.append(gaps[0])
    else:gaps=[]
    with pytest.raises(ValueError,match=error):validate_analysis(facts,jobs,inv,req,cmp,gaps,SETTINGS,{},10000)

def test_deterministic_order_empty_selection_and_no_confirmed_gaps():
    jobs=[job_at(required_skills=['Python'],preferred_skills=[],tools_and_technologies=[])]
    assert analysis({'skills':['Python']},jobs).gaps==[]
    assert analysis({'skills':['Python']},[]).jobs==[]
    first=analysis({'skills':['SQL']},jobs);second=analysis({'skills':['SQL']},jobs)
    assert [g.model_dump() for g in first.gaps]==[g.model_dump() for g in second.gaps]

@pytest.mark.parametrize('code',['groq_rate_limited','groq_timeout','matching_invalid_output'])
def test_provider_failure_retains_local_results(code):
    with patch('app.crews.skill_gaps.review_gap_guidance',side_effect=ProviderUnavailable(code,'Unavailable')):
        r=analysis({'skills':['Python']},[job_at(required_skills=['Docker'],preferred_skills=[])],use_ai=True)
    assert r.status=='partial' and r.gaps and r.validation['unsupported_claims']==0
    assert next(t for t in r.tasks if t['stage']=='generating_guidance')['status']=='partial'
    assert r.provider_metadata['ai_review']['error_code']==code

def test_agent_disagreement_needs_review_preserves_deterministic_guidance():
    with patch('app.crews.skill_gaps.review_gap_guidance',return_value=({'status':'needs_review'},[{'code':'agent_disagreement','detail':'unsupported_action'}])):
        r=analysis({'skills':['Python']},[job_at(required_skills=['Docker'],preferred_skills=[])],use_ai=True)
    assert r.status=='needs_review' and r.conflicts and r.gaps[0].guidance[0]['id']=='verify'
    assert next(t for t in r.tasks if t['stage']=='generating_guidance')['status']=='partial'

def test_real_crewai_tasks_validate_actions_and_rotate_three_keys(tmp_path,monkeypatch):
    import httpx
    from app.crews.skill_gaps import review_gap_guidance
    from app.providers.groq import GroqProvider
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    gaps=analysis({'skills':['Python']},[job_at(required_skills=['Docker'],preferred_skills=[],tools_and_technologies=[])]).gaps
    calls=[]
    def respond(request):
        calls.append(request.headers['authorization'])
        if len(calls)==1:return httpx.Response(429)
        if len(calls)==2:return httpx.Response(503)
        schema=json.loads(request.content)['response_format']['json_schema']['schema']
        answer={'selections':[{'gap_id':g.id,'action_ids':['verify','practice','recheck']} for g in gaps]} if 'selections' in schema['properties'] else {'issues':[]}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]})
    manager=GroqKeyManager(['synthetic-first','synthetic-second','synthetic-third'],0)
    transport=GroqProvider(manager,SETTINGS,client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(respond),**kw))
    metadata,conflicts=review_gap_guidance(gaps,SETTINGS,manager,9,time.monotonic()+30,transport)
    assert not conflicts and metadata['engine']=='crewai.Crew' and metadata['status']=='completed'
    assert calls[:3]==['Bearer synthetic-first','Bearer synthetic-second','Bearer synthetic-third']
    assert not any(key in json.dumps(metadata) for key in ['synthetic-first','synthetic-second','synthetic-third'])
    class Unsupported:
        settings=SETTINGS
        def complete(self,messages,schema,**kwargs):
            return json.dumps({'selections':[{'gap_id':gaps[0].id,'action_ids':['invented']} ]}) if 'selections' in schema['properties'] else '{"issues":[]}'
    before=[g.model_dump() for g in gaps]
    metadata,conflicts=review_gap_guidance(gaps,SETTINGS,manager,9,time.monotonic()+30,Unsupported())
    assert metadata['status']=='needs_review' and conflicts and [g.model_dump() for g in gaps]==before

def test_invalid_crewai_structured_output_is_bounded_failure(tmp_path,monkeypatch):
    from app.crews.skill_gaps import review_gap_guidance
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path/'crew'))
    gaps=analysis({'skills':['Python']},[job_at(required_skills=['Docker'],preferred_skills=[],tools_and_technologies=[])]).gaps
    class Invalid:
        settings=SETTINGS
        def complete(self,*args,**kwargs):return 'not-json'
    with pytest.raises(ProviderUnavailable) as error:
        review_gap_guidance(gaps,SETTINGS,GroqKeyManager([]),9,time.monotonic()+30,Invalid())
    assert error.value.code=='matching_invalid_output'

def direct_runner(payload,settings,manager,progress=lambda data:None):
    from app.flows.skill_gaps import SkillGapFlow
    flow=SkillGapFlow(payload,settings,manager,9,time.monotonic()+60,lambda kind,data:progress(data) if kind=='stage' else None)
    return flow.kickoff()

def wait(client,id):
    end=time.monotonic()+50
    while time.monotonic()<end:
        r=client.get(f'{BASE}/runs/{id}');assert r.status_code==200
        value=r.json()
        if value['status'] not in {'queued','preparing','analysing_profile','analysing_requirements','comparing_skills','prioritising_gaps','generating_guidance','validating'}:return value
        time.sleep(.03)
    pytest.fail('skill analysis timed out')

def finish(client,payload,real_spawn=False):
    runner=patch('app.services.skill_gaps.execute_skill_gap',side_effect=direct_runner)
    if not real_spawn:runner.start()
    try:
        r=client.post(BASE+'/runs',json=payload);assert r.status_code==202,r.text
        out=wait(client,r.json()['id']);assert out['status'] in {'completed','partial','needs_review'},out
        return out
    finally:
        if not real_spawn:runner.stop()

def test_api_confirmation_ownership_results_evidence_review_and_retry(client,application,profile):
    id=job_at().id
    assert client.post(BASE+'/runs',json={'mode':'single','job_ids':[id]}).status_code==409
    client.post('/api/v1/profile',json=profile)
    assert client.get(BASE+'/readiness').json()['can_analyse'] is False
    client.post('/api/v1/profile/verify',json={'expected_revision':1,'confirmed':True})
    before=client.get('/api/v1/profile').json()
    run=finish(client,{'mode':'single','job_ids':[id]})
    assert len(run['tasks'])==7 and run['config']['engine']=='crewai.Flow'
    result=client.get(f"{BASE}/runs/{run['id']}/results").json();assert result['total']>0
    gap=result['items'][0];path=f"{BASE}/runs/{run['id']}/gaps/{gap['id']}"
    assert client.get(path).json()['comparisons']
    assert client.put(path+'/review',json={'decision':'reviewed'}).json()['profile_modified'] is False
    assert client.get(f"{BASE}/runs/{run['id']}/results").json()['reviews'][gap['id']]['decision']=='reviewed'
    assert client.get(f"{BASE}/runs/{run['id']}/audit").json()['profile_mutated'] is False
    assert client.get('/api/v1/profile').json()==before
    other=TestClient(application,headers={'X-Workspace-Client':'careeros-web'})
    try:
        other.post('/api/v1/session')
        assert other.get(f"{BASE}/runs/{run['id']}").status_code==404
        assert other.put(path+'/review',json={'decision':'dismissed'}).status_code==404
    finally:other.close()
    with patch('app.services.skill_gaps.execute_skill_gap',side_effect=direct_runner):
        retry=client.post(f"{BASE}/runs/{run['id']}/retry");assert retry.status_code==202
        assert wait(client,retry.json()['id'])['status']=='completed'

def test_selected_top_ranked_target_and_empty_modes(client,profile):
    confirmed(client,profile)
    matching=start_finished(client,{'filters':{'category':'backend','location':'Bengaluru','max_results':3},'minimum_score':0})
    top=finish(client,{'mode':'top_ranked','matching_run_id':matching['id'],'top_k':2});assert top['job_count']==2
    rows=client.get(f"/api/v1/matching/runs/{matching['id']}/results").json()['items']
    chosen=finish(client,{'mode':'selected','matching_run_id':matching['id'],'job_ids':[r['job']['id'] for r in rows[:2]]});assert chosen['job_count']==2
    target=finish(client,{'mode':'target','filters':{'role':'AI','location':'Bengaluru, India','work_mode':'onsite','availability':'active'},'top_k':3});assert target['job_count']==3
    empty=finish(client,{'mode':'target','filters':{'role':'AI','location':'Atlantis'}});assert empty['job_count']==0 and empty['gap_count']==0
    assert client.post(BASE+'/runs',json={'mode':'target','filters':{}}).status_code==422
    assert client.post(BASE+'/runs',json={'mode':'single','job_ids':[]}).status_code==422
    assert client.post(BASE+'/runs',json={'mode':'single','job_ids':['demo-nonexistent']}).status_code==404
    assert client.post(BASE+'/runs',json={'use_ai':True}).status_code==422

def test_supporting_tools_are_optional_not_mandatory():
    job=job_at(required_skills=['Python'],preferred_skills=['Docker'],tools_and_technologies=['Python','Docker','Postman'])
    result=analysis({'skills':['Python']},[job])
    by_skill={r.normalized:r for r in result.requirements}
    assert by_skill['postman'].type=='optional' and not by_skill['postman'].critical
    assert by_skill['docker'].type=='preferred'
    gaps={g.normalized:g for g in result.gaps}
    assert gaps['postman'].required_count==0 and gaps['postman'].optional_count==1

def test_cancel_during_worker_then_retry_and_stale_profile(client,profile):
    confirmed(client,profile);entered=Event();release=Event()
    def hold(payload,settings,manager,progress):
        entered.set();release.wait(5);progress(None)
        return direct_runner(payload,settings,manager,progress)
    with patch('app.services.skill_gaps.execute_skill_gap',side_effect=hold):
        response=client.post(BASE+'/runs',json={'mode':'single','job_ids':[job_at().id]});id=response.json()['id']
        assert entered.wait(5)
        assert client.post(BASE+'/runs',json={'mode':'single','job_ids':[job_at().id]}).status_code==409
        assert client.post(f'{BASE}/runs/{id}/cancel').json()['status']=='cancelled'
        release.set()
    time.sleep(.1)
    with patch('app.services.skill_gaps.execute_skill_gap',side_effect=direct_runner):
        retried=client.post(f'{BASE}/runs/{id}/retry');assert wait(client,retried.json()['id'])['status']=='completed'
    current=client.get('/api/v1/profile').json();edited={k:current[k] for k in profile};edited.update(expected_revision=current['revision'],skills=['Rust'])
    assert client.put('/api/v1/profile',json=edited).status_code==200
    assert client.get(f"{BASE}/runs/{retried.json()['id']}").json()['stale'] is True
    assert client.get('/api/v1/dashboard/summary').json()['skill_gap_count'] is None

def test_additive_migration_and_actual_spawned_pipeline(client,application,profile):
    confirmed(client,profile)
    with application.state.session_factory() as db:
        before=db.scalar(select(func.count()).select_from(CandidateProfile))
    initialize_database(application.state.engine);initialize_database(application.state.engine)
    assert {'skill_gap_runs','skill_gap_reviews'}<=set(inspect(application.state.engine).get_table_names())
    with application.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(CandidateProfile))==before
    run=finish(client,{'mode':'single','job_ids':[job_at().id]},real_spawn=True)
    assert run['status']=='completed' and run['gap_count']>0
