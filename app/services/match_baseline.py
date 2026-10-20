"""Deterministic sparse TF-IDF, independently visible from hybrid ranking."""
from collections import Counter
import math

from app.services.match_normalization import tokens


class TfidfBaseline:
    version='tfidf-word-bigram-logtf-smoothidf-v1'

    @staticmethod
    def terms(text):
        words=tokens(text)
        return words+[a+' '+b for a,b in zip(words,words[1:])]

    def __init__(self,texts):
        frequency=Counter()
        for text in texts:frequency.update(set(self.terms(text)))
        self.documents=len(texts)
        self.idf={term:math.log((1+len(texts))/(1+count))+1 for term,count in frequency.items()}

    def vector(self,text):
        counts=Counter(self.terms(text))
        raw={term:(1+math.log(count))*self.idf[term] for term,count in counts.items() if term in self.idf}
        length=math.sqrt(sum(v*v for v in raw.values()))
        return {k:v/length for k,v in raw.items()} if length else {}

    @staticmethod
    def similarity(a,b):
        return max(0,min(100,100*sum(v*b.get(k,0) for k,v in a.items())))

    def compare(self,candidate,jobs):
        query=self.vector(candidate)
        return [round(self.similarity(query,self.vector(job)),6) for job in jobs]
