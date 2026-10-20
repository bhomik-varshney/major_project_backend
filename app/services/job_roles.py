"""Controlled career-role expansion. Mentions and transferable skills are not exact roles."""
from functools import lru_cache
import re
from app.services.skill_taxonomy import normalize_phrases

AI_CATEGORIES=frozenset({'artificial_intelligence','machine_learning','deep_learning','generative_ai','natural_language_processing','computer_vision','data_science'})
ROLE_GROUPS={
 'ai':('AI',AI_CATEGORIES,('artificial intelligence','ai','machine learning','ml','deep learning','natural language processing','nlp','computer vision','generative ai','llms','neural networks','predictive modelling','predictive modeling','agentic ai')),
 'ml':('Machine learning',frozenset({'machine_learning','data_science','deep_learning','natural_language_processing','computer_vision','artificial_intelligence'}),('machine learning','ml','neural networks','predictive modelling','predictive modeling')),
 'genai':('Generative AI',frozenset({'generative_ai'}),('generative ai','llms','large language model','large language models','retrieval augmented generation','llm fine-tuning','qlora','openai agents sdk','langgraph')),
 'nlp':('Natural language processing',frozenset({'natural_language_processing'}),('natural language processing','nlp','text classification','language modeling','spacy')),
 'vision':('Computer vision',frozenset({'computer_vision'}),('computer vision','image processing','opencv','mediapipe')),
 'deep':('Deep learning',frozenset({'deep_learning'}),('deep learning','neural networks','pytorch','tensorflow')),
}
ALIASES={'ai':'ai','ai engineer':'ai','artificial intelligence':'ai','artificial intelligence engineer':'ai','machine learning':'ml','machine learning engineer':'ml','ml':'ml','generative ai':'genai','gen ai':'genai','llm':'genai','llms':'genai','deep learning':'deep','neural networks':'deep','nlp':'nlp','natural language processing':'nlp','computer vision':'vision','predictive modelling':'ml','predictive modeling':'ml'}


def words(text):return re.findall(r'[\w]+(?:\+\+|#)?',normalize_phrases(str(text)))
def phrase(text,term):
    return re.search(r'(?<!\w)'+re.escape(normalize_phrases(term))+r'(?!\w)',normalize_phrases(text)) is not None


@lru_cache(maxsize=512)
def role_spec(query):
    normalized=' '.join(normalize_phrases(query).replace('_',' ').split())
    key=ALIASES.get(normalized)
    if key:
        label,categories,terms=ROLE_GROUPS[key]
        return {'original':query,'family':key,'label':label,'categories':sorted(categories),'terms':list(terms)}
    return {'original':query,'family':None,'label':normalized,'categories':[],'terms':words(normalized)}


def role_match(query,job):
    spec=role_spec(query);get=lambda key,default='':job.get(key,default) if isinstance(job,dict) else getattr(job,key,default)
    if not spec['label']:return {'accepted':True,'relation':'not_requested','reason':'No role constraint was requested.','evidence':[]}
    title=get('title');category=get('category');terms=spec['terms']
    required=get('required_skills',[]);preferred=get('preferred_skills',[])
    if spec['family']:
        title_terms=[term for term in terms if phrase(title,term)]
        if title_terms:
            primary={'ai':'artificial_intelligence','ml':'machine_learning','genai':'generative_ai','nlp':'natural_language_processing','vision':'computer_vision','deep':'deep_learning'}[spec['family']]
            related=category in spec['categories'] and category!=primary
            return {'accepted':True,'relation':'related_role' if related else 'exact_role','reason':'The title is a related specialization within the requested role family.' if related else 'The job title contains the requested role or synonym.','evidence':[{'job_path':'title','term':t} for t in title_terms]}

        if category in spec['categories']:return {'accepted':True,'relation':'related_role','reason':'The category belongs to the explicitly expanded role family.','evidence':[{'job_path':'category','value':category}]}
        matches=[{'job_path':f'{field}.{i}','term':term} for field,skills in [('required_skills',required),('preferred_skills',preferred)] for i,s in enumerate(skills) for term in terms if phrase(s,term)]
        if matches:return {'accepted':True,'relation':'skill_based','reason':'Explicit listed skills support this role topic; the title itself is not an exact role match.','evidence':matches[:12]}
        text=' '.join([get('description'),*get('responsibilities',[])])
        mentions=[term for term in terms if phrase(text,term)]
        return {'accepted':False,'relation':'weak_keyword' if mentions else 'unrelated_keyword','reason':'Only a description/responsibility mention exists; it does not establish the requested role.' if mentions else 'No bounded role, category or explicit-skill evidence supports the requested topic.','evidence':[{'job_path':'description/responsibilities','term':t} for t in mentions[:5]]}
    tokens=set(words(' '.join([title,category.replace('_',' '),get('subcategory')])));wanted=set(terms)-{'engineer','developer','intern','graduate','junior','trainee','associate','specialist'}
    if wanted and wanted<=tokens:return {'accepted':True,'relation':'exact_role','reason':'Requested role terms match the title/category.','evidence':[{'job_path':'title/category','terms':sorted(wanted)}]}
    skill_tokens=set(words(' '.join(required+preferred)))
    if wanted and wanted<=skill_tokens:return {'accepted':True,'relation':'skill_based','reason':'Explicit skill terms support the request.','evidence':[{'job_path':'required_skills/preferred_skills','terms':sorted(wanted)}]}
    return {'accepted':False,'relation':'unrelated_keyword','reason':'The requested role terms lack title/category/explicit-skill support.','evidence':[]}


def derived_topics(facts):
    sources=[]
    for field in ('skills','preferred_roles'):
        sources.extend((f'{field}.{i}',text) for i,text in enumerate(facts.get(field,[])))
    if facts.get('career_objective'):sources.append(('career_objective',facts['career_objective']))
    for field in ('projects','experience'):
        for i,entry in enumerate(facts.get(field,[])):
            sources.extend((f'{field}.{i}.{key}',str(entry.get(key,''))) for key in ('name','role','description'))
    output=[]
    for key,(label,categories,terms) in ROLE_GROUPS.items():
        evidence=[{'candidate_path':path,'mention':term} for path,text in sources for term in terms if phrase(text,term)]
        if evidence:output.append({'topic':label,'family':key,'categories':sorted(categories),'evidence':evidence[:10],'derived_from_confirmed_text':True,'counts_as_preference':False,'counts_as_required_skill':False})
    return output
