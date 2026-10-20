"""Central, conservative equivalence map. Related technologies never satisfy a requirement."""
import re
from functools import lru_cache
import unicodedata

ALIASES = {
    'js':'javascript','ts':'typescript','py':'python','ml':'machine learning',
    'nlp':'natural language processing','postgres':'postgresql','k8s':'kubernetes',
    'react.js':'react','reactjs':'react','node':'node.js','nodejs':'node.js',
    'rest api':'rest apis','restful api':'rest apis','restful apis':'rest apis','rest':'rest apis',
    'amazon web services':'aws','microsoft azure':'azure','gcp':'google cloud',
    'quality assurance':'software testing','qa':'software testing','testing':'software testing',
    'ci / cd':'ci/cd','powerbi':'power bi','sklearn':'scikit-learn','scikit learn':'scikit-learn',
    'natural-language processing':'natural language processing','llm':'llms',
    'rag':'retrieval augmented generation','react-native':'react native',
}
RELATED = {frozenset(pair) for pair in [('python','java'),('aws','azure'),('aws','google cloud'),('react','vue'),('mysql','postgresql')]}
TRANSFER = {
    ('python','django'):'Python is a foundation for learning Django.',
    ('javascript','typescript'):'JavaScript experience can help when learning TypeScript.',
    ('sql','postgresql'):'SQL knowledge can transfer to PostgreSQL, but it does not establish PostgreSQL proficiency.',
    ('react','react native'):'React concepts may transfer to React Native; mobile development still needs validation.',
    ('software testing','test automation'):'Testing concepts may help with automation, but automation experience is not confirmed.',
}

def canonical_skill(value):
    text=' '.join(unicodedata.normalize('NFKC',str(value)).casefold().split())
    return ALIASES.get(text,text)

def relationship(left,right):
    a,b=canonical_skill(left),canonical_skill(right)
    if a==b:return 'exact' if str(left).strip().casefold()==str(right).strip().casefold() else 'synonym'
    if (a,b) in TRANSFER:return 'transferable'
    if frozenset((a,b)) in RELATED:return 'related'
    return 'unrelated'

def normalize_label(value):
    text=' '.join(unicodedata.normalize('NFKC',str(value)).casefold().replace('_',' ').split())
    for a,b in [('bangalore','bengaluru'),('gurgaon','gurugram'),('back-end','backend'),('front-end','frontend'),('full-stack','fullstack'),('full stack','fullstack'),('ui/ux','ui ux')]:text=text.replace(a,b)
    return text

# Phrase aliases applied before lexical tokenization; boundaries protect Java/JS etc.
PHRASES=tuple(sorted(ALIASES.items(),key=lambda pair:-len(pair[0])))
PHRASE_PATTERN=re.compile(r'(?<![\w])('+'|'.join(re.escape(a) for a,_ in PHRASES)+r')(?![\w])')

@lru_cache(maxsize=32768)
def normalize_phrases(text):
    text=normalize_label(text)
    # One substitution pass avoids cascading aliases and retains punctuation languages.
    return PHRASE_PATTERN.sub(lambda m:ALIASES[m[0]],text)
