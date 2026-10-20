"""Independent retrieval, provenance, calibration, and full-corpus recall checks."""
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.providers.jobs import SeededJobProvider
from app.schemas.jobs import JobFilters
from app.schemas.matching import MatchingStart
from app.services.match_normalization import normalize_candidate,normalize_job
from app.services.match_retrieval import BM25Strategy,SkillStrategy,TfidfStrategy,WordVectorStrategy,retrieve
from app.services.match_calibration import fit_isotonic,calibrate
from app.services.skill_taxonomy import canonical_skill,relationship,normalize_label
from app.services.matching import filtered_snapshot


@pytest.mark.parametrize('a,b',[('Node','Node.js'),('RESTful API','REST APIs'),('Amazon Web Services','AWS'),('Microsoft Azure','Azure'),('QA','Software testing'),('TS','TypeScript')])
def test_shared_taxonomy_aliases(a,b):assert canonical_skill(a)==canonical_skill(b)


def test_relationships_do_not_grant_false_equivalence():
    assert relationship('Python','Python')=='exact'
    assert relationship('JS','JavaScript')=='synonym'
    assert relationship('JavaScript','TypeScript')=='transferable'
    assert relationship('Python','Java')=='related'
    assert relationship('Java','JavaScript')=='unrelated'
    assert relationship('React','React Native')=='transferable'
    assert canonical_skill('C/C++')!=canonical_skill('C++')!=canonical_skill('C#')
    assert normalize_label('Bangalore')==normalize_label('Bengaluru')


def docs():
    return [{'text':'Python SQL backend APIs','required':[{'normalized':'python'},{'normalized':'sql'}],'original':{'id':'demo-z'}},
            {'text':'Typography brand research design','required':[{'normalized':'user research'}],'original':{'id':'demo-a'}},
            {'text':'Python backend integration SQL SQL SQL','required':[{'normalized':'python'},{'normalized':'sql'}],'original':{'id':'demo-b'}}]


def test_bm25_formula_length_normalization_empty_and_stable_retrieval():
    candidate=normalize_candidate({'skills':['Python','SQL']});jobs=docs();result=BM25Strategy().score(candidate,jobs)
    assert result.raw[0]>result.raw[1]==0 and 0<result.scores[0]<=100
    assert result.metadata['k1']==1.2 and result.metadata['b']==.75
    # Independent single-term hand calculation.
    one=BM25Strategy().score({'text':'python'},[{'text':'python'}])
    assert one.raw==pytest.approx([math.log(1+(1-1+.5)/(1+.5))])
    assert one.scores==pytest.approx([100/2.2])
    assert BM25Strategy().score({'text':''},jobs).scores==[0,0,0]
    assert BM25Strategy().score(candidate,[]).scores==[]
    tfidf=TfidfStrategy().score(candidate,jobs)
    first=retrieve(candidate,jobs,tfidf.scores,pool_size=2)
    assert first[0]==retrieve(candidate,jobs,tfidf.scores,pool_size=2)[0]
    assert len(first[0])==2 and first[2]['corpus_size']==3
    assert 'word2vec' in first[2]['methods'] and first[2]['methods']['embedding']['status']=='unavailable'
    assert SkillStrategy().score(candidate,jobs).scores==[100,0,100]


def test_optional_word_vectors_are_truthful_about_oov_and_bad_artifacts(tmp_path):
    candidate={'text':'python sql'};jobs=docs()
    assert WordVectorStrategy().score(candidate,jobs).scores is None
    path=tmp_path/'toy.vec';path.write_text('3 2\npython 1 0\nsql 1 0\ndesign 0 1\n')
    result=WordVectorStrategy(path).score(candidate,jobs)
    assert result.scores[0]==pytest.approx(100) and result.scores[1]==0
    assert result.metadata['query_coverage']==1 and result.metadata['artifact_sha256']
    assert WordVectorStrategy(path).score({'text':'unseen-vocabulary'},jobs).metadata['status']=='insufficient_vocabulary'
    path.write_text('1 2\npython nan 1\n')
    assert WordVectorStrategy(path).score(candidate,jobs).metadata['status']=='unavailable'


