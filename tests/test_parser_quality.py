import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import CandidateProfile
from app.schemas.extraction import AIProposal, StructuredResume
from app.services.groq_extraction import enhance
from app.services.resume_presenter import review_view
from app.services.resume_structure import anchored_ai_values, deterministic_extract, detect_heading
from app.services.resume_text import extract_text
from scripts.evaluate_extraction import evaluate, FIXTURES
from test_document_processing import upload, finish, verify_payload, BASE


def blocks(text):
    return [{'block_id':i,'page':1,'paragraph':i,'text':line} for i,line in enumerate(text.splitlines(),1)]


@pytest.mark.parametrize('heading', ['Summary','Professional Summary','Profile','Profile Summary','Career Summary','Career Objective','Objective','Professional Objective','Job Objective','About Me','Overview','Personal Statement','Executive Summary','Career Profile'])
def test_complete_statement_aliases_stop_at_next_section(heading):
    values,evidence,_ = deterministic_extract(blocks(f'Nisha Example\n{heading}:\nSeeking backend experience.\nBuilding useful services.\nSkills:\nPython'))
    assert values['career_objective'] == 'Seeking backend experience.\nBuilding useful services.'
    assert evidence['career_objective']['heading'] == heading
    assert evidence['career_objective']['statement_type'] == ('objective' if 'Objective' in heading else 'summary' if 'Summary' in heading else 'profile_statement')
    assert len(evidence['career_objective']['references']) == 2
    assert values['skills'] == ['Python']


@pytest.mark.parametrize('heading,field', [('01) TECHNICAL SKILLS —','skills'),('II. ACADEMIC BACKGROUND:','education'),('03. WORK EXPERIENCE','experience'),('SELECTED PROJECTS:','projects'),('4) PROFESSIONAL CERTIFICATIONS','certifications'),('EXTRA-CURRICULAR ACHIEVEMENTS','achievements'),('O N L I N E  P R O F I L E S','links'),('Tools & Technologies:','skills')])
def test_formatting_and_heading_boundaries(heading,field):
    assert detect_heading(heading)[0] == field


@pytest.mark.parametrize('case',json.loads((FIXTURES/'cases.json').read_text()),ids=lambda c:c['file'])
def test_corpus_source_and_grouping(case, application):
    settings=application.state.settings
    if case.get('error'):
        with pytest.raises(AppError) as exc:
            extract_text((FIXTURES/case['file']).read_bytes(),case['file'],str(uuid4()),settings)
        assert exc.value.code == case['error']
        return
    text,_,warnings=extract_text((FIXTURES/case['file']).read_bytes(),case['file'],str(uuid4()),settings)
    values,evidence,parse_warnings=deterministic_extract(text)
    rich,proof,skills=review_view(values,evidence,'awaiting_review',warnings=warnings+parse_warnings)
    StructuredResume.model_validate(rich)
    assert [p['name'] for p in values['projects']] == case['project_names']
    assert [p['technologies'] for p in values['projects']] == case['project_technologies']
    assert all(not skill['verified'] for skill in skills)
    assert rich['profile_statement']['text']['verified'] is False
    if case.get('warning'):
        assert any(case['warning'].lower() in w.lower() for w in warnings+parse_warnings)


def test_corpus_evaluation_has_no_unsupported_facts():
    result,_=evaluate()
    assert result['resumes'] == 15
    assert result['fields_evaluated'] >= 100
    assert result['missing'] == result['incorrect'] == 0
    assert result['unsupported_leaves'] == result['unsupported_projects'] == result['unsupported_technology_associations'] == result['unexpected_skills'] == 0


def test_no_arbitrary_summary_degree_or_project_from_employment():
    values,proof,warnings=deterministic_extract(blocks('Nisha Example\nEducation:\nExample University\nCourses:\nPython Workshop\nExperience:\nProject Manager | Example Labs | 2025-2026\n- Managed academic courses.\nReferences\nReact Engineer'))
    assert values['career_objective'] == ''
    assert values['education'] == values['projects'] == []
    assert values['skills'] == ['Python']  # Explicit course technology, never React from a title.
    assert any('Profile Summary / Objective not detected' in w for w in warnings)


def test_uncertain_project_title_is_explicitly_reviewable():
    values,proof,_=deterministic_extract(blocks('Nisha Example\nProjects:\n- Campus Tool\n- Built a directory.'))
    assert values['projects'][0]['name'] == 'Campus Tool'
    assert proof['projects.0.name']['classification'] == 'ambiguous'
    assert proof['projects.0.name']['warning']


