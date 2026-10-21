"""Synthetic-only tailoring, exact supplied class, real XeLaTeX/PDF and API contracts."""
from copy import deepcopy
from pathlib import Path
from hashlib import sha256
import json,time
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core.config import Settings
from app.db.models import TailoredResumeVersion,Document,ApplicationRecord,CandidateProfile,VerificationRecord
from app.schemas.resume_tailoring import ResumeStart,ResumeDraft,SECTIONS
from app.services.resume_tailoring_content import catalog,assemble,validate,edit_draft,FICTION
from app.services.resume_tailoring_render import compile_resume,populate,TEMPLATE,pdf_quality
from app.services.cover_letter_render import RenderFailure
from app.flows.resume_tailoring import ResumeTailoringFlow
from app.crews.resume_tailoring import propose
from app.providers.groq import GroqKeyManager
from test_cover_letters import FACTS as BASIC,JOB
from test_applications import create,act,finished

ROOT='/api/v1/resume-tailoring'
FACTS={**deepcopy(BASIC),'location':'Bengaluru, India',
    'education':[{**BASIC['education'][0],'period':'2023 – 2027','grade':'8.2/10','location':'Aligarh, India'}],
    'experience':[{**BASIC['experience'][0],'period':'May 2026 – July 2026','location':'Bengaluru, India'}],
    'projects':[{**BASIC['projects'][0],'role':'Developer','period':'January 2026 – April 2026','repository':'https://github.com/student/search?view=code&x=one_two#readme'},BASIC['projects'][1]],
    'skills':['Python','SQL','NLP','React','Docker','Git','PostgreSQL']}

def draft(facts=None,job=None):
    facts=deepcopy(facts or FACTS);job=deepcopy(job or JOB);source=catalog(facts,job,{})
    return assemble(facts,job,source),source

def wait(client,row):
    end=time.monotonic()+45
    while time.monotonic()<end:
        row=client.get(ROOT+'/'+row['id']).json()
        if not row['operation_active']:return row
        time.sleep(.05)
    raise AssertionError('Tailoring did not finish')

def start(client,app,**extra):
    app=client.get('/api/v1/applications/'+app['id']).json()
    response=client.post(ROOT+'/applications/'+app['id'],json={'expected_application_revision':app['revision'],'use_ai':False,**extra})
    assert response.status_code==202,response.text
    return wait(client,response.json())

def action(client,row,kind,**extra):
    response=client.post(ROOT+'/'+row['id']+'/actions',json={'action':kind,'expected_revision':row['revision'],**extra})
    assert response.status_code==200,response.text
    return wait(client,response.json())

def test_relevance_summary_skill_priorities_and_omissions():
    value,source=draft()
    assert validate(value,source,FACTS,JOB)['valid']
    assert 'backend development' in value.summary and 'Python' in value.summary
    assert not any(text in value.summary for text in ('Targeting ', 'Education:', 'Skills include'))
    skills=[e.title for e in value.entries if e.section=='skills']
    assert skills.index('Python')<skills.index('React')
    assert value.entries[0].section=='experience'
    assert not FICTION.search(populate(value))

@pytest.mark.parametrize('field,value',[('title','Invented Employer'),('subtitle','Chief Scientist'),('period','2020 – 2030'),('grade','10/10'),('technologies',['Kubernetes']),('links',[{'url':'https://fake.example','label':'Fake'}])])
def test_immutable_fields_and_unsupported_technologies_fail(field,value):
    d,s=draft();setattr(d.entries[0],field,value)
    assert not validate(d,s,FACTS,JOB)['valid']

def test_summary_metrics_bullet_changes_and_unknown_skills_fail():
    d,s=draft();d.summary+=' Led 200 engineers and increased revenue by 50%.'
    assert not validate(d,s,FACTS,JOB)['valid']
    d,s=draft();d.entries[0].bullets[0]['text']='Improved results by 99%.'
    assert not validate(d,s,FACTS,JOB)['valid']
    with pytest.raises(ValueError):assemble(FACTS,JOB,s,{'entry_ids':['invented-skill']})