def test_monotonic_calibration_uses_labels_not_score_multiplication():
    bins=fit_isotonic([(5,0),(10,0),(30,1),(35,0),(50,0),(70,1),(75,1)])
    assert all(a['percentage']<=b['percentage'] for a,b in zip(bins,bins[1:]))
    values=[calibrate(n)[0] for n in range(101)]
    assert all(v is not None and 0<=v<=100 for v in values)
    assert values==sorted(values)
    assert calibrate(65)[1]['threshold_basis']=='raw_hybrid_score'
    with patch('app.services.match_calibration.artifact',return_value=None):
        assert calibrate(65)[0] is None


def test_entire_10000_corpus_is_filtered_without_first_thousand_truncation():
    snapshot=SeededJobProvider().snapshot();rows=filtered_snapshot(snapshot,JobFilters())
    assert len(rows)==10000 and rows[-1].id==snapshot.jobs[-1].id
    for mode in ('remote','hybrid','onsite'):
        selected=filtered_snapshot(snapshot,JobFilters(work_mode=mode))
        assert len(selected)==sum(j.work_mode==mode for j in snapshot.jobs)
    assert len(filtered_snapshot(snapshot,JobFilters(max_results=1001)))==1001
    assert MatchingStart(retrieval_pool_size=10000).retrieval_pool_size==10000
    with pytest.raises(ValueError):MatchingStart(retrieval_pool_size=10001)


def test_weighted_dataset_varies_coherent_stacks_and_not_only_titles():
    rows=SeededJobProvider().snapshot().jobs
    counts={c:sum(j.category==c for j in rows) for c in {j.category for j in rows}}
    assert counts['backend']>5*counts['ar_vr']
    assert len({tuple(j.required_skills) for j in rows if j.category=='backend'})>=15
    assert all(j.subcategory and j.title_tokens and j.category_tokens and j.searchable_text for j in rows)
    assert sum(j.experience_level=='limited_experience' for j in rows)<.05*len(rows)
    assert any('java' in j.normalized_skill_tokens for j in rows if j.category=='backend')
    assert any('react native' in j.normalized_skill_tokens for j in rows if j.category=='mobile')
    assert all(not {'seo','microcontrollers'}.issubset(set(j.normalized_skill_tokens)) for j in rows)


def test_embedding_strategy_reports_unavailable_without_faking_semantics():
    import time
    from app.services.match_retrieval import EmbeddingStrategy
    result=EmbeddingStrategy(Settings(_env_file=None),None,'synthetic',time.monotonic()+5).score(normalize_candidate({'skills':['Python']}),[])
    assert result.scores is None and result.metadata['status']=='not_configured'


def test_calibration_rejects_unseen_semantic_models_or_changed_weights():
    from test_matching import ranked
    from app.services.match_calibration import calibrate_match
    row,_,_=ranked({'skills':['Python','SQL']})
    assert calibrate_match(row)[0] is not None
    row.components['required_skills'].configured_weight=.5
    assert calibrate_match(row)[0] is None
    row,_,_=ranked({'skills':['Python']},semantic=65)
    assert calibrate_match(row)[1]['status']=='out_of_scope'


def test_v2_saved_listings_remain_readable_without_weakening_v3_taxonomy():
    from app.schemas.jobs import Job
    from app.services.job_normalization import normalized_skills
    raw=next(j for j in SeededJobProvider().snapshot().jobs if 'c/c++' in j.normalized_skill_tokens).model_dump(mode='json')
    raw['metadata']['dataset_version']='2026-10-demo-v2'
    raw['normalized_skill_tokens']=sorted({'c++' if s=='c/c++' else s for s in raw['normalized_skill_tokens']})
    assert Job.model_validate(raw).metadata.dataset_version=='2026-10-demo-v2'
    raw['metadata']['dataset_version']='2026-10-demo-v3-10000'
    with pytest.raises(ValueError):Job.model_validate(raw)
    raw['normalized_skill_tokens']=normalized_skills(raw['required_skills']+raw['preferred_skills'])
    assert 'c/c++' in Job.model_validate(raw).normalized_skill_tokens
