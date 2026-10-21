"""Anonymised regression for the sparse PDF: 2 internships / 4 projects / 36 skills."""
from copy import deepcopy
from pathlib import Path
import json,time
import pytest
from pypdf import PdfReader
from app.core.config import Settings
from app.schemas.resume_tailoring import ResumeStart,SECTIONS
from app.services.resume_tailoring_content import catalog,assemble,validate,report,edit_draft
from app.services.resume_tailoring_render import compile_resume,populate,reduce_content
from app.services.cover_letter_render import RenderFailure
from app.services.cover_letter_render import normalized
from app.flows.resume_tailoring import ResumeTailoringFlow
from app.providers.groq import GroqKeyManager
from test_resume_tailoring import FACTS,JOB,start,create

SKILLS=['Python','FastAPI','React 19','OpenAI Agents SDK','MCP','Docker','PyTorch','Hugging Face Transformers',
    'PEFT','QLoRA','React','TypeScript','LangGraph','Groq LLM API','React Three Fiber','Three.js','HTML','JavaScript',
    'C/C++','bitsandbytes','BERTScore','spaCy','Tesseract OCR','OpenCV','MediaPipe','Model Context Protocol (MCP)',
    'MCP Apps','CrewAI','Flask','React Native','TanStack Start','Docker Compose','Git','Render','Langfuse','SQL']

def regression():
    f=deepcopy(FACTS);f['skills']=SKILLS[:];f['experience_level']='student'
    f['experience']=[{'organization':'Adobe Systems India','role':'React Native Developer Intern','period':'May 2025 – July 2025','location':'Remote',
        'description':'Built React Native front-end components for an internal learning system. Delivered production-facing learning functionality during an internship.',
        'responsibilities':['Built React Native front-end components for an internal','learning system.'], 'technologies':['React']},
        {'organization':'Example Training Initiative','role':'Machine Learning Trainee','period':'December 2024 – January 2025','location':'Remote',
        'description':'Built an SMS classifier with an NLP preprocessing pipeline (HTML/URL stripping and stemming). Compared TF-IDF and Word2Vec with a Random Forest classifier, reaching 96.6% test accuracy.',
        'technologies':['HTML']}]
    f['projects']=[{'name':name,'description':text,'technologies':tech,'repository':f'https://github.com/student/project-{i}',
        'url':f'https://github.com/student/project-{i}','links':[f'https://github.com/student/project-{i}'],
        'deployment_url':f'https://project-{i}.example.com'} for i,(name,text,tech) in enumerate([
        ('Agent Marketplace — Team Project','Built an agentic marketplace using Python and MCP. Validated ranking with 216 synthetic tests.',['Python','MCP']),
        ('Voice Model Fine-Tuning — Technical Assessment','Fine-tuned a language model with QLoRA and PyTorch. Evaluated the model with BERTScore.',['PyTorch','QLoRA']),
        ('Health Assistant — Team Project','Built a FastAPI application with spaCy NLP and OpenCV. Implemented structured text extraction.',['FastAPI','spaCy','OpenCV']),
        ('Traffic Simulation — Demonstration','Designed a traffic-control simulation using React Three Fiber. Visualised adaptive signal timing.',['React Three Fiber'])])]
    f['education']+=[{'institution':'Example Secondary School','qualification':'Senior Secondary XII','period':'2021 – 2023'}]
    j={**deepcopy(JOB),'title':'NLP Engineer Intern','required_skills':['Python','Natural language processing'],'preferred_skills':['spaCy']}
    return f,j

def test_complete_ledger_preserves_internships_projects_skills_and_primary_education():
    f,j=regression();before=deepcopy(f);s=catalog(f,j,{});d=assemble(f,j,s);r=report(d,s,f)
    assert f==before and validate(d,s,f,j)['valid']
    assert r['coverage']['experience']=={'available':2,'preserved':2}
    assert r['coverage']['projects']=={'available':4,'preserved':4}
    assert r['coverage']['skills']=={'available':36,'preserved':36}
    assert len(s['ledger'])>100 and all(set(['original','normalized','source_evidence','confidence','relevance_status','omission_allowed'])<=v.keys() for v in s['ledger'])
    assert any(e.title=='Adobe Systems India' for e in d.entries)
    assert any(v['section']=='education' and 'primary qualification' in v['reason'] for v in r['omitted'])
    assert any(v['relevance_status']=='source_metric_or_date' for v in s['ledger'])

