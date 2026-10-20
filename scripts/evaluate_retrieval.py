"""Whole-corpus method comparison and disjoint authored challenge-pair calibration.
No model downloads, keys, private profiles, or external requests.
"""
import argparse
from collections import Counter
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
from pathlib import Path
from statistics import mean
import time

from app.core.config import Settings
from app.providers.jobs import SeededJobProvider
from app.providers.groq import GroqKeyManager
from app.services.match_normalization import normalize_candidate,normalize_job,deduplicate
from app.services.match_baseline import TfidfBaseline
from app.services.match_retrieval import BM25Strategy,SkillStrategy,WordVectorStrategy,retrieve
from app.services.match_scoring import score_job,validate_ranking
from app.services.match_calibration import ARTIFACT,fit_isotonic,calibrate,artifact

ROOT=Path(__file__).resolve().parents[1]


def metrics(order,relevant,k=10):
    labels=[int(i in relevant) for i in order[:k]]
    dcg=sum(x/math.log2(n+2) for n,x in enumerate(labels));ideal=sum(1/math.log2(n+2) for n in range(min(k,len(relevant))))
    return {'precision_at_10':sum(labels)/k,'recall_at_10':sum(labels)/len(relevant) if relevant else 0,'hit_rate':int(any(labels)),'mrr':next((1/(n+1) for n,x in enumerate(labels) if x),0),'ndcg_at_10':dcg/ideal if ideal else 0}


def observations():
    # Authored concrete judgments, independent of scores. Disjoint candidate
    # families for train/holdout, plus a dedicated mismatch/OOV challenge suite.
    rows=[]
    families=[
      ('train','python_backend',['Python','SQL','FastAPI','REST APIs','Docker','PostgreSQL'],['Backend Developer'],'backend'),
      ('train','react_frontend',['JavaScript','React','HTML','CSS','TypeScript','Accessibility'],['Frontend Developer'],'frontend'),
      ('train','analyst',['SQL','Excel','Statistics','Data analysis','Power BI'],['Data Analyst'],'data_analysis'),
      ('train','designer',['Figma','User research','Wireframing','Accessibility','Interaction design'],['UI/UX Designer'],'ui_ux'),
      ('holdout','java_backend',['Java','Spring Boot','SQL','REST APIs','Git'],['Backend Developer'],'backend'),
      ('holdout','ml_research',['Python','Machine learning','Pandas','Model evaluation','Statistics'],['Machine Learning Engineer'],'machine_learning'),
      ('holdout','security',['Linux','Networking','Threat analysis','Incident response','Security monitoring'],['Security Analyst'],'cybersecurity'),
      ('holdout','mobile',['Kotlin','Android','REST APIs','Git','Unit testing'],['Mobile Developer'],'mobile'),
    ]
    from app.providers.job_catalog import ROLES
    # Explicit relevance decisions apply to coherent role recipes, not generated text scores.
    for split,case,skills,roles,category in families:
        primary=next(r for r in ROLES if r.category==category and (r.skills[0] in skills))
        candidates=[(primary,1,'exact_role_and_foundations')]
        candidates.extend((r,0,'unrelated_role_shared_keywords') for r in ROLES[:46] if r.category not in {category} and len(set(skills)&set(r.skills))<=1)
        candidates=candidates[:13]
        for index,(recipe,label,reason) in enumerate(candidates):
            rows.append({'split':split,'case':case,'candidate':{'skills':skills,'preferred_roles':roles,'experience_level':'graduate','preferred_locations':['Remote']},'category':recipe.category,'title':recipe.title,'required':list(recipe.skills[:3]),'preferred':list(recipe.skills[3:]),'description':recipe.task,'work_mode':'remote','experience_min':0,'education':[],'relevant':label,'judgment':reason})
        # Strong exact role matches at different explicit skill depths.
        for required_count in (2,4,5):
            req=list(primary.skills[:required_count]);coverage=len(set(skills)&set(req))/len(req)
            rows.append({'split':split,'case':case,'candidate':{'skills':skills,'preferred_roles':roles,'experience_level':'graduate','preferred_locations':['Remote']},'category':category,'title':primary.title,'required':req,'preferred':[],'description':primary.task,'work_mode':'remote','experience_min':0,'education':[],'relevant':int(coverage>=.6),'judgment':'authored_role_and_at_least_60_percent_foundations'})
        # Explicit constraints establish negative suitability despite matching keywords.
        rows.append({'split':split,'case':case,'candidate':{'skills':skills,'preferred_roles':roles,'experience_level':'student','preferred_locations':['Remote']},'category':category,'title':primary.title,'required':list(primary.skills),'preferred':[],'description':primary.task,'work_mode':'onsite','experience_min':3,'education':['Master degree'],'relevant':0,'judgment':'explicit_remote_and_experience_mismatch'})
    return rows