@pytest.mark.parametrize('single_block', [False, True])
def test_ai_cannot_borrow_technology_or_project_from_other_entry(single_block):
    text=blocks('Nisha Example\nProjects:\nCampus Tool\nTechnologies: React\nBudget Tool\nTechnologies: Python\nExperience:\nBackend Intern | Example Labs | 2025-2026\n- Built internal APIs.')
    proposal=AIProposal.model_validate({'values':{'projects':[{'name':'Campus Tool','technologies':['Python']},{'name':'Backend Intern'}]},'claims':[
        {'path':'projects.0.name','block_id':3,'quote':'Campus Tool','confidence':'high'},
        {'path':'projects.0.technologies.0','block_id':6,'quote':'Technologies: Python','confidence':'high'},
        {'path':'projects.1.name','block_id':8,'quote':'Backend Intern | Example Labs | 2025-2026','confidence':'high'},
    ]})
    if single_block:
        text = [{'block_id': 1, 'page': 1, 'paragraph': 1, 'text': '\n'.join(block['text'] for block in text)}]
        for claim in proposal.claims:
            claim.block_id = 1
    values,proof,rejected=anchored_ai_values(proposal,text)
    assert values['projects'][0]['name'] == 'Campus Tool'
    assert values['projects'][0]['technologies'] == []
    assert len(values['projects']) == 1
    assert rejected == 2


def test_valid_rich_groq_json_is_validated_and_client_confirmations_ignored(application):
    text=blocks('Nisha Example\nObjective:\nSeeking backend experience.\nSkills:\nPython')
    local,proof,warnings=deterministic_extract(text)
    rich,_,_=review_view(local,proof,'verified',['name','career_objective','skills'],warnings=warnings)
    def provider_view(node):
        if isinstance(node, list):
            return [provider_view(item) for item in node]
        if not isinstance(node, dict):
            return node
        if 'value' in node:
            allowed = ['value', 'evidence', 'confidence', 'classification', 'method', 'review_status', 'warning']
            result = {key: node[key] for key in allowed}
            result.update(method='groq', review_status='pending')
            if result['evidence']:
                result['evidence'] = {key: result['evidence'][key] for key in ['block_id', 'quote']}
            return result
        return {key: provider_view(value) for key, value in node.items()}
    rich = provider_view(rich)
    def handler(request):
        payload=json.loads(request.content)
        schema=payload['response_format']['json_schema']['schema']
        assert 'personal_info' in schema['properties'] and 'extraction_metadata' in schema['properties']
        assert 'resume_blocks' in payload['messages'][1]['content']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(rich)}}]})
    from pydantic import SecretStr
    application.state.settings.groq_api_key=SecretStr('synthetic-key')
    factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw)
    values,evidence,rejected=enhance(text,application.state.settings,client_factory=factory)
    assert values['skills'] == ['Python']
    assert evidence['skills.0']['verified'] is False
    assert rejected == 0


def test_table_order_and_pdf_positions(application):
    settings=application.state.settings
    docx,_,_=extract_text((FIXTURES/'table_education.docx').read_bytes(),'table.docx',str(uuid4()),settings)
    assert docx[1]['style'] == 'Heading1' and docx[1]['bold']
    assert docx[2]['cells'][0] == 'Degree'
    assert docx[3]['cells'][0] == 'B.Tech Computer Science'
    assert docx[-1]['list_item']
    pdf,_,warnings=extract_text((FIXTURES/'two_column.pdf').read_bytes(),'columns.pdf',str(uuid4()),settings)
    assert all(p['page']==1 and p.get('position') for p in pdf)
    assert any('multi-column' in warning for warning in warnings)


def test_rich_review_edit_removal_keeps_correct_entry_evidence(client):
    doc=upload(client,b'Nisha Example\nProjects:\nFirst Tool\nTechnologies: React\n- Built a directory.\nSecond Tool\nTechnologies: Python\n- Built a planner.\n')
    finish(client,doc)
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert data['structured']['profile_statement']['text']['classification']=='not_found'
    assert data['structured']['projects'][1]['name']['verified'] is False
    second_source=data['evidence']['projects.1.technologies.0']['source']
    data['values']['projects'].pop(0)
    edited=client.put(f'{BASE}/{doc}/extracted-data',json={'processing_id':data['id'],'expected_revision':data['revision'],'values':data['values']})
    assert edited.status_code==200,edited.text
    data=edited.json()
    assert data['evidence']['projects.0.technologies.0']['source']==second_source
    saved=client.post(f'{BASE}/{doc}/verify',json=verify_payload(data,fields=['name','projects','skills']))
    assert saved.status_code==200,saved.text
    reviewed=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert reviewed['evidence']['projects.0.technologies.0']['verified'] is True
    assert reviewed['structured']['projects'][0]['name']['review_status']=='confirmed'