def test_relevance_recovers_missing_context_and_normalizes_nlp_without_inventing_skills():
    f,j=regression();s=catalog(f,j,{'matching':{'status':'unavailable'},'skill_gaps':{}});p=s['relevance_plan'];d=assemble(f,j,s)
    assert p['valid'] and p['status']=='computed_during_tailoring' and p['review_recommended']
    assert next(v for v in p['required'] if v['skill']=='Natural language processing')['status']=='supported'
    assert next(e for e in d.entries if e.id=='experience-1').relevance>next(e for e in d.entries if e.id=='experience-0').relevance
    assert 'Machine Learning Trainee' in d.summary and 'natural language processing' in d.summary
    assert not any('No current matching' in w or 'No current skill-gap' in w for w in s['warnings'])
    j['required_skills']+=['Kubernetes'];s=catalog(f,j,{})
    assert s['relevance_plan']['required'][-1]['status']=='missing_evidence'
    assert not any(e['section']=='skills' and e['title']=='Kubernetes' for e in s['entries'])

def test_technology_association_ocr_fragments_and_project_link_deduplication():
    f,j=regression();s=catalog(f,j,{});d=assemble(f,j,s)
    adobe=next(e for e in d.entries if e.id=='experience-0');ml=next(e for e in d.entries if e.id=='experience-1')
    assert 'React Native' in adobe.technologies and 'HTML' not in ml.technologies
    assert s['technology_reviews'][0]['technology']=='HTML'
    assert all(b['original'] not in ('learning system.','Built React Native front-end components for an internal') for b in adobe.bullets)
    for e in d.entries:
        assert len({v['url'] for v in e.links})==len(e.links)
        if e.section=='projects':assert [v['label'] for v in e.links]==['Repository','Deployment']
    tex=populate(d)
    assert 'Project link' not in tex and '"organization":' not in tex and 'Technologies: HTML' not in tex

@pytest.mark.parametrize('section',['experience','projects','skills'])
def test_protected_content_cannot_be_silently_deselected(section):
    f,j=regression();s=catalog(f,j,{});d=assemble(f,j,s)
    removed=next(e for e in d.entries if e.section==section)
    d=edit_draft(d,s,{'entry_ids':[e.id for e in d.entries if e.id!=removed.id]})
    assert any(v['field']==removed.id and 'Protected' in v['reason'] for v in validate(d,s,f,j)['issues'])

def test_sparse_model_selection_is_restored_before_render(tmp_path,monkeypatch):
    f,j=regression();s=catalog(f,j,{});monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path))
    import app.crews.resume_tailoring as crew
    sparse={'summary_id':'focused','entry_ids':['experience-1','projects-1','skills-0','education-0'],
        'bullet_ids':[],'section_order':list(SECTIONS)}
    monkeypatch.setattr(crew,'propose',lambda *args:(deepcopy(sparse),{'engine':'crewai.Crew','status':'completed','model':'synthetic'}))
    options=ResumeStart(expected_application_revision=1,use_ai=True,allow_external_processing=True).model_dump()
    result=ResumeTailoringFlow({'facts':f,'job':j,'context':{},'options':options},Settings(),GroqKeyManager([]),1,time.monotonic()+60).kickoff()
    assert result['provider']['status']=='repaired' and result['validation']['valid']
    assert set(s['protected_ids'])<={e['id'] for e in result['draft']['entries']}

def test_regression_one_page_has_all_history_skills_links_and_ats_order(tmp_path):
    f,j=regression();s=catalog(f,j,{});d=assemble(f,j,s);before=deepcopy(f)
    final,a=compile_resume(d,s,f,j,tmp_path,Settings());text=PdfReader(tmp_path/'document.pdf').pages[0].extract_text()
    assert a['quality']['valid'] and a['quality']['page_count']==1 and a['quality']['body_font_pt']==11
    assert a['quality']['selectable_text'] and a['quality']['fonts_embedded'] and a['quality']['images']==0
    assert 'Adobe Systems India' in text and all(normalized(e.title) in normalized(text) for e in final.entries if e.section=='skills')
    assert [e.id for e in final.entries]==[e.id for e in d.entries] and f==before
    headings=['Professional Summary','Experience','Projects','Technical Skills','Education']
    assert [text.index(h) for h in headings]==sorted(text.index(h) for h in headings)
    assert (tmp_path/'preview.png').is_file() and 'Project link' not in text
    assert 'Technologies: HTML' not in text
    assert report(final,s,f)['title_condensation']

