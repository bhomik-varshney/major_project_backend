"""Regression coverage for the recorded AI/Bengaluru empty-results failure."""
from datetime import date
from types import SimpleNamespace
import json

import pytest
from sqlalchemy import select

from app.db.models import MatchingRun,MatchingResult,CandidateProfile
from app.providers.jobs import SeededJobProvider,JobSnapshot
from app.schemas.jobs import JobFilters
from app.services.job_locations import parse_location,location_match
from app.services.job_roles import role_match,derived_topics,AI_CATEGORIES
from app.services.jobs import filter_jobs_with_audit,filter_jobs,availability
from app.services.match_normalization import normalize_candidate
from app.services.match_retrieval import retrieve
from app.services.match_scoring import score_job
from app.core.config import Settings
from test_matching import confirmed,start_finished,job_at


@pytest.mark.parametrize('query',['Bengaluru','Bangalore','Bengaluru, India','Bengaluru,India','Bangalore, India','Bengaluru, Karnataka','Bangalore, Karnataka'])
def test_location_alias_city_state_country(query):
    target={'location':'Bengaluru','country':'India','work_mode':'onsite'}
    result=location_match(query,target)
    assert result['accepted'] and result['relation']=='exact_city'
    assert result['requested']['city']=='Bengaluru' and result['requested']['state']=='Karnataka'
    assert result['requested']['original']==query
    assert not location_match(query,{'location':'Pune','country':'India','work_mode':'onsite'})['accepted']


def test_location_scopes_remote_country_unknown_and_conflict():
    india={'location':'Remote — India','country':'India','work_mode':'remote'}
    abroad={'location':'Remote — Germany','country':'Germany','work_mode':'remote'}
    assert not location_match('Bengaluru, India',india,'city')['accepted']
    assert location_match('Bangalore, India',india,'compatible')['relation']=='remote_compatible'
    assert not location_match('Bengaluru, India',abroad,'compatible')['accepted']
    assert location_match('Remote, India',india)['accepted']
    assert not location_match('Remote, India',abroad)['accepted']
    mysuru={'location':'Mysuru','country':'India','work_mode':'hybrid'}
    assert not location_match('Bengaluru',mysuru)['accepted']
    assert location_match('Bengaluru',mysuru,'state')['relation']=='same_state'
    assert location_match('Karnataka, India',mysuru)['relation']=='same_state'
    assert location_match('Bengaluru',{'location':'Pune','country':'India','work_mode':'onsite'},'country')['relation']=='same_country'
    assert not location_match('Bengaluru, USA',mysuru)['accepted']
    assert not location_match('Bengaluru',{'location':'','country':'India','work_mode':'onsite'})['accepted']
    assert location_match('Unknown Place',{'location':'Unknown Place','country':'India','work_mode':'onsite'})['relation']=='exact_label'


@pytest.mark.parametrize('title,category,relation',[
 ('AI Engineer','artificial_intelligence','exact_role'),
 ('Artificial Intelligence Engineer','artificial_intelligence','exact_role'),
 ('Machine Learning Engineer','machine_learning','related_role'),
 ('NLP Research Assistant','natural_language_processing','related_role'),
 ('Generative AI Engineer','generative_ai','related_role'),
 ('Computer Vision Engineer','computer_vision','related_role'),
])
def test_ai_role_expansion_is_grounded(title,category,relation):
    job={'title':title,'category':category,'required_skills':[],'preferred_skills':[],'description':''}
    for query in ('AI','Artificial Intelligence','ai engineer'):
        result=role_match(query,job);assert result['accepted'] and result['relation']==relation


def test_roles_distinguish_skill_weak_and_unrelated_occurrences():
    base={'title':'Backend Developer','category':'backend','required_skills':['Python'],'preferred_skills':[],'description':'maintain services and pair with a mentor'}
    assert not role_match('AI',base)['accepted']
    assert role_match('AI',base)['relation']=='unrelated_keyword'
    assert role_match('AI',{**base,'description':'Support an artificial intelligence product'})['relation']=='weak_keyword'
    assert not role_match('AI',{**base,'description':'Support an artificial intelligence product'})['accepted']
    result=role_match('AI',{**base,'required_skills':['Python','ML']})
    assert result['accepted'] and result['relation']=='skill_based'
    assert not role_match('AI',{**base,'required_skills':['HTML','JavaScript']})['accepted']