def test_primary_degree_and_core_repair_do_not_promote_secondary_school(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path))
    facts=deepcopy(FACTS);facts['education']=[{'institution':'Example School','qualification':'Senior Secondary XII','period':'2021 – 2023'},*facts['education']]
    s=catalog(facts,JOB,{})
    assert 'B.Tech Computer Engineering' in s['summaries'][0]['text'] and 'Senior Secondary' not in s['summaries'][0]['text']
    selection={'summary_id':'focused','entry_ids':[id for id in s['selected_ids'] if not id.startswith('education-')],
        'bullet_ids':[b['id'] for e in s['entries'] if e['id'] in s['selected_ids'] and e['section']!='education' for b in e['bullets']],
        'section_order':[section for section in SECTIONS if section!='education']}
    import app.crews.resume_tailoring as crew
    monkeypatch.setattr(crew,'propose',lambda *args:(deepcopy(selection),{'engine':'crewai.Crew','status':'completed','model':'synthetic-transport'}))
    options=ResumeStart(expected_application_revision=1,use_ai=True,allow_external_processing=True).model_dump()
    result=ResumeTailoringFlow({'facts':facts,'job':JOB,'options':options,'context':{}},Settings(),GroqKeyManager([]),1,time.monotonic()+60).kickoff()
    assert result['provider']['status']=='repaired' and result['validation']['valid']
    assert [e['title'] for e in result['draft']['entries'] if e['section']=='education']==['Example University']

@pytest.mark.parametrize('case',['normal','missing_sections','missing_requirements','long_names','long_skills','special_characters','no_experience','no_links'])
def test_real_template_compile_one_page_and_selectable_text(case,tmp_path):
    facts=deepcopy(FACTS);job=deepcopy(JOB)
    if case=='missing_sections':facts={'name':'Student Example','skills':['Python']}
    if case=='missing_requirements':job['required_skills']=[];job['preferred_skills']=[]
    if case=='long_names':facts['projects'][0]['name']='Reliable retrieval and ranking service with structured Python and SQL evidence for student career exploration';facts['education'][0]['institution']='Example Institute of Computer Engineering and Advanced Information Technology'
    if case=='long_skills':facts['skills']+=['Python text retrieval and structured relational data processing tools']+['Confirmed Technology '+str(i) for i in range(40)]
    if case=='special_characters':facts['experience'][0]['organization']='R&D 50% $Lab #1_ {AI} ~ ^ \\';facts['projects'][0]['name']='Python & SQL_ #Search'
    if case=='no_experience':facts['experience']=[]
    if case=='no_links':facts['links']=[];facts['projects'][0]['repository']=''
    d,s=draft(facts,job);assert validate(d,s,facts,job)['valid']
    final,audit=compile_resume(d,s,facts,job,tmp_path,Settings())
    assert audit['quality']['page_count']==1 and audit['quality']['selectable_text'] and audit['quality']['fonts_embedded']
    assert audit['quality']['section_reading_order_checked'] and not audit['quality']['issues']
    assert sha256((tmp_path/'resume.cls').read_bytes()).hexdigest()==sha256((TEMPLATE/'resume.cls').read_bytes()).hexdigest()
    assert (tmp_path/'preview.png').is_file() and not FICTION.search((tmp_path/'document.tex').read_text())

def test_sample_content_and_empty_or_duplicate_sections_rejected():
    d,s=draft();d.name='Carl Johnson';assert not validate(d,s,FACTS,JOB)['valid']
    d,s=draft();d.section_order+=['projects'];assert not validate(d,s,FACTS,JOB)['valid']
    d,s=draft();d.section_order.remove('education');assert not validate(d,s,FACTS,JOB)['valid']
    d,s=draft();d.entries=[];assert not validate(d,s,FACTS,JOB)['valid']

def test_overflow_revised_with_complete_evidence_and_bounded_retries(tmp_path):
    facts=deepcopy(FACTS)
    prose=' '.join(f'Built a Python retrieval component for source module {n} and connected SQL queries to a documented validation process that checked query coverage, record consistency, ranking output, source references, missing values, error responses, representative examples and expected behaviour during a student engineering project.' for n in range(6))
    facts['experience']=[{**FACTS['experience'][0],'organization':'Example Lab '+str(i),'description':prose} for i in range(2)]
    facts['projects']=[{**FACTS['projects'][0],'name':'Python Search Project '+str(i),'description':prose} for i in range(3)]
    d,s=draft(facts)
    with pytest.raises(RenderFailure) as caught:compile_resume(d,s,facts,JOB,tmp_path,Settings())
    assert caught.value.code=='resume_insufficient_space'
    assert len(caught.value.audit['attempts'])==3 and caught.value.audit['corrections']
    assert len([e for e in d.entries if e.section=='experience'])==2
    assert len([e for e in d.entries if e.section=='projects'])==3

def test_compile_failure_retry_missing_engine_and_corrupt_pdf(tmp_path,monkeypatch):
    from app.services import latex_documents
    d,s=draft()
    with pytest.raises(RenderFailure) as e:compile_resume(d,s,FACTS,JOB,tmp_path,Settings(cover_letter_latex_path='/missing/xelatex'))
    assert e.value.code=='latex_engine_unavailable' and (tmp_path/'document.tex').is_file()
    calls=[]
    class Fail:
        returncode=1
        def __init__(self,*args,**kw):calls.append(kw['cwd'])
        def communicate(self,**kwargs):return b'! LaTeX Error: deliberate test failure',b'diagnostic'
    monkeypatch.setattr(latex_documents.subprocess,'Popen',Fail)
    with pytest.raises(RenderFailure) as e:compile_resume(d,s,FACTS,JOB,tmp_path,Settings())
    assert len(calls)==2 and calls[0]!=calls[1] and e.value.code=='latex_compile_failed'
    (tmp_path/'bad.pdf').write_bytes(b'bad')
    assert not pdf_quality(tmp_path/'bad.pdf',d)['valid']

