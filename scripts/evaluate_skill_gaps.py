"""Evaluate genuine skill-gap Flow against authored, non-private oracle fixtures."""
import argparse
from copy import deepcopy
from collections import Counter
import json,time
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout,redirect_stderr
from statistics import mean
from app.core.config import Settings
from app.providers.groq import GroqKeyManager
from app.providers.jobs import SeededJobProvider
from app.schemas.jobs import Job
from app.services.job_normalization import normalized_skills
from app.services.skill_taxonomy import canonical_skill
from app.services.gap_taxonomy import vocabulary

ROOT=Path(__file__).resolve().parents[1]

def run_case(case,settings,use_ai=False):
    from app.flows.skill_gaps import SkillGapFlow
    base=SeededJobProvider().snapshot().jobs[0].model_dump(mode='json');jobs=[]
    for i,changes in enumerate(case['jobs']):
        raw=deepcopy(base);raw.update(changes);raw['id']=f"demo-gap-eval-{case['id'].replace('_','-')}-{i}"
        raw['source_url']=raw['application_url']='https://example.invalid/jobs/'+raw['id']
        raw['normalized_skill_tokens']=normalized_skills(raw['required_skills']+raw['preferred_skills']);raw['metadata']['duplicate_group']=None
        jobs.append(Job.model_validate(raw))
    req=Counter();pref=Counter()
    for job in jobs:
        a={canonical_skill(s) for s in job.required_skills};req.update(a);pref.update({canonical_skill(s) for s in job.preferred_skills}-a)
    counts={s:{'required':req[s],'preferred':pref[s]} for s in set(req)|set(pref)}
    payload={'candidate_snapshot':{'facts':deepcopy(case['candidate']),'proofs':{},'profile_source':'confirmed_fields_only','draft_data_used':False},'jobs':[j.model_dump(mode='json') for j in jobs],
        'selection':{'evaluation_case':case['id'],'target_role':case['target_role']},'mode':'selected','top_k':10,'as_of':'2026-10-19','corpus_counts':counts,'corpus_count':len(jobs),'vocabulary':vocabulary(jobs),'use_ai':use_ai}
    start=time.perf_counter();result=SkillGapFlow(payload,settings,GroqKeyManager([]),10,time.monotonic()+60).kickoff()
    return result,round(time.perf_counter()-start,6)

def evaluate():
    fixture=json.loads((ROOT/'tests/fixtures/skill_gaps/cases.json').read_text());settings=Settings(_env_file=None)
    checks=correct=types=type_correct=duplicates=unsupported=evidence=priority_checks=priority_correct=explanations=explanation_correct=0;rows=[];latencies=[]
    for case in fixture['cases']:
        before=deepcopy(case['candidate']);result,seconds=run_case(case,settings);repeat,_=run_case(case,settings);latencies.append(seconds)
        for comparison in result['comparisons']:
            checks+=1;correct+=comparison['classification']==case['expected'][comparison['normalized']]
            requirements=case['jobs'][int(comparison['job_id'].rsplit('-',1)[1])]
            key=comparison['normalized'];required={canonical_skill(s) for s in requirements['required_skills']};preferred={canonical_skill(s) for s in requirements['preferred_skills']}-required
            expected_type='unknown' if key=='__requirements_unknown__' else 'required' if key in required else 'preferred' if key in preferred else 'optional'
            types+=1;type_correct+=comparison['requirement']['type']==expected_type
            if comparison['counts_as_requirement_match']:
                evidence+=1
                assert comparison['candidate_evidence'] and comparison['relationship'] in {'exact','synonym'}
        gaps={g['normalized']:g for g in result['gaps']};duplicates+=len(result['gaps'])-len(gaps);unsupported+=result['validation']['unsupported_claims']
        for gap in result['gaps']:
            explanations+=1;explanation_correct+=bool(gap['explanation'] and gap['affected_count']==len(gap['affected_jobs']) and all(a['evidence_job_ids']==gap['affected_jobs'] for a in gap['guidance']))
        first=[(g['normalized'],g['priority'],g['priority_score']) for g in result['gaps']];again=[(g['normalized'],g['priority'],g['priority_score']) for g in repeat['gaps']]
        priority_checks+=1;priority_correct+=first==again
        if case.get('higher_priority'):
            high,low=case['higher_priority'];priority_checks+=1;priority_correct+=gaps[high]['priority_score']>gaps[low]['priority_score']
        assert case['candidate']==before
        rows.append({'case':case['id'],'target_role':case['target_role'],'jobs':len(case['jobs']),'status':result['status'],'gaps':len(gaps),'seconds':seconds,'classifications':[{k:c[k] for k in ('normalized','classification')} for c in result['comparisons']],'priority_order':first})
    fallback,_=run_case(fixture['cases'][0],settings,use_ai=True)
    return {'synthetic_only':True,'cases':rows,'metrics':{'gap_classification_accuracy':correct/checks,'classification_checks':checks,'required_preferred_optional_classification_accuracy':type_correct/types,'requirement_checks':types,'unsupported_claim_rate':unsupported/max(1,checks),'evidence_support_rate':1 if evidence else None,'matched_evidence_checks':evidence,'priority_consistency':priority_correct/priority_checks,'duplicate_gap_rate':duplicates/max(1,explanations),'explanation_grounding_accuracy':explanation_correct/explanations,'explanation_checks':explanations,'mean_flow_seconds':mean(latencies),'max_flow_seconds':max(latencies),'controlled_provider_failure_fallback_frequency':1 if fallback['status']=='partial' and fallback['gaps'] else 0},'limitations':['Authored synthetic fixtures, not general real-resume accuracy.','Explanation grounding checks validate references/counts, not human-rated learning quality.','Priority consistency is determinism plus one authored relative-frequency judgment.','Warm local Flow latency excludes HTTP, subprocess startup and external model latency.','Fallback frequency is a deliberate no-credentials failure test, not a production failure estimate.']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'docs/skill-gap-evaluation.json');args=parser.parse_args()
    with TemporaryDirectory(prefix='skill-gap-eval-') as scratch:
        import os
        os.environ['CREWAI_STORAGE_DIR']=scratch
        with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):report=evaluate()
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['metrics'],indent=2))
    if any(report['metrics'][k]!=1 for k in ['gap_classification_accuracy','required_preferred_optional_classification_accuracy','priority_consistency','explanation_grounding_accuracy']):raise SystemExit(1)