def test_exact_failed_query_has_34_active_onsite_ai_jobs_and_trace():
    snapshot=SeededJobProvider().snapshot()
    filters=JobFilters(location='Bengaluru,India',category='artificial_intelligence',work_mode='onsite',availability='active')
    rows,audit=filter_jobs_with_audit(snapshot,filters,True)
    assert len(rows)==34
    assert all(j.location=='Bengaluru' and j.category in AI_CATEGORIES and j.work_mode=='onsite' and availability(j,filters.as_of)=='active' for j in rows)
    assert audit['total_jobs']==10000 and audit['after_hard_filters']==34
    assert sum(s['excluded_count'] for s in audit['steps'])+len(rows)==10000
    assert len(audit['excluded_jobs'])==9966
    exact=filter_jobs(snapshot,filters.model_copy(update={'category_scope':'exact'}));assert len(exact)==1
    aliases=filter_jobs(snapshot,filters.model_copy(update={'location':'Bangalore, Karnataka','category':'','role':'AI'}))
    assert [j.id for j in aliases]==[j.id for j in rows]


def test_browsing_keywords_expand_ai_without_unrelated_substrings(client):
    responses=[client.get('/api/v1/jobs',params={'keyword':q,'page_size':50}).json() for q in ('AI','Artificial Intelligence')]
    assert responses[0]['total']==responses[1]['total']==1170
    assert [j['id'] for j in responses[0]['items']]==[j['id'] for j in responses[1]['items']]
    assert {j['category'] for j in responses[0]['items']} <= AI_CATEGORIES
    for query in ('Bengaluru, India','Bangalore, India','Bengaluru, Karnataka'):
        r=client.get('/api/v1/jobs',params={'location':query,'role':'AI','work_mode':'onsite','availability':'active'}).json()
        assert r['total']==34 and r['audit']['normalized_filters']['location']['city']=='Bengaluru'
    remote=client.get('/api/v1/jobs',params={'location':'Bengaluru, India','location_scope':'compatible','role':'AI','work_mode':'remote','availability':'active'}).json()
    assert remote['total']>0 and all(j['country']=='India' and j['remote'] for j in remote['items'])
    assert all(c['location']['relation']=='remote_compatible' for c in remote['match_context'].values())


def test_empty_filters_nulls_exclusion_diagnostics_and_no_dataset():
    snapshot=SeededJobProvider().snapshot()
    assert len(filter_jobs(snapshot,JobFilters(location=None,role=None,category=None,skills=None,work_mode='')))==10000
    rows,audit=filter_jobs_with_audit(snapshot,JobFilters(location='Atlantis',role='AI'))
    assert not rows and audit['empty_state']=='jobs_filtered_out' and audit['first_empty_filter']=='location'
    _,audit=filter_jobs_with_audit(JobSnapshot((),snapshot.version),JobFilters())
    assert audit['empty_state']=='no_jobs_in_dataset'
    rows=filter_jobs(snapshot,JobFilters(location='Bengaluru',role='AI',availability='expired'))
    assert rows and all(availability(j,snapshot.reference_date)=='expired' for j in rows)
    rows=filter_jobs(snapshot,JobFilters(location='Bengaluru',role='AI',availability='future'))
    assert rows and all(availability(j,snapshot.reference_date)=='future' for j in rows)


def test_small_filtered_pool_includes_zero_signal_candidates():
    jobs=[{'text':'Typography','required':[],'original':{'id':'demo-a'}},{'text':'PLC sensors','required':[],'original':{'id':'demo-b'}}]
    candidate=normalize_candidate({'skills':['UnknownTool']})
    selected,methods,audit=retrieve(candidate,jobs,[0,0],pool_size=50)
    assert selected==[0,1] and audit['retrieved_count']==2
    assert all(c['zero_signal'] for c in audit['candidates'])