def test_genuine_three_agent_crew_and_provider_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path));d,s=draft()
    class Transport:
        settings=Settings()
        def complete(self,messages,schema,**kwargs):
            key=kwargs['schema_name']
            if key.endswith('resumeselection'):return json.dumps({'entry_ids':s['selected_ids'],'reasons':['Relevant confirmed evidence']})
            if key.endswith('resumewriting'):return json.dumps({'summary_id':'focused','entry_ids':s['selected_ids'],'bullet_ids':[b['id'] for e in s['entries'] if e['id'] in s['selected_ids'] for b in e['bullets']],'section_order':list(SECTIONS)})
            return json.dumps({'unsupported_ids':[],'issues':[]})
    proposal,meta=propose(s,Settings(),GroqKeyManager([]),1,time.monotonic()+60,Transport())
    assert meta['engine']=='crewai.Crew' and len(meta['agents'])==3
    assert validate(assemble(FACTS,JOB,s,proposal),s,FACTS,JOB)['valid']
    p=ResumeStart(expected_application_revision=1,use_ai=True,allow_external_processing=True).model_dump()
    result=ResumeTailoringFlow({'facts':FACTS,'job':JOB,'options':p,'context':{}},Settings(),GroqKeyManager([]),1,time.monotonic()+60).kickoff()
    assert result['provider']['status']=='fallback' and result['validation']['valid']

def test_edits_supported_variants_and_section_regeneration(client):
    app=create(client,FACTS);first=start(client,app)
    assert first['status']=='needs_review',first.get('error_code')
    summary=first['data']['catalog']['summaries'][1]['text']
    second=start(client,app,base_version_id=first['id'],edits={'summary':summary})
    assert second['status']=='needs_review' and second['data']['draft']['summary']==summary and second['version']==2
    third=start(client,app,base_version_id=second['id'],regenerate_section='projects')
    assert third['data']['draft']['summary']==summary and 'summary' in third['data']['retained_sections']
    bad=start(client,app,base_version_id=third['id'],edits={'summary':'I have 15 years of leadership experience.'})
    assert bad['status']=='validation_failed' and not bad['pdf_available']

def test_approve_attach_original_preservation_and_replacement(client,application):
    app=finished(client,act(client,create(client,FACTS),'prepare'))
    original=b'Original private resume remains byte-identical.'
    uploaded=client.post('/api/v1/documents/upload',files={'file':('original.txt',original,'text/plain')});assert uploaded.status_code==201
    profile_before=client.get('/api/v1/profile').json()
    row=start(client,app);assert row['status']=='needs_review',row
    assert client.post(ROOT+'/'+row['id']+'/actions',json={'action':'approve','expected_revision':row['revision']}).status_code==422
    row=action(client,row,'approve',reviewed=True)
    current=client.get('/api/v1/applications/'+app['id']).json();row=action(client,row,'attach',expected_application_revision=current['revision'])
    assert row['status']=='attached'
    current=client.get('/api/v1/applications/'+app['id']).json()
    assert current['status']=='needs_user_review' and not current['approval']
    assert any(d['role']=='resume' and d['document_id']==row['document_id'] for d in current['draft']['documents'])
    from app.services.applications import attachment_packet
    with application.state.session_factory() as db:
        assert attachment_packet(db,db.get(ApplicationRecord,app['id']))[0]['role']=='resume'
        assert db.get(Document,uploaded.json()['id']).content==original
    assert client.get('/api/v1/profile').json()==profile_before
    second=action(client,start(client,app),'approve',reviewed=True);current=client.get('/api/v1/applications/'+app['id']).json()
    second=action(client,second,'attach',expected_application_revision=current['revision'])
    assert client.get(ROOT+'/'+row['id']).json()['status']=='replaced'

