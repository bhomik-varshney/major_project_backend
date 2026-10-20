"""Reproducible public-fixture coverage and structured query audit; no private profiles."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from app.providers.jobs import SeededJobProvider
from app.providers.job_dataset import DATA_PATH
from app.schemas.jobs import JobFilters
from app.services.job_locations import parse_location
from app.services.job_roles import AI_CATEGORIES,role_match
from app.services.jobs import filter_jobs_with_audit,availability
from app.services.match_normalization import deduplicate


def audit():
    started=time.perf_counter();snapshot=SeededJobProvider().snapshot();jobs=snapshot.jobs
    distributions={field:dict(sorted(Counter(str(getattr(j,field)) for j in jobs).items())) for field in ('category','title','location','country','work_mode','experience_level','employment_type')}
    terms=['AI','Artificial Intelligence','Machine Learning','ML','Generative AI','Deep Learning','NLP','Computer Vision','Bengaluru','Bangalore','Bengaluru, India','Bangalore, India','Karnataka','Remote']
    keyword_counts={term:len(filter_jobs_with_audit(snapshot,JobFilters(keyword=term))[0]) for term in terms}
    location_counts={term:len(filter_jobs_with_audit(snapshot,JobFilters(location=term))[0]) for term in terms[8:]}
    params={'location':'Bengaluru,India','category':'artificial_intelligence','work_mode':'onsite','availability':'active'}
    selected,trace=filter_jobs_with_audit(snapshot,JobFilters(**params),True)
    # SQL staging mirrors the provider because production has no SQL job table.
    db=sqlite3.connect(':memory:');db.execute('create table fixture(city text,state text,country text,mode text,ai integer,status text)')
    db.executemany('insert into fixture values(?,?,?,?,?,?)',[(parse_location(j.location,j.country,j.work_mode)['city'],parse_location(j.location,j.country,j.work_mode)['state'],j.country,j.work_mode,int(role_match('AI',j)['accepted']),availability(j,snapshot.reference_date)) for j in jobs])
    query="select count(*) from fixture where city='Bengaluru' and country='India' and mode='onsite' and ai=1 and status='active'"
    sql_count=db.execute(query).fetchone()[0];db.close();assert sql_count==len(selected)
    skill_pattern=re.compile(r'\b(?:machine learning|natural language processing|computer vision|deep learning|llms?|ml|nlp|responsible ai)\b',re.I)
    return {'records':len(jobs),'dataset_version':snapshot.version,'dataset_sha256':hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),'reference_date':str(snapshot.reference_date),'distributions':distributions,
        'ai_title_count':sum(role_match('AI',{'title':j.title,'category':'','required_skills':[],'preferred_skills':[]})['accepted'] for j in jobs),
        'ai_category_count':sum(j.category in AI_CATEGORIES for j in jobs),'ai_skill_count':sum(any(skill_pattern.search(s) for s in j.required_skills+j.preferred_skills) for j in jobs),
        'raw_location_variants':{term:sum(j.location.casefold()==term.casefold() for j in jobs) for term in ('Bengaluru','Bangalore','Bengaluru, India','Bangalore, India','Bengaluru, Karnataka','Bangalore, Karnataka','Karnataka, India')},
        'structured_city_distribution':dict(Counter(parse_location(j.location,j.country,j.work_mode)['city'] or 'not_listed' for j in jobs)),
        'structured_state_distribution':dict(Counter(parse_location(j.location,j.country,j.work_mode)['state'] or 'not_listed' for j in jobs)),
        'india_remote':sum(j.remote and j.country=='India' for j in jobs),'missing_location':sum(not j.location for j in jobs),'missing_category':sum(not j.category for j in jobs),'empty_description':sum(not j.description for j in jobs),'empty_required_skills':sum(not j.required_skills for j in jobs),'empty_all_skills':sum(not(j.required_skills+j.preferred_skills) for j in jobs),
        'availability':dict(Counter(availability(j,snapshot.reference_date) for j in jobs)),'excluded_by_default_active_dates':sum(availability(j,snapshot.reference_date)!='active' for j in jobs),'duplicate_like_tagged':sum('duplicate_like' in j.metadata.edge_cases for j in jobs),'deduplicated_records':len(deduplicate(jobs)[0]),'duplicates_excluded':len(deduplicate(jobs)[1]),
        'keyword_equivalents':keyword_counts,'location_equivalents':location_counts,'exact_query':{'parameters':params,'count':len(selected),'trace':{k:v for k,v in trace.items() if k!='excluded_jobs'}},'diagnostic_sql':{'production_job_table':False,'staging_query':query,'result':sql_count},'seconds':round(time.perf_counter()-started,4)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('docs/ai-location-dataset-after.json'));args=parser.parse_args();result=audit();args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ('distributions','exact_query')},indent=2))
