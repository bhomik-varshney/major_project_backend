"""HTTP smoke against an isolated local server, using a new synthetic session only."""
import argparse
import json
from pathlib import Path
from statistics import median
import time
from urllib.parse import urlparse

import httpx


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://localhost:8001/api/v1');parser.add_argument('--output',type=Path);args=parser.parse_args()
    if urlparse(args.base).hostname not in {'localhost','127.0.0.1'}:parser.error('Use an isolated loopback test backend.')
    with httpx.Client(base_url=args.base+'/',headers={'X-Workspace-Client':'careeros-web'},timeout=30) as client:
        def get(path,**kwargs):
            response=client.get(path,**kwargs);response.raise_for_status();return response.json()
        assert client.post('session').status_code==200
        options=get('jobs/options');assert options['total']==10000
        last=get('jobs',params={'page':200,'page_size':50});assert len(last['items'])==50
        for state in ('active','expired','future'):
            assert get('jobs',params={'availability':state})['total']>0
        durations=[]
        for _ in range(5):
            started=time.perf_counter();get('jobs',params={'keyword':'Python','page_size':50});durations.append(time.perf_counter()-started)
        profile={'name':'Synthetic API Smoke','skills':['Python','SQL','FastAPI','Docker','RESTful API','Postgres'],'preferred_roles':['Backend Developer'],'preferred_locations':['Remote'],'experience_level':'graduate'}
        saved=client.post('profile',json=profile);saved.raise_for_status()
        verified=client.post('profile/verify',json={'expected_revision':saved.json()['revision'],'confirmed':True});verified.raise_for_status();original=get('profile')
        started=time.perf_counter();response=client.post('matching/runs',json={'minimum_score':30});assert response.status_code==202
        run_id=response.json()['id'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            run=get('matching/runs/'+run_id)
            if run['status'] in {'completed','partial','failed','cancelled'}:break
            time.sleep(.2)
        assert run['status']=='completed',run['error_code']
        page=get('matching/runs/'+run_id+'/results');assert page['total']>0
        first=page['items'][0];assert first['match_percentage'] is not None and first['method_scores']['bm25'] is not None
        audit=get('matching/runs/'+run_id+'/audit');retrieval=audit['retrieval']
        assert retrieval['corpus_size']>8000 and retrieval['retrieved_count']<=1200
        assert len(retrieval['excluded_jobs'])==retrieval['excluded_count']
        assert 'excluded_jobs' not in run['validation']['retrieval']
        detail=get('matching/runs/'+run_id+'/results/'+first['job']['id']);assert detail['normalized_job']['required']
        assert client.put('matching/bookmarks/'+first['job']['id']).status_code==200
        assert get('matching/runs/'+run_id+'/results',params={'saved_only':'true'})['total']==1
        assert client.delete('matching/bookmarks/'+first['job']['id']).status_code==204
        assert get('profile')==original
        report={'synthetic_only':True,'status':run['status'],'records':options['total'],'categories':len(options['categories']),'last_page_items':len(last['items']),'retrieval_corpus':retrieval['corpus_size'],'retrieved':retrieval['retrieved_count'],'excluded':retrieval['excluded_count'],'recommended_above_30':page['total'],'run_seconds':round(time.perf_counter()-started,4),'warm_search_median_ms':round(median(durations)*1000,3),'profile_unchanged':True,'all_stages':[t['status'] for t in run['tasks']],'score_errors':run['validation']['score_errors'],'calibration':first['calibration']['status'],'audit_exclusions_present':True,'summary_payload_exclusions_omitted':True}
        assert client.delete('matching/runs/'+run_id).status_code==204
        if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))

if __name__=='__main__':main()