def test_input_modes_passive_tex_and_unconfirmed_pdf(client):
    app=create(client,FACTS)
    tex=b'\\documentclass{resume}\n\\begin{document}\n\\input{/etc/passwd}\\write18{never_execute}\n\\end{document}'
    response=client.post(ROOT+'/sources',files={'file':('reference.tex',tex,'text/plain')});assert response.status_code==201
    source=response.json()['id']
    row=start(client,app,input_mode='latex_source',source_document_id=source)
    assert row['status']=='needs_review' and row['data']['input_source']['authority'].startswith('Confirmed profile')
    generated=client.get(ROOT+'/'+row['id']+'/artifacts/source').text
    assert '/etc/passwd' not in generated and 'write18' not in generated
    assert client.get('/api/v1/documents/'+source+'/original').content==tex
    response=client.post(ROOT+'/applications/'+app['id'],json={'expected_application_revision':client.get('/api/v1/applications/'+app['id']).json()['revision'],'use_ai':False,'input_mode':'processed_pdf','source_document_id':source})
    assert response.status_code==409
    row=start(client,app,input_mode='template_profile');assert row['status']=='needs_review'

def test_previously_processed_confirmed_pdf_is_read_only_input(client,application):
    from pypdf import PdfWriter
    from io import BytesIO
    app=create(client,FACTS);writer=PdfWriter();writer.add_blank_page(width=612,height=792);stream=BytesIO();writer.write(stream)
    response=client.post('/api/v1/documents/upload',files={'file':('processed-resume.pdf',stream.getvalue(),'application/pdf')});assert response.status_code==201
    doc=response.json()
    with application.state.session_factory() as db:
        profile=db.scalar(select(CandidateProfile))
        db.add(VerificationRecord(user_id=profile.user_id,profile_id=profile.id,document_id=doc['id'],profile_revision=profile.revision,confirmed_values=profile.facts,selected_values=profile.facts,applied_fields=list(profile.facts),source_evidence={}))
        db.commit()
    row=start(client,app,input_mode='processed_pdf',source_document_id=doc['id'])
    assert row['status']=='needs_review' and row['data']['input_source']['document_id']==doc['id']
    assert client.get('/api/v1/documents/'+doc['id']+'/original').content==stream.getvalue()

def test_cross_job_resume_and_tampered_pdf_cannot_be_approved_or_selected(client,application):
    app=finished(client,act(client,create(client,FACTS),'prepare'));row=start(client,app)
    with application.state.session_factory() as db:
        path=Path(db.get(TailoredResumeVersion,row['id']).artifact_dir)/'document.pdf';original=path.read_bytes();path.write_bytes(original+b'changed')
    response=client.post(ROOT+'/'+row['id']+'/actions',json={'action':'approve','expected_revision':row['revision'],'reviewed':True});assert response.status_code==409
    path.write_bytes(original);row=action(client,row,'approve',reviewed=True)
    job=client.get('/api/v1/jobs',params={'category':'backend','page_size':1,'page':3}).json()['items'][0]
    client.put('/api/v1/matching/bookmarks/'+job['id'])
    other=client.post('/api/v1/applications',json={'job_id':job['id']});assert other.status_code==201
    other=finished(client,act(client,other.json(),'prepare'))
    response=client.post('/api/v1/applications/'+other['id']+'/actions',json={'action':'review','expected_revision':other['revision'],'documents':[{'document_id':row['document_id'],'role':'resume','approved':True}]})
    assert response.status_code==409 and response.json()['error']['code']=='application_tailored_resume_mismatch'

def test_owner_revision_stale_source_and_cancel_recovery(client,application,monkeypatch):
    app=create(client,FACTS);row=start(client,app)
    assert client.post(ROOT+'/'+row['id']+'/actions',json={'action':'approve','expected_revision':1,'reviewed':True}).status_code==409
    with TestClient(application,headers={'X-Workspace-Client':'careeros-web'}) as other:
        other.post('/api/v1/session')
        assert other.get(ROOT+'/'+row['id']).status_code==404
        assert other.get(ROOT+'/'+row['id']+'/artifacts/pdf').status_code==404
    current=client.get('/api/v1/profile').json()
    updated={**FACTS,'name':'Changed Candidate','expected_revision':current['revision']}
    assert client.put('/api/v1/profile',json=updated).status_code==200
    assert client.post(ROOT+'/'+row['id']+'/actions',json={'action':'approve','expected_revision':row['revision'],'reviewed':True}).status_code==409

def test_cancel_before_worker_and_restart_recovery(client,application,monkeypatch):
    app=create(client,FACTS);processor=application.state.resume_tailoring_processor
    monkeypatch.setattr(processor,'submit',lambda *a:processor.capacity.release())
    payload={'expected_application_revision':app['revision'],'use_ai':False}
    response=client.post(ROOT+'/applications/'+app['id'],json=payload);assert response.status_code==202
    row=action(client,response.json(),'cancel');assert row['status']=='cancelled'
    payload['expected_application_revision']=client.get('/api/v1/applications/'+app['id']).json()['revision']
    response=client.post(ROOT+'/applications/'+app['id'],json=payload);processor.recover_interrupted()
    assert client.get(ROOT+'/'+response.json()['id']).json()['error_code']=='resume_tailoring_interrupted'
