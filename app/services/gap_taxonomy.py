"""Small explainable taxonomy; unknown terms stay domain skills, never fuzzy matches."""
from functools import lru_cache
import re
from app.services.skill_taxonomy import ALIASES, CANONICAL_ONLY_ALIASES, canonical_skill

GROUPS = {
    'programming_languages':'Python|Java|JavaScript|TypeScript|C|C++|C#|Go|Rust|R|Kotlin|Swift|Ruby|PHP',
    'frontend':'HTML|CSS|Accessibility|Web accessibility|Responsive design',
    'frameworks':'React|Vue|Angular|Next.js|Node.js|Django|Flask|FastAPI|Express|Spring Boot|React Native',
    'databases':'SQL|PostgreSQL|MySQL|MongoDB|Redis|SQLite|Database design',
    'data_analysis':'Statistics|Data analysis|Pandas|NumPy|Excel|Power BI|Tableau|Data visualization',
    'machine_learning':'Machine learning|Deep learning|PyTorch|TensorFlow|Scikit-learn|Model evaluation|Feature engineering|Neural networks',
    'artificial_intelligence':'Artificial intelligence|LLMs|Generative AI|Retrieval augmented generation|Prompt engineering|Responsible AI|LangGraph|LangChain',
    'nlp':'Natural language processing|Text classification|Word2Vec|TF-IDF|Transformers',
    'computer_vision':'Computer vision|OpenCV|Image processing',
    'cloud':'AWS|Azure|Google Cloud|Cloud computing',
    'devops':'Docker|Kubernetes|CI/CD|Terraform|Linux|Git|Monitoring',
    'cybersecurity':'Cybersecurity|Network security|Penetration testing|OWASP|Threat modeling|Cryptography',
    'testing':'Software testing|Test automation|Selenium|Playwright|Pytest|Unit testing',
    'networking':'TCP/IP|Networking|DNS|HTTP',
    'embedded_systems':'Embedded C|Microcontrollers|RTOS|Embedded systems|Arduino',
    'design':'Figma|UI design|UX research|Prototyping',
    'project_management':'Agile|Scrum|Project management|Leadership',
    'communication':'Communication|Technical writing|Teamwork|Presentation',
    'research':'Research|Experiment design|Literature review',
    'tools':'Jira|Postman|VS Code|Jupyter',
    'libraries':'Requests|Matplotlib|Seaborn|Hugging Face Transformers',
    'platforms':'GitHub|GitLab|Vercel|Firebase',
    'backend':'REST APIs|API design|Authentication|Distributed systems|Microservices',
}
CATEGORIES = {canonical_skill(s):category for category,terms in GROUPS.items() for s in terms.split('|')}
FOUNDATIONS = {canonical_skill(s) for s in ('Python','JavaScript','SQL','Git','Statistics','Machine learning','Software testing','Networking','HTML','CSS','Linux','Data structures')}

def category(skill): return CATEGORIES.get(canonical_skill(skill),'domain_skills')

@lru_cache(maxsize=32768)
def phrase_pattern(term):
    return re.compile(r'(?<![\w+#])'+re.escape(term)+r'(?![\w+#])',re.I)

def mentions(text,terms):
    """Longest labels first; ML never appears inside HTML and Java never inside JavaScript."""
    occupied=[];output=[]
    for term in sorted(set(terms),key=lambda s:(-len(s),s)):
        if len(term)<2 and term not in {'C','R'}: continue
        for match in phrase_pattern(term).finditer(text):
            if any(match.start()<end and match.end()>start for start,end in occupied):continue
            occupied.append(match.span())
            prefix=text[max(0,match.start()-55):match.start()]
            denied=bool(re.search(r'(?:no (?:\w+\s+){0,3}(?:experience|knowledge|skills?) (?:with|in|of)|never used|not familiar with|do not know|don.t know)\s*$',prefix,re.I))
            output.append((term,match.group(),denied))
    return output

def vocabulary(jobs):
    terms=set(CATEGORIES)|set(ALIASES)|set(ALIASES.values())|set(CANONICAL_ONLY_ALIASES)
    for job in jobs:
        raw=job.model_dump() if hasattr(job,'model_dump') else job
        terms.update(raw.get('required_skills',[])+raw.get('preferred_skills',[]))
    return sorted(terms)
