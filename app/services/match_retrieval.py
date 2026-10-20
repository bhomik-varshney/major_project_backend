"""Independent retrieval strategies and a recall-oriented, whole-corpus fusion pool.

BM25 uses Robertson's positive IDF, k1=1.2, b=.75. Its bounded diagnostic
score is the fraction of the query's theoretical saturation upper bound;
raw BM25 is retained. Neither value is a calibrated probability.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol

from app.services.match_normalization import tokens
from app.services.match_baseline import TfidfBaseline


@dataclass
class RetrievalScores:
    name: str
    scores: list[float] | None
    raw: list[float] | None = None
    metadata: dict = field(default_factory=dict)


class RetrievalStrategy(Protocol):
    def score(self, candidate: dict, jobs: list[dict]) -> RetrievalScores: ...


class TfidfStrategy:
    def score(self,candidate,jobs):
        model=TfidfBaseline([candidate['text']]+[j['text'] for j in jobs])
        return RetrievalScores('tfidf',model.compare(candidate['text'],[j['text'] for j in jobs]),metadata={'version':model.version})


class BM25Strategy:
    k1=1.2;b=.75;version='bm25-positive-idf-k1-1.2-b-.75-v1'
    def score(self,candidate,jobs):
        docs=[Counter(tokens(j['text'])) for j in jobs]
        lengths=[sum(d.values()) for d in docs];avg=sum(lengths)/len(docs) if docs else 1
        df=Counter(t for d in docs for t in d)
        query=set(tokens(candidate['text']));n=len(docs)
        idf={t:math.log(1+(n-df[t]+.5)/(df[t]+.5)) for t in query if df[t]}
        bound=sum(v*(self.k1+1) for v in idf.values())
        raw=[]
        for doc,length in zip(docs,lengths):
            norm=self.k1*(1-self.b+self.b*length/(avg or 1))
            raw.append(sum(idf[t]*doc[t]*(self.k1+1)/(doc[t]+norm) for t in idf if doc[t]))
        return RetrievalScores('bm25',[100*s/bound if bound else 0 for s in raw],raw,{'version':self.version,'k1':self.k1,'b':self.b,'query_saturation_bound':bound})


class SkillStrategy:
    def score(self,candidate,jobs):
        query=set(candidate['skill_tokens']);postings=defaultdict(list);totals=[]
        for i,job in enumerate(jobs):
            required={s['normalized'] for s in job['required']};totals.append(len(required))
            for skill in required:postings[skill].append(i)
        matches=Counter(i for skill in query for i in postings.get(skill,[]))
        return RetrievalScores('skill_overlap',[100*matches[i]/total if total else 0 for i,total in enumerate(totals)],metadata={'policy':'inverted skill postings; distinct exact/synonym required-skill recall; no related-skill credit','indexed_skills':len(postings)})


class WordVectorStrategy:
    """Optional local Word2Vec .vec text artifact; no auto-download or fake semantics.

    OOV terms are ignored and coverage is reported. Without a supplied suitable
    model this method is unavailable, not zero and not a lexical stand-in.
    """
    def __init__(self,path=None):self.path=Path(path) if path else None
    def score(self,candidate,jobs):
        if self.path is None or not self.path.is_file():return RetrievalScores('word2vec',None,metadata={'status':'not_configured','reason':'No suitable pretrained local word-vector artifact is configured.'})
        try:
            if self.path.stat().st_size>64*1024*1024:raise ValueError()
            content=self.path.read_bytes();lines=content.decode().splitlines();header=lines.pop(0).split()
            count,dimensions=map(int,header)
            if not 1<=dimensions<=1024 or not 1<=count<=300000 or len(lines)!=count:raise ValueError()
            vectors={}
            for line in lines:
                values=line.split();word=values[0];v=list(map(float,values[1:]))
                if len(v)!=dimensions or any(not math.isfinite(x) for x in v) or word in vectors:raise ValueError()
                vectors[word]=v
            def vector(text):
                terms=tokens(text);known=[vectors[t] for t in terms if t in vectors]
                return [sum(col)/len(known) for col in zip(*known)] if known else [],len(known)/len(terms) if terms else 0
            query,coverage=vector(candidate['text'])
            if not query or coverage<.25:return RetrievalScores('word2vec',None,metadata={'status':'insufficient_vocabulary','query_coverage':coverage})
            def cosine(a,b):
                denom=math.hypot(*a)*math.hypot(*b)
                return 100*max(0,min(1,sum(x*y for x,y in zip(a,b))/denom)) if denom else 0
            scores=[cosine(query,vector(j['text'])[0]) for j in jobs]
            return RetrievalScores('word2vec',scores,metadata={'status':'active','query_coverage':coverage,'vocabulary':len(vectors),'dimensions':dimensions,'artifact_sha256':hashlib.sha256(content).hexdigest(),'quality':'user-supplied model; not validated for hiring relevance'})
        except (ValueError,OSError,UnicodeError,IndexError):
            return RetrievalScores('word2vec',None,metadata={'status':'unavailable','reason':'Invalid local vector artifact; lexical retrieval remains available.'})


def retrieve(candidate,jobs,tfidf,semantic=None,word_vector_path=None,pool_size=1200):
    methods=[RetrievalScores('tfidf',tfidf,metadata={'version':TfidfBaseline.version}),BM25Strategy().score(candidate,jobs),SkillStrategy().score(candidate,jobs),WordVectorStrategy(word_vector_path).score(candidate,jobs),RetrievalScores('embedding',semantic,metadata={'status':'active' if semantic is not None else 'unavailable'})]
    ids=[j['original']['id'] for j in jobs];fusion=defaultdict(float);ranks=defaultdict(dict)
    active=[m for m in methods if m.scores is not None]
    # Each method contributes up to the entire configurable pool, preserving union recall.
    for method in active:
        ordered=sorted((i for i,s in enumerate(method.scores) if s>0),key=lambda i:(-method.scores[i],ids[i]))
        for rank,i in enumerate(ordered[:pool_size],1):
            fusion[i]+=1/(60+rank);ranks[i][method.name]=rank
    selected=sorted(fusion,key=lambda i:(-fusion[i],ids[i]))[:pool_size]
    # Small filtered pools are scored completely, including zero-signal rows.
    # A threshold of zero must not masquerade as an empty dataset.
    if len(jobs)<=pool_size:
        selected+=sorted((i for i in range(len(jobs)) if i not in fusion),key=lambda i:ids[i])
    selected_set=set(selected)
    excluded=[{'job_id':ids[i],'reason':'outside_retrieval_pool' if i in fusion else 'no_positive_retrieval_signal'} for i in range(len(jobs)) if i not in selected_set]
    return selected,methods,{
        'status':'completed','policy':'reciprocal-rank fusion, k=60; full filtered corpus; small pools include zero-signal rows',
        'corpus_size':len(jobs),'pool_limit':pool_size,'retrieved_count':len(selected),'excluded_count':len(excluded),
        'methods':{m.name:{'status':'active' if m.scores is not None else m.metadata.get('status','unavailable'),**m.metadata} for m in methods},
        'candidates':[{'job_id':ids[i],'fusion_score':fusion[i],'method_ranks':ranks[i],'zero_signal':not bool(ranks[i])} for i in selected],
        'excluded_jobs':excluded,
    }


class EmbeddingStrategy:
    """Independent adapter for the existing validated, content-addressed provider."""
    def __init__(self,settings,factory,scope,deadline,provider=None):
        self.settings,self.factory,self.scope,self.deadline,self.provider=settings,factory,scope,deadline,provider
    def score(self,candidate,jobs):
        from app.providers.embeddings import semantic_scores
        scores,_,metadata,warning=semantic_scores(candidate,jobs,self.settings,self.factory,self.scope,self.deadline,self.provider)
        if warning:metadata['warning']=warning
        return RetrievalScores('embedding',scores,metadata=metadata)