def test_compression_logs_bullet_omissions_without_dropping_entries():
    f,j=regression();s=catalog(f,j,{});d=assemble(f,j,s);final,changes=reduce_content(d,1)
    assert [e.id for e in final.entries]==[e.id for e in d.entries] and changes
    assert report(final,s,f)['bullet_omissions'] and validate(final,s,f,j)['valid']

def test_non_overflow_qa_failure_never_drops_content(tmp_path,monkeypatch):
    from app.services import resume_tailoring_render as render
    f,j=regression();s=catalog(f,j,{});d=assemble(f,j,s);seen=[]
    def qa(path,current,log):
        seen.append(current.model_dump());return {'valid':False,'issues':['possible_text_overlap']}
    monkeypatch.setattr(render,'pdf_quality',qa)
    with pytest.raises(RenderFailure) as exc:compile_resume(d,s,f,j,tmp_path,Settings())
    assert exc.value.code=='resume_pdf_quality_failed' and len(seen)==3
    assert all(current==d.model_dump() for current in seen) and not exc.value.audit['corrections']

def test_no_space_is_explicit_and_never_shrinks_font_or_invents_omissions(tmp_path):
    f,j=regression();f['projects']*=6;s=catalog(f,j,{});d=assemble(f,j,s)
    with pytest.raises(RenderFailure) as exc:compile_resume(d,s,f,j,tmp_path,Settings())
    assert exc.value.code=='resume_insufficient_space' and len(exc.value.audit['attempts'])==3
    assert r'\LoadClass[11pt,letterpaper]' in (tmp_path/'resume.cls').read_text()
    assert len([e for e in d.entries if e.section=='projects'])==24

def test_original_verified_evidence_is_local_and_current_profile_unchanged(client,application):
    from sqlalchemy import select
    from app.db.models import CandidateProfile,VerificationRecord,TailoredResumeVersion
    f,j=regression();app=create(client,f)
    with application.state.session_factory() as db:
        profile=db.scalar(select(CandidateProfile));proof=VerificationRecord(user_id=profile.user_id,profile_id=profile.id,
            profile_revision=profile.revision,confirmed_values={'skills':f['skills']},source_evidence={'skills':[{'text':'Confirmed synthetic skill evidence'}]},applied_fields=['skills'])
        db.add(proof);db.commit()
    before=client.get('/api/v1/profile').json();row=start(client,app)
    assert row['status']=='needs_review', (row.get('error_code'),row['data'].get('render'))
    assert row['data']['original_evidence']
    assert 'original_evidence' not in row['data']['catalog']
    assert client.get('/api/v1/profile').json()==before

def test_legacy_sparse_version_requires_new_preservation_checks_before_approval(client,application):
    from sqlalchemy import select
    from app.db.models import TailoredResumeVersion
    from test_resume_tailoring import ROOT
    app=create(client,FACTS);row=start(client,app)
    assert row['status']=='needs_review'
    with application.state.session_factory() as db:
        saved=db.get(TailoredResumeVersion,row['id']);data=deepcopy(saved.data)
        data['catalog'].pop('content_policy_version');saved.data=data;db.commit()
    result=client.post(ROOT+'/'+row['id']+'/actions',json={'action':'approve','expected_revision':row['revision'],'reviewed':True})
    assert result.status_code==409 and result.json()['error']['code']=='resume_policy_update_required'

def test_raw_json_source_cannot_render_as_prose():
    f,j=regression();f['projects'][0]['description']='{"organization": "Example", "description": "raw object"}'
    s=catalog(f,j,{});d=assemble(f,j,s)
    assert any('Raw structured JSON' in v['reason'] for v in validate(d,s,f,j)['issues'])

def test_relevant_achievement_survives_full_profile_one_page_layout(tmp_path):
    f,j=regression()
    f['achievements']=['Runner-up, Python and NLP engineering hackathon.']
    f['education'][0]['institution']='Example College of Computer Engineering and Advanced Information Technology, Example National University, Aligarh, India'
    s=catalog(f,j,{});d=assemble(f,j,s)
    assert 'achievements-0' in s['protected_ids']
    final,a=compile_resume(d,s,f,j,tmp_path,Settings())
    assert a['quality']['valid'] and a['quality']['page_count']==1 and a['quality']['body_font_pt']==11
    text=PdfReader(tmp_path/'document.pdf').pages[0].extract_text()
    assert 'Runner-up' in text and 'Adobe Systems India' in text
    assert len([e for e in final.entries if e.section=='skills'])==36
