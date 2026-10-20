"""Optional real local embeddings. Lexical vectors are never labeled semantic."""
from hashlib import sha256
import json
import math
import time
from typing import Protocol

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import EmbeddingCache


class EmbeddingUnavailable(Exception):
    pass


class EmbeddingProvider(Protocol):
    name: str
    model: str
    def embed(self,texts:list[str],deadline:float)->list[list[float]]: ...


def validate_vectors(vectors,count):
    if not isinstance(vectors,list) or len(vectors)!=count:raise EmbeddingUnavailable('Embedding provider returned an invalid batch.')
    dimensions=None
    for vector in vectors:
        if not isinstance(vector,list) or not 1<=len(vector)<=4096 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vector):
            raise EmbeddingUnavailable('Embedding provider returned invalid vectors.')
        if dimensions is not None and len(vector)!=dimensions:raise EmbeddingUnavailable('Embedding dimensions changed within a batch.')
        dimensions=len(vector)
        magnitude=math.hypot(*vector)
        if not magnitude or not math.isfinite(magnitude):raise EmbeddingUnavailable('Embedding provider returned an invalid magnitude.')
    return vectors


class OllamaEmbeddings:
    name='ollama'
    def __init__(self,settings,client_factory=httpx.Client):
        self.model=settings.matching_embedding_model;self.url=settings.matching_embedding_url;self.client_factory=client_factory

    def embed(self,texts,deadline):
        last='Local embedding service is unavailable.'
        for attempt in range(2):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise EmbeddingUnavailable('Embedding time budget expired.')
            try:
                with self.client_factory(timeout=min(15,remaining)) as client:
                    response=client.post(self.url,json={'model':self.model,'input':texts,'truncate':False})
                if response.status_code==429 or response.status_code>=500:
                    if attempt==0:continue
                    break
                if not response.is_success:break
                return validate_vectors(response.json().get('embeddings'),len(texts))
            except (httpx.HTTPError,ValueError,AttributeError):
                pass
        raise EmbeddingUnavailable(last)


def cosine(a,b):
    if len(a)!=len(b):raise EmbeddingUnavailable('Cached embedding dimensions do not match.')
    norm_a,norm_b=math.hypot(*a),math.hypot(*b)
    if not norm_a or not norm_b or not math.isfinite(norm_a) or not math.isfinite(norm_b):raise EmbeddingUnavailable('Embedding has an invalid magnitude.')
    return max(0,min(100,100*sum((x/norm_a)*(y/norm_b) for x,y in zip(a,b))))


class CachedEmbeddings:
    def __init__(self,provider,factory):self.provider,self.factory=provider,factory;self.hits=0;self.misses=0
    def embed(self,texts,scope,deadline):
        keys=[sha256(json.dumps([scope,self.provider.name,self.provider.model,text],ensure_ascii=False).encode()).hexdigest() for text in texts]
        output=[None]*len(texts);missing={}
        with self.factory() as db:
            for index,key in enumerate(keys):
                cached=db.get(EmbeddingCache,key)
                if cached:
                    try:output[index]=validate_vectors([cached.vector],1)[0];self.hits+=1
                    except EmbeddingUnavailable:db.delete(cached);missing.setdefault(key,[]).append(index)
                else:missing.setdefault(key,[]).append(index)
            db.commit()
        pending=list(missing)
        for start in range(0,len(pending),32):
            batch=pending[start:start+32]
            vectors=validate_vectors(self.provider.embed([texts[missing[key][0]] for key in batch],deadline),len(batch))
            with self.factory() as db:
                for key,vector in zip(batch,vectors):
                    if db.bind.dialect.name=='sqlite':
                        db.execute(sqlite_insert(EmbeddingCache).values(key=key,provider=self.provider.name,model=self.provider.model,vector=vector).on_conflict_do_nothing(index_elements=['key']))
                    else:
                        # A cache conflict is harmless; results remain content addressed.
                        from sqlalchemy.dialects.postgresql import insert
                        db.execute(insert(EmbeddingCache).values(key=key,provider=self.provider.name,model=self.provider.model,vector=vector).on_conflict_do_nothing(index_elements=['key']))
                    for index in missing[key]:output[index]=vector
                    self.misses+=1
                db.commit()
        return validate_vectors(output,len(texts))


def semantic_scores(candidate,jobs,settings,factory,user_scope,deadline,provider=None):
    metadata={'provider':settings.matching_embedding_provider,'model':settings.matching_embedding_model if settings.matching_embedding_provider!='none' else None,'status':'unavailable','cache_hits':0,'cache_misses':0}
    if provider is None and settings.matching_embedding_provider=='none':
        metadata['status']='not_configured'
        return None,None,metadata,'Semantic embeddings are not configured. TF-IDF is used as the text-similarity fallback.'
    provider=provider or OllamaEmbeddings(settings)
    metadata.update(provider=provider.name,model=provider.model)
    cache=CachedEmbeddings(provider,factory)
    try:
        query_texts=[candidate['text']]
        if candidate['project_experience_text']:query_texts.append(candidate['project_experience_text'])
        queries=cache.embed(query_texts,'candidate:'+user_scope,deadline)
        vectors=cache.embed([j['text'] for j in jobs],'seeded_jobs',deadline) if jobs else []
        scores=[cosine(queries[0],v) for v in vectors]
        projects=[cosine(queries[1],v) for v in vectors] if len(queries)>1 else None
        metadata.update(status='active',dimensions=len(queries[0]),cache_hits=cache.hits,cache_misses=cache.misses)
        return scores,projects,metadata,None
    except Exception:
        metadata.update(cache_hits=cache.hits,cache_misses=cache.misses)
        return None,None,metadata,'Embedding service failed or returned invalid vectors. TF-IDF and explicit skill scoring remain available.'
