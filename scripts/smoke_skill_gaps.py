"""Real subprocess/API smoke and optional real Groq Crew, using synthetic facts only."""
import argparse,json,time
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.providers.jobs import SeededJobProvider
from app.providers.groq import GroqKeyManager

ROOT=Path(__file__).resolve().parents[1]

def api_smoke():
    from app.main import create_app
    with TemporaryDirectory(prefix='skill-gap-api-smoke-') as scratch:
        settings=Settings(_env_file=None,app_env='test',data_dir=Path(scratch),document_storage_path=Path(scratch)/'private',database_url=f'sqlite:///{scratch}/api.db',session_secret='synthetic-gap-api-session-'*3)
        app=create_app(settings)
        with TestClient(app,headers={'X-Workspace-Client':'careeros-web'}) as client:
            client.post('/api/v1/session').raise_for_status()
            saved=client.post('/api/v1/profile',json={'name':'Synthetic Skill Gap Example','skills':['Python','SQL','JavaScript'],'experience_level':'student'}).json()
            client.post('/api/v1/profile/verify',json={'expected_revision':saved['revision'],'confirmed':True}).raise_for_status()
            before=client.get('/api/v1/profile').json();jobs=SeededJobProvider().snapshot().jobs
            cases=[]
            def await_run(root,id):
                deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    r=client.get(f'/api/v1/{root}/runs/{id}');r.raise_for_status();run=r.json()
                    if run['status'] in {'completed','partial','needs_review','failed','cancelled'}:return run
                    time.sleep(.15)
                raise RuntimeError('smoke_timeout')
            matching=client.post('/api/v1/matching/runs',json={'filters':{'category':'backend','location':'Bengaluru,India','work_mode':'onsite','max_results':3},'minimum_score':0});matching.raise_for_status()
            parent=await_run('matching',matching.json()['id']);assert parent['status']=='completed'
            payloads=[{'mode':'single','job_ids':[jobs[0].id]}, {'mode':'selected','job_ids':[jobs[0].id,jobs[1].id]}, {'mode':'top_ranked','matching_run_id':parent['id'],'top_k':2}, {'mode':'target','filters':{'role':'AI','location':'Bengaluru, India','work_mode':'onsite','availability':'active'},'top_k':3}, {'mode':'target','filters':{'role':'AI','location':'Atlantis'}}, {'mode':'single','job_ids':[jobs[0].id],'use_ai':True,'allow_external_processing':True}]
            for payload in payloads:
                started=time.monotonic();r=client.post('/api/v1/skill-gaps/runs',json=payload);r.raise_for_status();run=await_run('skill-gaps',r.json()['id']);assert run['status'] in {'completed','partial','needs_review'}
                result=client.get(f"/api/v1/skill-gaps/runs/{run['id']}/results");result.raise_for_status();data=result.json();audit=client.get(f"/api/v1/skill-gaps/runs/{run['id']}/audit");audit.raise_for_status()
                assert data['validation']['unsupported_claims']==0
                if data['items']:
                    gap=data['items'][0];evidence=client.get(f"/api/v1/skill-gaps/runs/{run['id']}/gaps/{gap['id']}");evidence.raise_for_status()
                    client.put(f"/api/v1/skill-gaps/runs/{run['id']}/gaps/{gap['id']}/review",json={'decision':'reviewed'}).raise_for_status()
                cases.append({'mode':payload['mode'],'request':payload,'status':run['status'],'job_count':run['job_count'],'gap_count':run['gap_count'],'seconds':round(time.monotonic()-started,4),'stages':[{'stage':t['stage'],'status':t['status']} for t in run['tasks']],'validation':data['validation'],'ai_review':run['provider_metadata']['ai_review']})
            after=client.get('/api/v1/profile').json();assert before==after
            return {'synthetic_only':True,'api_cases':cases,'confirmed_profile_unchanged':True,'real_spawned_workers':True}

def live_smoke():
    from app.orchestration.skill_gaps import execute_skill_gap
    from scripts.evaluate_skill_gaps import run_case
    from app.services.gap_taxonomy import vocabulary
    from collections import Counter
    settings=Settings();fixture=json.loads((ROOT/'tests/fixtures/skill_gaps/cases.json').read_text())['cases'][0]
    local,_=run_case(fixture,Settings(_env_file=None))
    jobs=[entry['job'] for entry in local['jobs']];required=Counter();preferred=Counter()
    from app.services.skill_taxonomy import canonical_skill
    for job in jobs:
        a={canonical_skill(s) for s in job['required_skills']};required.update(a);preferred.update({canonical_skill(s) for s in job['preferred_skills']}-a)
    counts={s:{'required':required[s],'preferred':preferred[s]} for s in set(required)|set(preferred)}
    payload={'candidate_snapshot':local['candidate_snapshot'],'jobs':jobs,'selection':local['selection'],'mode':'selected','top_k':10,'use_ai':True,'as_of':'2026-10-19','corpus_counts':counts,'corpus_count':len(jobs),'vocabulary':vocabulary(jobs)}
    manager=GroqKeyManager(settings.groq_keys,settings.groq_key_cooldown_seconds);started=time.monotonic()
    with TemporaryDirectory(prefix='synthetic-skill-gap-live-') as scratch:
        isolated=settings.model_copy(update={'data_dir':Path(scratch),'document_storage_path':Path(scratch)/'private','database_url':f'sqlite:///{scratch}/unused.db'})
        result=execute_skill_gap(payload,isolated,manager)
    return {'synthetic_only':True,'live_groq':True,'real_spawned_worker':True,'analysis_engine':'crewai.Flow','status':result['status'],'seconds':round(time.monotonic()-started,4),
        'provider_metadata':result['provider_metadata']['ai_review'],'conflicts':result['conflicts'],'stages':[{'stage':t['stage'],'status':t['status']} for t in result['tasks']],
        'validation':result['validation'],'gap_count':len(result['gaps']),'confirmed_profile_loaded':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true',help='Send only built-in synthetic gap/action catalog to Groq. No private profile/database is loaded.');parser.add_argument('--output',type=Path,default=ROOT/'docs/skill-gap-api-smoke.json');args=parser.parse_args()
    report=live_smoke() if args.live else api_smoke();args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if args.live and (report['conflicts'] or report['provider_metadata']['status']!='completed'):raise SystemExit(1)
