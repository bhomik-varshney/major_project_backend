"""Cover-letter-only validation: synthetic profiles, real fonts/LaTeX, no private exports."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import json,time
from unittest.mock import Mock
import pytest
from PIL import Image,ImageDraw
from pydantic import ValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import ApplicationRecord,ApplicationEvent,CoverLetterVersion,Document,SignatureAsset
from app.schemas.cover_letters import CoverStart,LetterContent,RelevanceSelection,WritingSelection,ContentReview,SECTION_KEYS
from app.services.cover_letter_content import research,sentence_catalog,assemble,validate_content
from app.services.cover_letter_render import (escape,href,signature_image,compile_letter,pdf_quality,populate,RenderFailure,engine_path)
from app.flows.cover_letters import CoverLetterFlow
from app.crews.cover_letters import generate,CoverLLM,build_cover_crew
from app.providers.groq import GroqKeyManager,ProviderUnavailable
from test_applications import create,act,finished

ROOT='/api/v1/cover-letters'
FACTS={'name':'Student Example','email':'student@example.com','phone':'+91 9000000000','skills':['Python','SQL','NLP'],
    'education':[{'qualification':'B.Tech Computer Engineering','institution':'Example University'}],
    'experience':[{'organization':'Example Software','role':'Developer Intern','description':'Built a Python API for an internal search service.','technologies':['Python','SQL']}],
    'projects':[{'name':'Search Service','description':'Developed a Python retrieval service that combined SQL queries with text search.','technologies':['Python','SQL']},
        {'name':'Text Classification','description':'Built an NLP classifier and evaluated its predictions on a held-out data split.','technologies':['Python','NLP']}],
    'career_objective':'Develop reliable backend and AI applications.',
    'links':['https://linkedin.com/in/student-example','https://student-portfolio.example.com']}
JOB={'id':'test-job','title':'Python Engineer Intern','company':'Research & Development Ltd','required_skills':['Python','SQL'],'preferred_skills':['NLP']}

def image_bytes(transparent=True):
    # Clearly synthetic QA ink, never a person's generated signature.
    im=Image.new('RGBA',(400,100),(255,255,255,0 if transparent else 255));d=ImageDraw.Draw(im)
    d.line([(20,75),(75,25),(90,70),(155,35),(220,68),(340,40)],fill=(20,20,70,255),width=3)
    stream=BytesIO();im.save(stream,'PNG');return stream.getvalue()

def content(facts=None,job=None):
    facts=deepcopy(facts or FACTS);job=deepcopy(job or JOB);p=research(facts,job,{})
    catalog=sentence_catalog(facts,job,p)
    return assemble(facts,job,{'document_date':'2026-10-21'},catalog,[s.id for s in catalog]),catalog

def wait(client,row):
    end=time.monotonic()+40
    while time.monotonic()<end:
        row=client.get(f'{ROOT}/{row["id"]}').json()
        if not row['operation_active']:return row
        time.sleep(.05)
    raise AssertionError('Cover letter did not finish')

def start(client,application,**extra):
    app=client.get('/api/v1/applications/'+application['id']).json()
    response=client.post(f'{ROOT}/applications/{app["id"]}',json={'expected_application_revision':app['revision'],'document_date':'2026-10-21','use_ai':False,'include_signature':False,**extra})
    assert response.status_code==202,response.text
    return wait(client,response.json())

def action(client,row,kind,**extra):
    response=client.post(f'{ROOT}/{row["id"]}/actions',json={'action':kind,'expected_revision':row['revision'],**extra})
    assert response.status_code==200,response.text
    return wait(client,response.json())

def test_structured_grounded_claims_and_unsupported_rejection():
    value,catalog=content()
    check=validate_content(value,catalog,FACTS,JOB)
    assert check['valid'] and value.signature_name==FACTS['name']
    value.claims[0].text='I led 200 engineers and increased revenue by 40%.'
    assert not validate_content(value,catalog,FACTS,JOB)['valid']
    value,_=content();value.opening+=' I invented a new award.'
    assert not validate_content(value,catalog,FACTS,JOB)['valid']

@pytest.mark.parametrize('case',['student','internships_only','no_portfolio','long_project','many_requirements','missing_company','special_characters'])
def test_validation_cases_real_template_pdf(case,tmp_path):
    facts=deepcopy(FACTS);job=deepcopy(JOB)
    if case=='student':facts.pop('experience')
    if case=='internships_only':assert all('Intern' in e['role'] for e in facts['experience'])
    if case=='no_portfolio':facts['links']=facts['links'][:1]
    if case=='long_project':facts['projects'][0]['description']='Built a Python search service. '+'Evaluated SQL retrieval quality. '*65;facts['projects'][0]['name']='Reliable Python Search and Retrieval Service with SQL for Academic Project Evidence'
    if case=='many_requirements':job['required_skills']+=['Kubernetes','AWS','Java','Docker','TypeScript','FastAPI','NLP']
    if case=='missing_company':job['company']=''
    if case=='special_characters':job['company']='R&D 50% $Lab #1_ {AI} ~ ^ \\';facts['projects'][0]['name']='Python & SQL #1_ {search}'
    value,catalog=content(facts,job)
    assert validate_content(value,catalog,facts,job)['valid']
    assert engine_path(Settings()),'Install documented XeLaTeX dependencies for this integration test.'
    audit=compile_letter(value,facts,None,38,tmp_path/case,Settings())
    assert audit['quality']['valid'] and audit['quality']['page_count']==1
    assert audit['quality']['fonts_embedded'] and audit['quality']['selectable_text']
    assert (tmp_path/case/'preview.png').is_file()
    assert (tmp_path/case/'letter.tex').is_file()
    if case=='missing_company':assert value.salutation=='Dear Hiring Manager,'
    assert 'John Snow' not in (tmp_path/case/'letter.tex').read_text()

def test_latex_and_url_escaping_and_unsafe_links():
    assert escape('&%$#_{}~^\\')==r'\&\%\$\#\_\{\}\textasciitilde{}\textasciicircum{}\textbackslash{}'
    assert r'\&' in href('https://example.com/?a=1&b=two_words#part','A & B')
    assert r'\textbackslash{}input' in escape(r'\input{/etc/passwd}')
    for url in ['javascript:alert(1)','https://user:password@example.com','https://example.com/?token=private','not-url']:
        with pytest.raises(AppError):href(url,'Link')

def test_incomplete_profile_omits_contacts_and_long_skill_labels_compile(tmp_path):
    facts={'name':'Student Example','skills':['Python for text retrieval and structured data processing with relational SQL databases']}
    job={**JOB,'required_skills':['Python']}
    value,catalog=content(facts,job)
    assert validate_content(value,catalog,facts,job)['valid']
    audit=compile_letter(value,facts,None,38,tmp_path,Settings())
    assert audit['quality']['valid'] and not value.signature_email
    source=(tmp_path/'letter.tex').read_text()
    assert 'mailto:' not in source and 'null' not in source

@pytest.mark.parametrize('transparent',[True,False])
def test_signature_validation_scaling_and_placement(transparent,tmp_path):
    data,metadata=signature_image(image_bytes(transparent))
    assert metadata['transparent']==transparent
    value,_=content()
    audit=compile_letter(value,FACTS,data,50,tmp_path/'signature',Settings())
    assert audit['quality']['signature_present'] and audit['quality']['signature_overlap_checked']
    assert audit['quality']['valid']
    with Image.open(BytesIO(data)) as im:assert im.size==(metadata['width'],metadata['height'])

def test_invalid_signature_rejected():
    for data in [b'',b'<svg onload="x"></svg>',b'invalid',b'a'*(2*1024*1024+1)]:
        with pytest.raises(AppError):signature_image(data)
    im=Image.new('RGB',(200,100),'white');buf=BytesIO();im.save(buf,'PNG')
    with pytest.raises(AppError):signature_image(buf.getvalue())

def test_missing_engine_retains_source(tmp_path):
    value,_=content()
    with pytest.raises(RenderFailure) as error:compile_letter(value,FACTS,None,38,tmp_path,Settings(cover_letter_latex_path='/missing/xelatex'))
    assert error.value.code=='latex_engine_unavailable' and (tmp_path/'letter.tex').is_file()

def test_compile_failure_and_timeout_retry_fresh_directory(tmp_path,monkeypatch):
    from app.services import cover_letter_render as renderer
    runs=[]
    class Failed:
        returncode=1
        def __init__(self,*a,**kw):runs.append(kw['cwd'])
        def communicate(self,**kw):return b'failed',b'diagnostic'
    monkeypatch.setattr(renderer.subprocess,'Popen',Failed)
    value,_=content()
    with pytest.raises(RenderFailure) as error:compile_letter(value,FACTS,None,38,tmp_path,Settings())
    assert error.value.code=='latex_compile_failed' and len(runs)==2 and runs[0]!=runs[1]
    assert all(a['exit_code']==1 for a in error.value.audit['attempts'])
    assert (tmp_path/'attempt-2.stderr').read_text()=='diagnostic'

def test_placeholder_and_bad_pdf_detection(tmp_path):
    value,catalog=content();value.opening='Lorem ipsum John Snow'
    assert not validate_content(value,catalog,FACTS,JOB)['valid']
    (tmp_path/'bad.pdf').write_bytes(b'not a PDF')
    assert not pdf_quality(tmp_path/'bad.pdf',value,None)['valid']

def test_real_overflow_requires_bounded_spacing_revision(tmp_path):
    value,_=content()
    # Sweep lengths to locate a one-page boundary with this exact supplied font.
    outcomes=[]
    for count in (35,105):
        trial=value.model_copy(deep=True)
        trial.experience+=' '+'The project used Python and SQL to evaluate reliable retrieval results. '*count
        try:audit=compile_letter(trial,FACTS,None,38,tmp_path/str(count),Settings());outcomes.append(audit)
        except RenderFailure as error:outcomes.append(error.audit)
    assert any(len(a['attempts'])==3 for a in outcomes)
    assert any(a.get('quality',{}).get('valid') and len(a['attempts'])>1 for a in outcomes)
    assert any('page_overflow' in a.get('quality',{}).get('issues',[]) for a in outcomes)
    assert all(len(a['attempts'])<=3 for a in outcomes)

def test_crew_is_real_and_validated_with_mock_transport(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path))
    value,catalog=content();plan=research(FACTS,JOB,{})
    class Transport:
        settings=Settings()
        def complete(self,messages,schema,**kw):
            name=kw['schema_name']
            if name.endswith('relevanceselection'):return json.dumps({'evidence_ids':plan['selected_evidence_ids'],'warnings':[]})
            if name.endswith('writingselection'):return json.dumps({'sentence_ids':[s.id for s in catalog],'warnings':[]})
            return json.dumps({'unsupported_sentence_ids':[],'issues':[]})
    ids,metadata=generate(plan,catalog,Settings(),GroqKeyManager([]),1,time.monotonic()+60,Transport())
    assert ids==[s.id for s in catalog] and metadata['engine']=='crewai.Crew' and len(metadata['agents'])==3
    class Bad(Transport):
        def complete(self,*a,**kw):
            if kw['schema_name'].endswith('writingselection'):return json.dumps({'sentence_ids':['invented'], 'warnings':[]})
            return super().complete(*a,**kw)
    with pytest.raises(ValueError):generate(plan,catalog,Settings(),GroqKeyManager([]),1,time.monotonic()+60,Bad())

def test_provider_failure_flow_is_explicit_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR',str(tmp_path))
    p=CoverStart(expected_application_revision=1,document_date='2026-10-21',use_ai=True,allow_external_processing=True).model_dump(mode='json')
    result=CoverLetterFlow({'facts':FACTS,'job':JOB,'options':p,'context':{}},Settings(),GroqKeyManager([]),1,time.monotonic()+60).kickoff()
    assert result['validation']['valid'] and result['provider']['status']=='fallback'
    assert result['provider']['error_code']=='groq_not_configured'

def test_missing_job_title_no_confirmed_profile_and_consent(client,application,profile):
    app=create(client,profile)
    bad=client.post(f'{ROOT}/applications/{app["id"]}',json={'expected_application_revision':app['revision'],'document_date':'2026-10-21'})
    assert bad.status_code==422
    with application.state.session_factory() as db:
        row=db.get(ApplicationRecord,app['id']);row.job_snapshot={**row.job_snapshot,'title':''};db.commit()
    response=client.post(f'{ROOT}/applications/{app["id"]}',json={'expected_application_revision':app['revision'],'document_date':'2026-10-21','use_ai':False})
    assert response.status_code==422 and response.json()['error']['code']=='cover_inputs_missing'

def test_waiting_signature_render_approve_attach_and_library(client,application,profile):
    app=create(client,profile)
    row=start(client,app,include_signature=True)
    assert row['status']=='waiting_for_signature' and not row['pdf_available']
    response=client.post(ROOT+'/signatures',data={'signer_name':'Student Example','approved':'true'},files={'file':('signature.png',image_bytes(),'image/png')})
    assert response.status_code==201,response.text
    asset=response.json()
    row=action(client,row,'render',signature_id=asset['id'],signature_approved=True)
    assert row['status']=='preview_ready' and row['data']['signature']['included']
    assert client.get(f'{ROOT}/{row["id"]}/artifacts/pdf').content.startswith(b'%PDF-')
    assert client.get(f'{ROOT}/{row["id"]}/artifacts/preview').content.startswith(b'\x89PNG')
    log=client.get(f'{ROOT}/{row["id"]}/artifacts/compile-log')
    assert log.status_code==200 and 'XeTeX' in log.text
    assert client.post(f'{ROOT}/{row["id"]}/actions',json={'action':'approve','expected_revision':row['revision']}).status_code==422
    row=action(client,row,'approve',reviewed=True)
    assert row['status']=='approved' and row['document_id']
    app=client.get('/api/v1/applications/'+app['id']).json()
    row=action(client,row,'attach',expected_application_revision=app['revision'])
    assert row['status']=='attached'
    app=client.get('/api/v1/applications/'+app['id']).json()
    assert app['status']=='job_selected'
    assert any(e['payload'].get('cover_letter_id')==row['id'] for e in client.get(f'/api/v1/applications/{app["id"]}/timeline').json())
    assert any(d['id']==row['document_id'] for d in client.get('/api/v1/documents').json())

def test_edits_regeneration_versions_and_replacement(client,profile):
    app=create(client,profile);first=start(client,app)
    assert first['status']=='preview_ready',first
    first=action(client,first,'approve',reviewed=True)
    app=client.get('/api/v1/applications/'+app['id']).json();first=action(client,first,'attach',expected_application_revision=app['revision'])
    second=start(client,app,base_version_id=first['id'],edits={'opening':f'Please consider my application for the {first["job"]["title"]} role at {first["job"]["company"]}. I would welcome an opportunity to discuss my project work.'},personal_sentence='I am particularly interested in contributing to practical engineering work.',user_text_confirmed=True,tone='warm')
    assert second['status']=='preview_ready',second
    assert second['version']==2 and 'opening' in second['data']['validation']['user_sections']
    second=action(client,second,'approve',reviewed=True)
    app=client.get('/api/v1/applications/'+app['id']).json();second=action(client,second,'attach',expected_application_revision=app['revision'])
    assert client.get(f'{ROOT}/{first["id"]}').json()['status']=='replaced'
    third=start(client,app,base_version_id=second['id'],regenerate_section='closing',tone='concise')
    assert third['data']['content']['opening']==second['data']['content']['opening']
    assert len(client.get(ROOT,params={'application_id':app['id']}).json())==3

def test_signature_replacement_is_new_asset_and_new_version(client,profile):
    app=create(client,profile);ids=[]
    for transparent in (True,False):
        response=client.post(ROOT+'/signatures',data={'signer_name':'Student Example','approved':'true'},files={'file':('signature.png',image_bytes(transparent),'image/png')})
        assert response.status_code==201;ids.append(response.json()['id'])
    first=start(client,app,include_signature=True,signature_id=ids[0],signature_approved=True)
    second=start(client,app,include_signature=True,signature_id=ids[1],signature_approved=True)
    assert first['signature_id']!=second['signature_id'] and first['data']['signature']['approved_by_user']

def test_owned_assets_approval_and_stale_profile(client,application,profile):
    app=create(client,profile);row=start(client,app)
    original=client.get('/api/v1/profile').json()
    payload={k:v for k,v in profile.items()};payload.update(name='Changed Name',expected_revision=original['revision'])
    assert client.put('/api/v1/profile',json=payload).status_code==200
    response=client.post(f'{ROOT}/{row["id"]}/actions',json={'action':'approve','expected_revision':row['revision'],'reviewed':True})
    assert response.status_code==409
    with TestClient(application,headers={'X-Workspace-Client':'careeros-web'}) as other:
        other.post('/api/v1/session')
        for suffix in ('','/artifacts/pdf','/artifacts/source'):
            assert other.get(f'{ROOT}/{row["id"]}'+suffix).status_code==404

def test_cancel_and_restart_recovery_preserve_user_data(client,application,profile,monkeypatch):
    app=create(client,profile)
    monkeypatch.setattr(application.state.cover_letter_processor,'submit',lambda *args:application.state.cover_letter_processor.capacity.release())
    response=client.post(f'{ROOT}/applications/{app["id"]}',json={'expected_application_revision':app['revision'],'document_date':'2026-10-21','use_ai':False})
    row=response.json();row=action(client,row,'cancel')
    assert row['status']=='cancelled' and not row['operation_active']
    app=client.get('/api/v1/applications/'+app['id']).json()
    response=client.post(f'{ROOT}/applications/{app["id"]}',json={'expected_application_revision':app['revision'],'document_date':'2026-10-21','use_ai':False})
    application.state.cover_letter_processor.recover_interrupted()
    assert client.get(f'{ROOT}/{response.json()["id"]}').json()['error_code']=='cover_interrupted'
    assert client.get('/api/v1/profile').json()['name']==profile['name']

def test_attachment_enters_existing_reviewed_mcp_packet(client,application,profile):
    app=finished(client,act(client,create(client,profile),'prepare'))
    row=action(client,start(client,app),'approve',reviewed=True)
    app=client.get('/api/v1/applications/'+app['id']).json();row=action(client,row,'attach',expected_application_revision=app['revision'])
    app=client.get('/api/v1/applications/'+app['id']).json()
    assert app['status']=='needs_user_review' and not app['approval']
    assert app['draft']['documents'][0]['document_id']==row['document_id']
    from app.services.applications import attachment_packet
    with application.state.session_factory() as db:
        packet=attachment_packet(db,db.get(ApplicationRecord,app['id']))
        assert packet[0]['role']=='cover_letter' and packet[0]['content_type']=='application/pdf'

def test_tampered_pdf_and_cross_application_document_selection_fail_closed(client,application,profile):
    app=finished(client,act(client,create(client,profile),'prepare'))
    row=start(client,app)
    with application.state.session_factory() as db:
        path=Path(db.get(CoverLetterVersion,row['id']).artifact_dir)/'letter.pdf'
        original=path.read_bytes();path.write_bytes(original+b'tamper')
    response=client.post(f'{ROOT}/{row["id"]}/actions',json={'action':'approve','expected_revision':row['revision'],'reviewed':True})
    assert response.status_code==409
    path.write_bytes(original);row=action(client,row,'approve',reviewed=True)
    jobs=client.get('/api/v1/jobs',params={'category':'backend','page_size':2,'page':2}).json()['items']
    other=jobs[0];client.put('/api/v1/matching/bookmarks/'+other['id'])
    response=client.post('/api/v1/applications',json={'job_id':other['id']});assert response.status_code==201
    second=finished(client,act(client,response.json(),'prepare'))
    response=client.post(f'/api/v1/applications/{second["id"]}/actions',json={'action':'review','expected_revision':second['revision'],'documents':[{'document_id':row['document_id'],'role':'cover_letter','approved':True}]})
    assert response.status_code==409 and response.json()['error']['code']=='application_cover_mismatch'