def pair_scores(pairs,settings):
    from app.schemas.jobs import Job
    from app.services.job_normalization import normalized_skills
    snapshot=SeededJobProvider().snapshot();base=snapshot.jobs[0].model_dump(mode='json');out=[]
    materialized=[]
    from app.schemas.jobs import Job
    from app.services.job_normalization import normalized_skills
    for n,pair in enumerate(pairs):
        candidate=normalize_candidate(pair['candidate']);raw=json.loads(json.dumps(base));ident=f'demo-evaluation-{n}'
        raw.update(id=ident,title=pair['title'],category=pair['category'],description=pair['description'],required_skills=pair['required'],preferred_skills=pair['preferred'],work_mode=pair['work_mode'],remote=pair['work_mode']=='remote',experience_level='limited_experience' if pair['experience_min'] else 'graduate',experience_years_min=pair['experience_min'],experience_years_max=3,education_requirements=pair['education'],application_url=f'https://example.invalid/jobs/{ident}',source_url=f'https://example.invalid/jobs/{ident}')
        raw['metadata']['category']=pair['category'];raw['metadata']['duplicate_group']=None;raw['normalized_skill_tokens']=normalized_skills(pair['required']+pair['preferred'])
        materialized.append((candidate,normalize_job(Job.model_validate(raw)),pair['relevant']))
    # Use corpus-sized IDF rather than fitting two-document cosines: production
    # similarity depends on the background corpus as well as the pair's words.
    model=TfidfBaseline([normalize_job(j)['text'] for j in snapshot.jobs]+[j['text'] for _,j,_ in materialized])
    for candidate,job,label in materialized:
        lex=model.compare(candidate['text'],[job['text']])[0]
        result=score_job(candidate,job,lex,0,lex,None,settings,snapshot.reference_date)
        out.append((result.hybrid_score,label))
    return out


def compare_authored_pairs(pairs,settings):
    from app.schemas.jobs import Job
    from app.services.job_normalization import normalized_skills
    snapshot=SeededJobProvider().snapshot();base=snapshot.jobs[0].model_dump(mode='json');reports=[]
    for case in sorted({p['case'] for p in pairs}):
        group=[p for p in pairs if p['case']==case];candidate=normalize_candidate(group[0]['candidate']);jobs=[]
        for n,pair in enumerate(group):
            raw=json.loads(json.dumps(base));ident=f'demo-pair-{n}'
            raw.update(id=ident,title=pair['title'],category=pair['category'],description=pair['description'],required_skills=pair['required'],preferred_skills=pair['preferred'],work_mode=pair['work_mode'],remote=pair['work_mode']=='remote',experience_years_min=pair['experience_min'],experience_years_max=3,education_requirements=pair['education'],application_url=f'https://example.invalid/jobs/{ident}',source_url=f'https://example.invalid/jobs/{ident}')
            raw['metadata']['category']=pair['category'];raw['metadata']['duplicate_group']=None;raw['normalized_skill_tokens']=normalized_skills(pair['required']+pair['preferred'])
            jobs.append(normalize_job(Job.model_validate(raw)))
        model=TfidfBaseline([candidate['text']]+[j['text'] for j in jobs]);tfidf=model.compare(candidate['text'],[j['text'] for j in jobs]);bm25=BM25Strategy().score(candidate,jobs).scores;skills=SkillStrategy().score(candidate,jobs).scores
        hybrid=[];hybrid_bm25=[]
        for i,job in enumerate(jobs):
            # Pair-level candidate preference changes belong to that candidate snapshot.
            confirmed=candidate
            hybrid.append(score_job(confirmed,job,tfidf[i],0,tfidf[i],None,settings,snapshot.reference_date).hybrid_score)
            hybrid_bm25.append(score_job(confirmed,job,tfidf[i],0,tfidf[i],None,settings,snapshot.reference_date,lexical_score=bm25[i],lexical_name='bm25').hybrid_score)
        relevant={i for i,p in enumerate(group) if p['relevant']};methods={}
        for name,values in [('tfidf',tfidf),('bm25',bm25),('skill_overlap',skills),('hybrid',hybrid),('hybrid_bm25',hybrid_bm25)]:
            order=sorted(range(len(jobs)),key=lambda i:(-values[i],jobs[i]['original']['id']))
            # Precision@K has a fixed denominator, including when fewer than K relevant pairs exist.
            methods[name]={**metrics(order,relevant),'precision_at_3':sum(i in relevant for i in order[:3])/3,'ndcg_at_3':metrics(order,relevant,k=3)['ndcg_at_10'],'mean_relevant_score':mean(values[i] for i in relevant),'mean_irrelevant_score':mean(values[i] for i in range(len(jobs)) if i not in relevant)}
        reports.append({'case':case,'judgments':len(group),'positives':len(relevant),'methods':methods})
    return {'cases':reports,'aggregate':{m:{key:round(mean(r['methods'][m][key] for r in reports),6) for key in ('precision_at_3','ndcg_at_3','precision_at_10','ndcg_at_10','mrr','mean_relevant_score','mean_irrelevant_score')} for m in reports[0]['methods']},'policy':'Authored holdout pairs, candidate families disjoint from calibration training; role/skill/remote/experience judgments. Broad corpus metrics reported separately.'}


