"""Frozen monotonic empirical calibration, trained only on authored synthetic pairs.

The displayed percentage estimates the synthetic relevance label frequency.
It is experimental and is neither hiring probability nor semantic similarity.
Ranking and minimum thresholds continue to use the transparent raw hybrid score.
"""
from functools import lru_cache
import json
from pathlib import Path

ARTIFACT=Path(__file__).parents[1]/'providers/data/match_calibration.json'


def fit_isotonic(observations):
    # Aggregate exact ties before weighted pool-adjacent-violators. Laplace
    # smoothing prevents tiny synthetic groups claiming absolute certainty.
    grouped={}
    for score,label in observations:
        bucket=min(4,int(max(0,min(100,score))//20))
        grouped.setdefault(bucket,[]).append(int(label))
    blocks=[]
    for bucket,labels in sorted(grouped.items()):
        block={'lower':bucket*20,'upper':(bucket+1)*20,'positive':sum(labels),'count':len(labels),'success':sum(labels)+1,'weight':len(labels)+2}
        blocks.append(block)
        while len(blocks)>1 and blocks[-2]['success']/blocks[-2]['weight']>blocks[-1]['success']/blocks[-1]['weight']:
            b=blocks.pop();a=blocks.pop()
            blocks.append({'lower':a['lower'],'upper':b['upper'],'positive':a['positive']+b['positive'],'count':a['count']+b['count'],'success':a['success']+b['success'],'weight':a['weight']+b['weight']})
    return [{'lower':b['lower'],'upper':b['upper'],'percentage':round(100*b['success']/b['weight'],4),'examples':b['count'],'positive_labels':b['positive']} for b in blocks]


@lru_cache(maxsize=1)
def artifact():
    try:
        data=json.loads(ARTIFACT.read_text())
        bins=data['bins']
        if not bins or any(not 0<=b['percentage']<=100 for b in bins) or any(a['percentage']>b['percentage'] for a,b in zip(bins,bins[1:])):raise ValueError()
        return data
    except (OSError,ValueError,KeyError,TypeError):return None


def calibrate(score):
    data=artifact()
    if data is None:return None,{'status':'unavailable','caveat':'Calibration artifact unavailable. Raw score is still usable.'}
    bins=data['bins'];chosen=next((b for b in bins if score<b['upper']),bins[-1])
    return chosen['percentage'],{'status':'experimental','version':data['version'],'training_pairs':data['training_pairs'],'bin_examples':chosen['examples'],'raw_range':[chosen['lower'],chosen['upper']],'interpretation':'Empirical synthetic relevance frequency with Laplace smoothing and monotonic isotonic pooling; not a hiring probability.','threshold_basis':'raw_hybrid_score','artifact_sha256':data['training_sha256']}


def calibrate_match(row):
    data=artifact()
    if data is None:return calibrate(row.hybrid_score)
    config=data.get('score_config',{})
    weights=config.get('weights',{})
    compatible=bool(weights) and row.calculation.get('semantic_fallback') and row.calculation.get('lexical_strategy','tfidf')=='tfidf'
    compatible=compatible and row.calculation.get('critical_penalty_max')==config.get('critical_penalty') and row.calculation.get('unknown_ceiling_base')==config.get('unknown_ceiling_base')
    for key,weight in weights.items():
        part=row.components.get('lexical_similarity' if key=='semantic' else key)
        compatible=compatible and part is not None and abs(part.configured_weight-weight)<1e-9
    if not compatible:return None,{'status':'out_of_scope','caveat':'The calibration fixture does not cover this semantic model or scoring configuration. Raw hybrid score is shown.'}
    return calibrate(row.hybrid_score)
