"""Exercise the real spawned matching worker with public, synthetic fixture data."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import Settings
from app.db.connection import make_engine,initialize_database
from app.orchestration.matching import execute_matching
from app.providers.groq import GroqKeyManager
from app.providers.jobs import SeededJobProvider


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true',help='Send synthetic reason catalogs to Groq. No user profile or resume is loaded.')
    args=parser.parse_args()
    with TemporaryDirectory(prefix='synthetic-matching-smoke-') as scratch:
        settings=Settings(data_dir=Path(scratch),document_storage_path=Path(scratch)/'private',database_url=f'sqlite:///{scratch}/smoke.db',matching_ai_top_k=3)
        if not args.live:
            settings=Settings(_env_file=None,data_dir=Path(scratch),document_storage_path=Path(scratch)/'private',database_url=f'sqlite:///{scratch}/smoke.db')
        engine=make_engine(settings.database_url);initialize_database(engine);engine.dispose()
        fixture=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/matching/cases.json').read_text())['cases'][0]
        snapshot=SeededJobProvider().snapshot()
        payload={'candidate':fixture['candidate'],'jobs':[j.model_dump(mode='json') for j in snapshot.jobs],
            'user_scope':'synthetic-smoke','minimum_score':30,'use_ai':args.live,'as_of':str(snapshot.reference_date)}
        stages=[]
        result=execute_matching(payload,settings,GroqKeyManager(settings.groq_keys,settings.groq_key_cooldown_seconds),lambda data:stages.append(data['stage']) if data else None)
        summary={'synthetic_only':True,'status':result['status'],'jobs_scored':len(result['results']),'stages':stages,'providers':result['provider_metadata'],
            'validation':result['validation'],'ai_selected_explanations':sum(r['explanation_method']=='crewai_selected_grounded_reasons' for r in result['results'])}
        print(json.dumps(summary,indent=2))
        if args.live and result['provider_metadata'].get('ai_review',{}).get('status')!='completed':raise SystemExit(2)


if __name__=='__main__':main()
