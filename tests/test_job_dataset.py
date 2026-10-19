from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text

from app.providers.job_dataset import DATA_PATH, DEFAULT_SEED, dataset_bytes, generate_jobs
from app.providers.jobs import SeededJobProvider
from app.schemas.jobs import Job, JobFilters
from app.services.jobs import browse_jobs
from scripts.seed_jobs import reseed_jobs
from scripts.validate_jobs import validate_dataset


@pytest.fixture(scope='module')
def generated():
    return generate_jobs()


def test_generation_reproducible_across_processes_and_hash_seeds(generated):
    blob=dataset_bytes(generated)
    assert len(generated)==1000 and DATA_PATH.read_bytes()==blob
    assert dataset_bytes(generate_jobs())==blob
    assert dataset_bytes(generate_jobs(DEFAULT_SEED+1))!=blob
    assert {j['id'] for j in generate_jobs(DEFAULT_SEED+1)}=={j['id'] for j in generated}
    code='from app.providers.job_dataset import generate_jobs,dataset_bytes; import hashlib; print(hashlib.sha256(dataset_bytes(generate_jobs())).hexdigest())'
    result=subprocess.check_output([sys.executable,'-c',code], env={**os.environ,'PYTHONHASHSEED':'12345'},text=True)
    assert result.strip()==hashlib.sha256(blob).hexdigest()


def test_schema_domains_mix_edge_cases_and_quality(generated):
    report=validate_dataset()
    assert report['records']==1000 and len(report['categories'])==46
    assert sum(report['availability'].values())==1000
    assert report['experience_levels']['limited_experience']<60
    assert set(report['work_modes'])=={'remote','hybrid','onsite'}
    assert 150<report['employment_types']['internship']<400
    assert len({j['id'] for j in generated})==1000
    assert all(set(Job.model_fields).issubset(j) for j in generated)
    groups=Counter(j['metadata']['duplicate_group'] for j in generated if j['metadata']['duplicate_group'])
    assert len(groups)==3 and all(n==2 for n in groups.values())
    assert any('JS' in j['required_skills']+j['preferred_skills'] and 'javascript' in j['normalized_skill_tokens'] for j in generated)
    assert any('natural language processing' in j['normalized_skill_tokens'] for j in generated)
    assert any(j['description']=='' for j in generated)
    assert any(not j['preferred_skills'] for j in generated)
    assert any(j['salary'] is None for j in generated)


@pytest.mark.parametrize('field,value', [('remote',True),('normalized_skill_tokens',['UNNORMALIZED']),('closing_date','1900-01-01'),('source_url','https://real-company.example/jobs/123'),('generated_data',False),('experience_years_min',4)])
def test_schema_rejects_corrupted_records(generated, field, value):
    row=deepcopy(next(row for row in generated if row['work_mode']=='onsite'))
    row[field]=value
    with pytest.raises(ValidationError): Job.model_validate(row)


def test_validator_rejects_missing_ids_fields_identical_records_and_secrets(tmp_path,generated):
    path=tmp_path/'jobs.json'
    for mutate in ('missing','id','identical','secret','size'):
        rows=deepcopy(generated)
        if mutate=='missing': del rows[0]['industry']
        elif mutate=='id': rows[1]['id']=rows[0]['id']
        elif mutate=='identical':
            ident=rows[1]['id']; rows[1]=deepcopy(rows[0]); rows[1]['id']=ident
            rows[1]['source_url']=rows[1]['application_url']=f'https://example.invalid/jobs/{ident}'
        elif mutate=='secret': rows[0]['description']='password='+'syntheticCredentialValue1234'
        else:rows.pop()
        path.write_bytes(dataset_bytes(rows))
        with pytest.raises(ValueError):validate_dataset(path,reproducible=False)


def test_atomic_reseed_cache_invalidation_and_reset(tmp_path):
    path=tmp_path/'jobs.json'
    assert reseed_jobs(path)==1000
    provider=SeededJobProvider(path)
    first=provider.snapshot()
    assert provider.snapshot() is first
    provider.reset()
    assert provider.snapshot()==first and provider.snapshot() is not first
    assert reseed_jobs(path,DEFAULT_SEED+1)==1000
    assert provider.snapshot()!=first
    assert len(provider.snapshot().jobs)==1000
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots=list(pool.map(lambda _:provider.snapshot(),range(12)))
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert not list(tmp_path.glob('.jobs-*.tmp'))
    path.write_text('{broken')
    assert reseed_jobs(path)==1000 and validate_dataset(path)['records']==1000