def test_derived_role_topics_never_invent_preferences_or_skills():
    facts={'skills':['Python','LangGraph'],'experience':[{'organization':'Example','role':'Machine Learning Trainee'}],'projects':[{'name':'Evaluation','description':'Fine-tuned an LLM with QLoRA'}]}
    candidate=normalize_candidate(facts)
    assert candidate['derived_role_topics'] and not candidate['preferred_roles']
    assert not any(t['counts_as_preference'] or t['counts_as_required_skill'] for t in candidate['derived_role_topics'])
    assert 'machine learning' not in candidate['skill_tokens'] and 'llms' not in candidate['skill_tokens']
    assert derived_topics({'skills':['Python'],'projects':[{'name':'maintain services'}]})==[]


def test_real_confirmed_matching_query_and_owned_audit(client,profile):
    before=confirmed(client,profile)
    payload={'filters':{'location':'Bengaluru,India','role':'AI','work_mode':'onsite','availability':'active'},'minimum_score':0}
    run=start_finished(client,payload)
    assert run['eligible_jobs']==34 and run['result_count']==34
    page=client.get(f"/api/v1/matching/runs/{run['id']}/results").json()
    assert len(page['items'])==6 and page['diagnostics']['effective_minimum_score']==0
    assert page['diagnostics']['empty_state']=='ready'
    for row in page['items']:
        assert row['filter_evidence']['location']['relation']=='exact_city'
        assert row['filter_evidence']['role']['accepted']
        assert any(r['id']=='search_location' for r in row['reasons'])
    assert 'excluded_jobs' not in run['validation']['filter_audit']
    audit=client.get(f"/api/v1/matching/runs/{run['id']}/audit").json()
    assert len(audit['filter_audit']['excluded_jobs'])==9966
    assert audit['candidate_snapshot']['normalized']['derived_role_topics'] is not None
    assert client.get('/api/v1/profile').json()==before
    high=client.get(f"/api/v1/matching/runs/{run['id']}/results?minimum_score=100").json()
    assert high['diagnostics']['empty_state']=='score_below_threshold'


def test_saved_threshold_can_be_lowered_to_zero_and_fractional_values(client,application,profile):
    confirmed(client,profile);candidate=normalize_candidate({'career_objective':'Explore opportunities'})
    rows=[]
    for index,baseline in enumerate((0,.0625,.3125,.9375,62.5,100)):
        job=job_at(index,required_skills=[],preferred_skills=[],education_requirements=[])
        from app.services.match_normalization import normalize_job
        rows.append(score_job(candidate,normalize_job(job),baseline,0,0,None,Settings(_env_file=None),date(2026,10,19)))
    rows.sort(key=lambda r:-r.hybrid_score)
    assert sorted(r.hybrid_score for r in rows)==pytest.approx([0,.05,.25,.75,50,62.5])
    with application.state.session_factory() as db:
        p=db.scalar(select(CandidateProfile));run=MatchingRun(user_id=p.user_id,profile_revision=p.revision,candidate_hash='test',status='completed',stage='completed',request={'minimum_score':30,'filters':{}},total_jobs=6,eligible_jobs=6,result_count=2,dataset_version='test',dataset_reference_date='2026-10-19',config={},validation={})
        db.add(run);db.flush();id=run.id
        for rank,row in enumerate(rows,1):
            row.rank=rank;db.add(MatchingResult(run_id=id,job_id=row.job.id,rank=rank,hybrid_score=row.hybrid_score,baseline_score=row.baseline_score,normalized_job={},data=row.model_dump(mode='json')))
        db.commit()
    assert client.get(f'/api/v1/matching/runs/{id}/results').json()['total']==2
    for threshold,expected in ((0,6),(.1,4),(.5,3),(50,2),(62.5,1),(100,0)):
        result=client.get(f'/api/v1/matching/runs/{id}/results',params={'minimum_score':threshold}).json()
        assert result['total']==expected and result['diagnostics']['effective_minimum_score']==threshold
        assert all(row['hybrid_score']>=threshold for row in result['items'])