def test_rich_skill_input_does_not_trust_client_origin_or_verification(client):
    response=client.post('/api/v1/profile',json={'name':'Nisha Example','skills':[{'name':'Python','source':'resume','verified':True},{'name':'React','source':'manual','verified':True},'python']})
    assert response.status_code==201,response.text
    profile=response.json()
    assert profile['skills']==['Python','React']
    assert all(s['source']=='manual' and not s['verified'] for s in profile['skill_details'])
    profile=client.post('/api/v1/profile/verify',json={'expected_revision':profile['revision'],'confirmed':True}).json()
    assert all(s['verified'] for s in profile['skill_details'])


def test_manual_skill_addition_preserves_resume_provenance(client):
    doc=upload(client);finish(client,doc)
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    profile=client.post(f'{BASE}/{doc}/verify',json=verify_payload(data)).json()
    payload={k:profile[k] for k in data['values']}
    payload['skills']=[{'name':s,'source':'manual','verified':False} for s in profile['skills']+['React']]
    saved=client.put('/api/v1/profile',json={**payload,'expected_revision':profile['revision']})
    assert saved.status_code==200,saved.text
    by_name={s['name']:s for s in saved.json()['skill_details']}
    assert by_name['Python']['source']=='resume' and by_name['Python']['verified']
    assert by_name['React']['source']=='manual' and not by_name['React']['verified']


def test_reading_legacy_verified_profile_does_not_rewrite_saved_data(client,application):
    client.post('/api/v1/profile',json={'name':'Existing Student'})
    legacy={'name':'Existing Student','skills':['Python'],'projects':[{'name':'Saved project','description':'Existing description','url':''}]}
    with application.state.session_factory() as db:
        profile=db.scalar(select(CandidateProfile)); profile.facts=legacy;profile.verified=True;db.commit();revision=profile.revision
    response=client.get('/api/v1/profile')
    assert response.status_code==200,response.text
    assert response.json()['verification_state']=='verified'
    with application.state.session_factory() as db:
        profile=db.scalar(select(CandidateProfile))
        assert profile.facts==legacy and profile.revision==revision and profile.verified


@pytest.mark.parametrize('content,status,code', [('{}',200,'groq_invalid_output'), ('',413,'groq_input_limit')])
def test_incomplete_provider_json_and_token_limits_fall_back(application,content,status,code):
    from pydantic import SecretStr
    from app.services.groq_extraction import EnhancementUnavailable
    application.state.settings.groq_api_key=SecretStr('synthetic-key')
    def handler(request):
        return httpx.Response(status,json={'choices':[{'message':{'content':content}}]})
    factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw)
    with pytest.raises(EnhancementUnavailable) as exc:
        enhance([],application.state.settings,client_factory=factory)
    assert exc.value.code==code


def test_explicit_absence_and_dot_net_skill_separators():
    values,proof,_=deterministic_extract(blocks('Nisha Example\nProjects:\nNone\nSkills:\n.NET/Python; python\nExperience:\nNone'))
    assert values['projects']==values['experience']==[]
    assert values['skills']==['.NET','Python']


def test_user_added_review_skill_remains_manual_after_source_deletion(client):
    doc=upload(client);finish(client,doc)
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    data['values']['skills'].append('Manual Tool')
    data=client.put(f'{BASE}/{doc}/extracted-data',json={'processing_id':data['id'],'expected_revision':data['revision'],'values':data['values']}).json()
    response=client.post(f'{BASE}/{doc}/verify',json=verify_payload(data))
    assert response.status_code==200,response.text
    assert client.get('/api/v1/dashboard/summary').json()['extracted_skill_count']==3
    assert client.delete(f'{BASE}/{doc}').status_code==204
    skill=next(s for s in client.get('/api/v1/profile').json()['skill_details'] if s['name']=='Manual Tool')
    assert skill['source']=='manual' and skill['verified'] and skill['evidence'] is None


def test_identical_job_titles_cannot_mix_employers_or_dates():
    text=blocks('Nisha Example\nExperience:\nBackend Intern | Alpha Labs | 2024-2025\n- Used Python.\nBackend Intern | Beta Labs | 2025-2026\n- Used React.')
    proposal=AIProposal.model_validate({'values':{'experience':[{'role':'Backend Intern','organization':'Alpha Labs','period':'2025-2026'}]},'claims':[
        {'path':'experience.0.role','block_id':3,'quote':'Backend Intern | Alpha Labs | 2024-2025','confidence':'high'},
        {'path':'experience.0.organization','block_id':3,'quote':'Backend Intern | Alpha Labs | 2024-2025','confidence':'high'},
        {'path':'experience.0.period','block_id':5,'quote':'Backend Intern | Beta Labs | 2025-2026','confidence':'high'},
    ]})
    values,_,rejected=anchored_ai_values(proposal,text)
    assert values['experience'][0]['organization']=='Alpha Labs'
    assert values['experience'][0]['period']=='' and rejected==1
