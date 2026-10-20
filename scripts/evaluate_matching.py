"""Reproducible synthetic ranking evaluation; no keys, network, or private data."""
from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
from statistics import mean
import time

from app.core.config import Settings
from app.flows.matching import CareerMatchingFlow
from app.providers.jobs import SeededJobProvider
from app.providers.groq import GroqKeyManager
from app.services.match_normalization import fingerprint

ROOT=Path(__file__).resolve().parents[1]


def evaluate():
    fixture=json.loads((ROOT/'tests/fixtures/matching/cases.json').read_text())
    settings=Settings(_env_file=None,app_env='test')
    snapshot=SeededJobProvider().snapshot()
    jobs=[j.model_dump(mode='json') for j in snapshot.jobs]
    outputs=[];oracle_correct=oracle_total=0;reason_checks=unsupported=0
    for case in fixture['cases']:
        payload={'candidate':case['candidate'],'jobs':jobs,'user_scope':'synthetic-evaluation','minimum_score':30,'use_ai':False,'as_of':str(snapshot.reference_date)}
        def run():
            with redirect_stdout(io.StringIO()):
                return CareerMatchingFlow(payload,settings,None,GroqKeyManager((),0),123,time.monotonic()+120).kickoff()
        started=time.perf_counter();result=run();elapsed=time.perf_counter()-started
        repeat=run();rows=result['results'];relevant=set(case['relevant_categories'])
        eligible=[r for r in rows if r['hybrid_score']>=30]
        total_relevant=sum(r['job']['category'] in relevant for r in rows)
        def metrics(ordered):
            top=ordered[:10];labels=[int(r['job']['category'] in relevant) for r in top]
            dcg=sum(label/math.log2(i+2) for i,label in enumerate(labels))
            ideal=sum(1/math.log2(i+2) for i in range(min(10,total_relevant)))
            return {'precision_at_10':sum(labels)/10,'recall_at_10':sum(labels)/total_relevant if total_relevant else 0,'ndcg_at_10':dcg/ideal if ideal else 0}
        for job_id,expected in case['skill_oracle'].items():
            row=next(r for r in rows if r['job']['id']==job_id)
            for key,field in [('matched','matched_required'),('missing','missing_required')]:
                oracle_total+=1;oracle_correct+=set(expected[key])=={s['normalized'] for s in row[field]}
        for row in rows:
            catalog={r['id']:r['text'] for r in row['reasons']}
            for reason_id in row['highlighted_reason_ids']:
                reason_checks+=1;unsupported+=int(reason_id not in catalog)
            if row['explanation']!=' '.join(catalog[id] for id in row['highlighted_reason_ids']):unsupported+=1
        outputs.append({'case':case['id'],'compared':len(rows),'recommended':len(eligible),'relevant_in_corpus':total_relevant,
            'hybrid':metrics(rows),'served_hybrid':metrics(eligible),'baseline':metrics(sorted(rows,key=lambda r:(-r['baseline_score'],r['job']['id']))),
            'recommendation_recall':sum(r['job']['category'] in relevant for r in eligible)/total_relevant if total_relevant else 0,
            'seconds':round(elapsed,4),'semantic_fallback':result['provider_metadata']['embeddings']['status']!='active',
            'consistent_repeat':fingerprint(rows)==fingerprint(repeat['results']),
            'top_ids':[r['job']['id'] for r in eligible[:10]]})
    report={'dataset_version':snapshot.version,'reference_date':str(snapshot.reference_date),'jobs':len(jobs),'cases':outputs,
        'label_policy':fixture['label_policy'],'metric_policy':'Hybrid and baseline ranking metrics use all compared jobs before thresholding. served_hybrid measures thresholded output; precision@10 always divides by 10.','minimum_score':30,'provider':'none','live_embeddings_evaluated':False,'live_ai_review_evaluated':False,
        'aggregate':{'hybrid':{k:round(mean(c['hybrid'][k] for c in outputs),6) for k in ('precision_at_10','recall_at_10','ndcg_at_10')},
            'served_hybrid':{k:round(mean(c['served_hybrid'][k] for c in outputs),6) for k in ('precision_at_10','recall_at_10','ndcg_at_10')},
            'baseline':{k:round(mean(c['baseline'][k] for c in outputs),6) for k in ('precision_at_10','recall_at_10','ndcg_at_10')},
            'mean_recommendation_recall':round(mean(c['recommendation_recall'] for c in outputs),6),
            'required_missing_oracle_checks':oracle_total,'required_missing_oracle_correct':oracle_correct,'required_missing_oracle_accuracy':oracle_correct/oracle_total,
            'explanation_id_checks':reason_checks,'structural_explanation_support_rate':1-unsupported/max(1,reason_checks),'unsupported_reason_rate':unsupported/max(1,reason_checks),
            'mean_seconds':round(mean(c['seconds'] for c in outputs),4),'fallback_frequency':mean(c['semantic_fallback'] for c in outputs),'repeat_consistency':mean(c['consistent_repeat'] for c in outputs)},
        'limitations':['Category labels are synthetic domain proxies, not human hiring judgments.','Explanation support checks validate stored catalog membership and exact text, not general language truth.','Six profiles and hand-written skill oracles are a regression fixture, not a benchmark of general accuracy.','Embedding and AI review quality require separately configured live provider evaluation.']}
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    result=evaluate();body=json.dumps(result,indent=2)
    if args.output:args.output.write_text(body+'\n')
    print(body)
