import json

import pytest

from app.providers.jobs import SeededJobProvider
from app.schemas.jobs import JobFilters
from app.services.jobs import browse_jobs


def test_seeded_records_are_stable_labeled_bounded_and_diverse(client):
    first = client.get('/api/v1/jobs?page_size=50')
    second = client.get('/api/v1/jobs?page_size=50')
    assert first.status_code == 200 and first.json() == second.json()
    data = first.json()
    assert data['total'] == data['matched_total'] == 10000 and data['source'] == 'seeded_mock'
    snapshot = SeededJobProvider().snapshot()
    assert len({j.id for j in snapshot.jobs}) == 10000
    assert len({j.category for j in snapshot.jobs}) == 56
    assert all(j.source == 'seeded_mock' and j.generated_data and j.metadata.is_demo and 'example.invalid' in j.application_url for j in snapshot.jobs)
    a = client.get('/api/v1/jobs?page=1&page_size=3').json()
    b = client.get('/api/v1/jobs?page=2&page_size=3').json()
    assert a['total'] == b['total'] == 10000 and len(a['items']) == len(b['items']) == 3
    assert not {j['id'] for j in a['items']} & {j['id'] for j in b['items']}


@pytest.mark.parametrize('query,check', [
    ('keyword=backend', lambda j:'backend' in (j.title+' '+j.description).casefold().replace('back-end','backend') or j.category=='backend'),
    ('skill=python&skill=sql', lambda j:{'python','sql'}.issubset(j.normalized_skill_tokens)),
    ('location=NOIDA', lambda j:'noida' in j.location.casefold()),
    ('remote=true&skill=Python', lambda j:j.remote and 'python' in j.normalized_skill_tokens),
    ('experience=entry_level', lambda j:j.experience_level=='entry_level'),
    ('category=embedded', lambda j:j.category=='embedded'),
    ('work_mode=hybrid', lambda j:j.work_mode=='hybrid'),
    ('employment_type=internship', lambda j:j.employment_type=='internship'),
    ('company=Demo%20River%20Systems', lambda j:'demo river systems' in j.company.casefold()),
    ('industry=Healthcare', lambda j:'healthcare' in j.industry.casefold()),
    ('country=Germany', lambda j:j.country=='Germany'),
])
def test_filters(client, query, check):
    data = client.get('/api/v1/jobs?page_size=50&'+query)
    assert data.status_code == 200, data.text
    result = data.json()
    expected = [j for j in SeededJobProvider().snapshot().jobs if check(j)]
    assert result['total'] == len(expected)
    assert [j['id'] for j in result['items']] == [j.id for j in expected[:50]]


@pytest.mark.parametrize('query', ['keyword=not-a-real-vacancy', 'remote=true&location=Noida', 'page=9999', 'category=nonexistent'])
def test_empty_results(client, query):
    assert client.get('/api/v1/jobs?'+query).json()['items'] == []


@pytest.mark.parametrize('query', ['page=0', 'page_size=51', 'max_results=10001', 'max_results=0', 'remote=maybe', 'experience=senior', 'work_mode=somewhere', 'posted_from=not-date', 'posted_from=2026-11-01&posted_to=2026-10-01', 'keyword='+'x'*121, '&'.join('skill=x' for _ in range(21))])
def test_filter_limits(client, query):
    assert client.get('/api/v1/jobs?'+query).status_code == 422


def test_malformed_records_are_excluded_without_breaking_valid_jobs(tmp_path, client, application):
    path = tmp_path / 'jobs.json'
    job = SeededJobProvider().snapshot().jobs[0].model_dump(mode='json')
    path.write_text(json.dumps([job, job, {'title':'invalid'}, {'id':None}, None]))
    application.state.job_provider = SeededJobProvider(path)
    response = client.get('/api/v1/jobs').json()
    assert response['total'] == 1 and response['warnings'] == ['4 malformed or duplicate demonstration records were excluded.']
    path.write_text('[]')
    assert client.get('/api/v1/jobs').json()['total'] == 0
    path.write_text('{malformed')
    response = client.get('/api/v1/jobs')
    assert response.status_code == 503 and response.json()['error']['code'] == 'jobs_unavailable'


def test_job_search_never_updates_profile_or_claims_recommendations(client, profile):
    saved = client.post('/api/v1/profile', json=profile).json()
    client.get('/api/v1/jobs?skill=Python')
    assert client.get('/api/v1/profile').json() == saved
    summary = client.get('/api/v1/dashboard/summary').json()
    assert summary['feature_states']['jobs'] == 'seeded_demo'
    assert summary['recommended_job_count'] is None


def test_posted_range_availability_and_limit(client):
    records = SeededJobProvider().snapshot().jobs
    from datetime import date
    for state in ('active','expired','future'):
        data = client.get('/api/v1/jobs?availability='+state+'&page_size=50').json()
        def matches(job):
            if state=='future': return job.posted_date > date(2026,10,19)
            if state=='expired': return job.closing_date is not None and job.closing_date < date(2026,10,19)
            return job.posted_date <= date(2026,10,19) and (job.closing_date is None or job.closing_date >= date(2026,10,19))
        assert data['total'] == sum(matches(j) for j in records)
        assert all(matches(next(j for j in records if j.id==row['id'])) for row in data['items'])
    data = client.get('/api/v1/jobs?posted_from=2026-10-01&posted_to=2026-10-15').json()
    assert data['total'] == sum(date(2026,10,1)<=j.posted_date<=date(2026,10,15) for j in records)
    data = client.get('/api/v1/jobs?max_results=8&page_size=6&page=2').json()
    assert data['matched_total'] == 10000 and data['total'] == 8 and len(data['items']) == 2
    assert client.get('/api/v1/jobs?max_results=8&page_size=6&page=3').json()['items']==[]


def test_synonyms_mult_word_skills_and_filter_options(client):
    short = client.get('/api/v1/jobs?skill=JS').json()
    long = client.get('/api/v1/jobs?skill=JavaScript').json()
    assert short['items'] == long['items'] and short['total'] == long['total'] > 0
    for skill in ('machine learning','natural language processing'):
        data = client.get('/api/v1/jobs',params={'skill':skill}).json()
        assert data['total']>0 and all(skill in job['normalized_skill_tokens'] for job in data['items'])
    options = client.get('/api/v1/jobs/options').json()
    assert options['total']==10000 and len(options['categories'])==56
    assert set(options['work_modes'])=={'remote','hybrid','onsite'}
    assert set(options['experience_levels'])=={'student','fresher','graduate','entry_level','junior','limited_experience'}