def evaluate(output,fit=False):
    settings=Settings(_env_file=None,app_env='test');snapshot=SeededJobProvider().snapshot();unique,duplicates=deduplicate(snapshot.jobs);jobs=[normalize_job(j) for j in unique]
    fixture=json.loads((ROOT/'tests/fixtures/matching/cases.json').read_text());cases=[];checks=correct=reason_checks=unsupported=0
    for case in fixture['cases']:
        started=time.perf_counter();candidate=normalize_candidate(case['candidate']);baseline=TfidfBaseline([candidate['text']]+[j['text'] for j in jobs]);tfidf=baseline.compare(candidate['text'],[j['text'] for j in jobs])
        selected,methods,retrieval=retrieve(candidate,jobs,tfidf,pool_size=settings.matching_retrieval_pool_size)
        # Exhaustive reranking is the recall oracle; production only reranks selected.
        rows=[];qproject=baseline.vector(candidate['project_experience_text']);qinterest=baseline.vector(candidate['interests_text'])
        for i,job in enumerate(jobs):
            row=score_job(candidate,job,tfidf[i],baseline.similarity(qproject,baseline.vector(job['project_text'])),baseline.similarity(qinterest,baseline.vector(job['text'])),None,settings,snapshot.reference_date)
            rows.append(row)
        relevant={i for i,j in enumerate(jobs) if j['original']['category'] in case['relevant_categories']}
        orders={m.name:sorted(range(len(jobs)),key=lambda i:(-m.scores[i],jobs[i]['original']['id'])) if m.scores is not None else None for m in methods}
        hybrid=sorted(range(len(jobs)),key=lambda i:(-rows[i].hybrid_score,-tfidf[i],rows[i].job.id));pool=set(selected);served=[i for i in hybrid if i in pool and rows[i].hybrid_score>=30]
        orders['hybrid_exhaustive']=hybrid;orders['hybrid']=served
        for row in rows:
            catalog={r.id:r.text for r in row.reasons}
            reason_checks+=len(row.highlighted_reason_ids)
            unsupported+=sum(r not in catalog for r in row.highlighted_reason_ids)
        for id,expected in case['skill_oracle'].items():
            row=next(r for r in rows if r.job.id==id)
            for key,field in [('matched','matched_required'),('missing','missing_required')]:checks+=1;correct+=set(expected[key])=={s['normalized'] for s in getattr(row,field)}
        cases.append({'case':case['id'],'corpus':len(jobs),'retrieved':len(selected),'relevant_in_corpus':len(relevant),'methods':{m:metrics(order,relevant) if order is not None else {'status':'unavailable'} for m,order in orders.items()},'recall_of_exhaustive_top_10':len(set(hybrid[:10])&pool)/10,'relevant_retrieval_recall':len(relevant&pool)/len(relevant),'above_threshold':{str(t):sum(r.hybrid_score>=t for r in rows) for t in (20,30,40,50,60,70,80)},'mean_relevant_score':mean(rows[i].hybrid_score for i in relevant),'mean_irrelevant_score':mean(rows[i].hybrid_score for i in range(len(rows)) if i not in relevant),'seconds_exhaustive':round(time.perf_counter()-started,4)})
    pairs=observations();train=[p for p in pairs if p['split']=='train'];test=[p for p in pairs if p['split']=='holdout'];train_values=pair_scores(train,settings);test_values=pair_scores(test,settings)
    if fit:
        ARTIFACT.write_text(json.dumps({'version':'synthetic-isotonic-v1','training_pairs':len(train),'score_config':{'weights':settings.matching_weights,'critical_penalty':settings.matching_critical_penalty,'unknown_ceiling_base':settings.matching_unknown_ceiling_base,'lexical_strategy':'tfidf','semantic':False},'training_sha256':hashlib.sha256(json.dumps(train,sort_keys=True).encode()).hexdigest(),'bins':fit_isotonic(train_values),'policy':'Fixed-width raw-score bins, Laplace smoothing, weighted monotonic PAVA. Disjoint candidate families held out. Experimental synthetic relevance frequency, not hiring probability.'},indent=2)+'\n');artifact.cache_clear()
        (ROOT/'tests/fixtures/matching/calibration-pairs.json').write_text(json.dumps(pairs,indent=2)+'\n')
    probabilities=[calibrate(s)[0] for s,_ in test_values]
    brier=mean((p/100-y)**2 for p,(_,y) in zip(probabilities,test_values)) if all(p is not None for p in probabilities) else None
    report={'dataset':snapshot.version,'records':len(snapshot.jobs),'deduplicated':len(jobs),'duplicates_removed':len(duplicates),'minimum_raw_score':30,'pool_size':settings.matching_retrieval_pool_size,'cases':cases,
      'aggregate':{m:{k:round(mean(c['methods'][m][k] for c in cases),6) for k in ('precision_at_10','recall_at_10','hit_rate','mrr','ndcg_at_10')} for m in ('tfidf','bm25','skill_overlap','hybrid_exhaustive','hybrid')},
      'mean_top10_retrieval_recall':mean(c['recall_of_exhaustive_top_10'] for c in cases),'required_missing_oracle_accuracy':correct/checks,'required_missing_oracle_checks':checks,'explanation_support_rate':1-unsupported/max(1,reason_checks),'unsupported_reason_rate':unsupported/max(1,reason_checks),'explanation_checks':reason_checks,
      'calibration':{'train_pairs':len(train),'holdout_pairs':len(test),'holdout_brier':brier,'raw_score_brier':mean((s/100-y)**2 for s,y in test_values),'train_positive':sum(y for _,y in train_values),'holdout_positive':sum(y for _,y in test_values),'artifact':artifact()},
      'authored_holdout_comparison':compare_authored_pairs(test,settings),
      'availability':{'embedding':'not_configured; no live semantic-quality claim','word2vec':'no suitable pretrained local model; not used for ranking'},
      'limitations':['Career-category relevance proxies are broad and do not establish employer suitability.','Calibration uses authored synthetic examples and disjoint candidate families; needs a substantially larger human-labeled benchmark.','Recall@10 decreases with a larger relevant corpus; compare precision and top10 retrieval recall as well.','No live embeddings or word-vector quality evaluation is possible without suitable configured artifacts.']}
    report['selected_method']='hybrid with TF-IDF lexical feature; BM25 and skill postings contribute retrieval only; no unavailable semantic features are scored'
    output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'aggregate':report['aggregate'],'top10_recall':report['mean_top10_retrieval_recall'],'calibration_brier':brier},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'docs/retrieval-evaluation.json');parser.add_argument('--fit-calibration',action='store_true');args=parser.parse_args();evaluate(args.output,args.fit_calibration)