def test_reseed_refuses_non_job_files(tmp_path):
    path=tmp_path/'profiles.json';path.write_text('[{"name":"Saved Student"}]')
    before=path.read_bytes()
    with pytest.raises(ValueError):reseed_jobs(path)
    assert path.read_bytes()==before
    with pytest.raises(ValueError):reseed_jobs(tmp_path/'workspace.db')


def test_fixture_seeding_never_modifies_workspace_database(tmp_path,client,application,profile):
    # JSON is the selected persistence strategy. No redundant SQL job table/seeder.
    from test_document_processing import upload, finish
    client.post('/api/v1/profile',json=profile)
    doc=upload(client);finish(client,doc)
    tables=inspect(application.state.engine).get_table_names()
    def database_snapshot():
        with application.state.engine.connect() as connection:
            return {name: sorted(map(repr,connection.execute(text(f'SELECT * FROM "{name}"')).fetchall())) for name in tables}
    before=database_snapshot()
    path=tmp_path/'seeded'/'jobs.json'
    reseed_jobs(path);reseed_jobs(path,DEFAULT_SEED+1)
    provider=SeededJobProvider(path);provider.reset();provider.snapshot()
    assert database_snapshot()==before
    assert not any('job' in name for name in tables)


def test_warm_search_performance_and_concurrent_reads():
    provider=SeededJobProvider()
    filters=JobFilters(keyword='python',page_size=50)
    browse_jobs(provider,filters)  # Cold validation/cache warmup is measured separately by CLI.
    durations=[]
    for _ in range(10):
        start=time.perf_counter();result=browse_jobs(provider,filters);durations.append(time.perf_counter()-start)
        assert len(result.items)<=50 and result.total>0
    assert statistics.median(durations)<0.25
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages=list(pool.map(lambda _:browse_jobs(provider,filters).model_dump(),range(12)))
    assert all(page==pages[0] for page in pages)


def test_controlled_evaluation_cases_reference_real_jobs_and_factual_properties(generated):
    from app.providers.job_evaluation import evaluation_cases
    from app.services.job_normalization import normalized_skills
    from app.services.jobs import availability
    from app.providers.job_dataset import REFERENCE_DATE
    manifest=evaluation_cases(generated)
    by_id={row['id']:Job.model_validate(row) for row in generated}
    assert len(manifest['cases'])==16
    for case in manifest['cases']:
        jobs=[by_id[ident] for ident in case['job_ids']]
        assert jobs
        candidate=case['candidate'];checks=case['checks'];job=jobs[0]
        skills=set(normalized_skills(candidate.get('skills',[])))
        if 'required_skill_overlap' in checks:
            assert len(skills & set(normalized_skills(job.required_skills)))==checks['required_skill_overlap']
        if 'preferred_skill_overlap' in checks:
            assert len(skills & set(normalized_skills(job.preferred_skills)))==checks['preferred_skill_overlap']
        if 'canonical_overlap' in checks:
            assert set(checks['canonical_overlap']).issubset(skills & set(job.normalized_skill_tokens))
        if 'above_posted_experience_range' in checks:assert candidate['years']>job.experience_years_max
        if 'below_posted_experience_range' in checks:assert candidate['years']<job.experience_years_min
        if 'location_match' in checks:assert (candidate['location']==job.location)==checks['location_match']
        if 'work_mode_match' in checks:assert (candidate['work_mode']==job.work_mode)==checks['work_mode_match']
        if 'employment_type_match' in checks:assert candidate['employment_type']==job.employment_type
        if 'category_match' in checks:assert candidate['category']==job.category
        if 'availability' in checks:assert availability(job,REFERENCE_DATE)==checks['availability']
        if 'shared_duplicate_group' in checks:assert len({j.id for j in jobs})==2 and len({j.metadata.duplicate_group for j in jobs})==1
