"""Independent canonical invariants and end-to-end owned draft/version boundaries."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import time
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from app.core.config import Settings
from app.flows.resume_canonical import ResumeCanonicalFlow
from app.providers.groq import GroqKeyManager,ProviderUnavailable
from app.schemas.agents import ResumeClaims,ResumeClaim
from app.schemas.canonical import CanonicalResume
from app.services.resume_structure import deterministic_extract
from app.services.canonical_resume import build_canonical,validate_canonical,project_draft
from app.services.intelligence_audit import source_conflicts
from test_document_processing import upload,finish,verify_payload,BASE
from test_resume_crew import crew_runtime


def blocks_of(text):return [{'block_id':i+1,'text':line,'page':1} for i,line in enumerate(text.splitlines())]


def run_graph(blocks,settings=None,values=None,evidence=None,repair=None,use_ai=False,warnings=None):
    parsed,proof,_=deterministic_extract(blocks)
    return ResumeCanonicalFlow({'id':str(uuid4()),'file_type':'txt','values':parsed if values is None else values,'evidence':proof if evidence is None else evidence,
        'blocks':blocks,'exact_text':blocks,'quality':{},'layout':[],'warnings':warnings or []},settings or Settings(_env_file=None),GroqKeyManager(['fixture-only'] if use_ai else (),0),use_ai=use_ai,repair=repair).kickoff()


def test_connected_graph_correct_grouping_and_complete_evidence():
    blocks=blocks_of('Mira Example\nmira@example.com\nProjects\nDirectory Tool\nTechnologies: Python, SQL\n- Built a campus directory.\n\nDesign Tool\nTechnologies: React, CSS\n- Designed accessible forms.')
    result=run_graph(blocks);graph=CanonicalResume.model_validate(result['canonical'])
    projects=[e for e in graph.entities if e.bucket=='projects']
    assert len(projects)==2 and result['values']['projects'][0]['technologies']==['Python','SQL'] and result['values']['projects'][1]['technologies']==['React','CSS']
    raw={r['evidence_id']:r for r in result['raw_evidence']}
    for entity in graph.entities:
        for f in entity.fields.values():
            assert f.evidence_ids and all(id in raw for id in f.evidence_ids)
            assert f.related_entities==[entity.entity_id]
    assert result['canonical_validation']['unsupported_fields']==0
    assert result['canonical_workflow']['engine']=='crewai.Flow' and len(result['canonical_workflow']['tasks'])==7


@pytest.mark.parametrize('heading,bucket',[('Core Competencies','skills'),('Expertise','skills'),('Academic Background','education'),('Employment','experience'),('Internship','internships'),('Research','research'),('Volunteer Experience','volunteer'),('Leadership','leadership'),('Extracurricular Activities','extracurricular'),('Languages','languages'),('Interests','interests'),('References','references')])
def test_heading_buckets_do_not_bleed(heading,bucket):
    body='Python, SQL' if bucket=='skills' else 'Campus team participant'
    result=run_graph(blocks_of('Mira Example\n'+heading+'\n'+body))
    assert bucket in {s['bucket'] for s in result['canonical']['sections']}
    if bucket in {'research','volunteer','leadership','extracurricular','languages','interests','references'}:
        assert result['values']['additional_sections']==[{'heading':heading,'content':body}]
        assert not result['values']['projects'] and not result['values']['experience']


def test_unknown_styled_heading_stays_unclassified_and_preserves_original():
    blocks=blocks_of('Mira Example\nThings That Matter\nOrganised a campus event')
    blocks[1]['style']='Heading1'
    result=run_graph(blocks)
    assert any(s['bucket']=='unclassified' for s in result['canonical']['sections'])
    assert result['values']['additional_sections'][0]['content']=='Organised a campus event'


def test_unsupported_claim_withheld_but_preserved_in_initial_graph():
    blocks=blocks_of('Mira Example\nSkills\nPython')
    values,evidence,_=deterministic_extract(blocks);values['skills']=['Rust']
    result=run_graph(blocks,values=values,evidence=evidence)
    assert result['values']['skills']==[]
    fact=next(f for e in result['canonical']['entities'] for f in e['fields'].values() if f['profile_path']=='skills.0')
    assert fact['status']=='unsupported' and fact['value'] is None and fact['original_text']=='Python'
    assert result['repair_iterations'][-1]['outcome']=='skipped'


def test_conflicts_preserve_both_email_sources_and_do_not_claim_resolution():
    blocks=blocks_of('Mira Example\nEmail: mira@example.com\nEmail: other@example.com')
    result=run_graph(blocks,warnings=source_conflicts(blocks))
    conflict=result['canonical']['conflicts'][0]
    assert {a['value'] for a in conflict['alternatives']}=={'mira@example.com','other@example.com'}
    assert conflict['resolution']=='unresolved' and conflict['selected_value'] is None
    email=next(e for e in result['canonical']['entities'] if e['entity_id']=='identity')['fields']['email']
    assert email['status']=='conflict'


def test_cross_project_technology_source_group_is_rejected():
    blocks=blocks_of('Mira Example\nProjects\nTool One\nTechnologies: Python\n\nTool Two\nTechnologies: React')
    values,proof,_=deterministic_extract(blocks)
    values['projects'][0]['technologies']=['React'];proof['projects.0.technologies.0']=deepcopy(proof['projects.1.technologies.0'])
    result=run_graph(blocks,values=values,evidence=proof)
    assert result['values']['projects'][0]['technologies']==[]
    assert result['values']['projects'][1]['technologies']==['React']


def test_native_spelling_reconciliation_does_not_use_visual_guess():
    blocks=blocks_of('Mira Example\nSkills\nPyTorch')
    values,proof,_=deterministic_extract(blocks);values['skills']=['pytorch']
    # Deliberate model casing error with a real source anchor.
    proof['skills.0']={'source':{'block_id':3,'page':1,'quote':'PyTorch'},'confidence':'high','method':'groq'}
    result=run_graph(blocks,values=values,evidence=proof)
    assert result['values']['skills']==['PyTorch'] and result['repair_iterations'][0]['changes'][0]['before']=='pytorch'


def test_targeted_patch_cannot_change_unaffected_fields_and_revalidates():
    blocks=blocks_of('Mira Example\nSkills\nPython')
    values,proof,_=deterministic_extract(blocks);values['skills']=['Rust']
    seen=[]
    def repair(payload,*args):
        seen.append(payload)
        return ResumeClaims(claims=[ResumeClaim(path='skills.0',value='Python',block_id=3,confidence='high'),ResumeClaim(path='name',value='Python',block_id=3,confidence='high')])
    result=run_graph(blocks,values=values,evidence=proof,repair=repair,use_ai=True)
    assert result['values']['skills']==['Python'] and result['values']['name']=='Mira Example'
    assert seen[0].allowed_paths==['skills.0'] and result['repair_iterations'][-1]['outcome']=='validated'
    assert result['canonical_validation']['unsupported_fields']==0


def test_no_progress_and_all_keys_fail_end_bounded_loop():
    blocks=blocks_of('Mira Example\nSkills\nPython');values,proof,_=deterministic_extract(blocks);values['skills']=['Rust']
    for outcome in ('no_progress','unavailable'):
        calls=[]
        def repair(*args):
            calls.append(1)
            if outcome=='unavailable':raise ProviderUnavailable('groq_keys_unavailable','Provider unavailable')
            return ResumeClaims(claims=[])
        result=run_graph(blocks,values=values,evidence=proof,repair=repair,use_ai=True)
        assert len(calls)==1 and result['repair_iterations'][-1]['outcome']==outcome
        assert result['values']['skills']==[]
    with pytest.raises(ValueError):Settings(_env_file=None,resume_max_repair_cycles=4)


def test_graph_schema_rejects_dangling_edges():
    result=run_graph(blocks_of('Mira Example'))
    graph=result['canonical'];graph['relations'].append({'source_id':'unknown','target_id':'identity','relation':'contains','evidence_ids':[]})
    with pytest.raises(ValidationError):CanonicalResume.model_validate(graph)


def test_genuine_crewai_targeted_repair_task_uses_real_executor(crew_runtime):
    from app.crews.canonical_repair import build_repair_crew
    llm=crew_runtime(ResumeClaims(claims=[ResumeClaim(path='skills.0',value='Python',block_id=1,confidence='high')],warnings=[]).model_dump_json())
    crew=build_repair_crew(llm);crew.kickoff(inputs={'repair_input':'Synthetic Python source; allowed path skills.0'})
    assert crew.tasks[0].output.pydantic.claims[0].value=='Python'
    assert len(llm._prompts)==1 and all(not a.tools and not a.allow_delegation for a in crew.agents)


def test_owned_history_original_canonical_edit_confirm_and_reprocess(client):
    text=b'Mira Example\nEmail: mira@example.com\nSkills\nPython\nLeadership\nStudent coordinator'
    doc=upload(client,text);first=finish(client,doc)
    graph=client.get(f'{BASE}/{doc}/canonical').json()
    assert graph['run_id']==first['id']
    assert client.get(f'{BASE}/{doc}/original').content==text
    assert client.get(f'{BASE}/{doc}/evidence').json()[0]['evidence_id']=='ev_00001'
    assert client.get('/api/v1/profile').status_code==404
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert data['values']['additional_sections'][0]['heading']=='Leadership'
    payload=verify_payload(data);payload['fields']=['name','skills','additional_sections']
    assert client.post(f'{BASE}/{doc}/verify',json=payload).status_code==200
    assert client.get('/api/v1/profile').json()['additional_sections'][0]['content']=='Student coordinator'
    second=finish(client,doc,restart=True)
    history=client.get(f'{BASE}/{doc}/runs').json();assert len(history)==2
    assert client.get(f'{BASE}/{doc}/audit',params={'run_id':first['id']}).json()['id']==first['id']
    diff=client.get(f'{BASE}/{doc}/compare',params={'left_run_id':first['id'],'right_run_id':second['id']}).json()
    assert diff['differences']==[] and not diff['profile_updated']
    assert client.get(f'{BASE}/{doc}/canonical',params={'run_id':'unowned'}).status_code==404


def test_explicit_outcomes_repository_and_deployment_do_not_create_false_projects():
    fixture=(Path(__file__).parent/'fixtures/resumes/connected_extras.txt').read_text()
    result=run_graph(blocks_of(fixture));projects=result['values']['projects']
    assert len(projects)==1 and projects[0]['name']=='Campus Directory'
    assert projects[0]['tools']==['Git','Docker']
    assert projects[0]['outcomes']==['Reduced repeated data entry in the project demonstration.']
    assert projects[0]['repository']=='https://github.com/example/campus-directory'
    assert projects[0]['deployment_url']=='https://example.com/campus'
    issuer=next(e for e in result['canonical']['entities'] if e['bucket']=='certifications')['fields']['issuer']
    assert issuer['value']=='Example Academy' and issuer['profile_path'] is None


def test_maximum_three_cycles_and_zero_cycles_are_enforced():
    blocks=blocks_of('Mira Example\nSkills\nPython, SQL, Git, Docker')
    values,proof,_=deterministic_extract(blocks);correct=values['skills'][:];values['skills']=['Wrong'+str(i) for i in range(4)]
    calls=[]
    def repair(payload,*args):
        path=payload.allowed_paths[0];calls.append(path)
        return ResumeClaims(claims=[ResumeClaim(path=path,value=correct[int(path.rsplit('.',1)[1])],block_id=3,confidence='high')])
    result=run_graph(blocks,values=values,evidence=proof,repair=repair,use_ai=True)
    assert len(calls)==3 and len(result['repair_iterations'])==4
    assert len(result['values']['skills'])==3 and result['canonical_validation']['unsupported_fields']==1
    calls.clear()
    zero=run_graph(blocks,values=values,evidence=proof,repair=repair,use_ai=True,settings=Settings(_env_file=None,resume_max_repair_cycles=0))
    assert not calls and len(zero['repair_iterations'])==1


def test_every_entity_and_field_is_connected_to_resume_root():
    result=run_graph(blocks_of('Mira Example\nSkills\nPython\nProjects\nCampus Tool\nTechnologies: Python'))
    graph=result['canonical'];reachable={'resume'}
    for _ in graph['relations']:
        reachable.update(r['target_id'] for r in graph['relations'] if r['source_id'] in reachable)
    assert all(e['entity_id'] in reachable for e in graph['entities'])
    assert all(f['fact_id'] in reachable for e in graph['entities'] for f in e['fields'].values())


def test_supported_visual_heading_can_suggest_bucket_without_changing_text_or_hiding_uncertainty():
    from app.services.canonical_resume import layout_context
    blocks=blocks_of('Mira Example\nThings I Built\nCampus Tool\nTechnologies: Python')
    visual=[{'id':'vision-heading','method':'vision','status':'supported','block_type':'heading','confidence':.95,'section':'Projects','raw_text':'Things I Built','source_block_ids':[2],'region':{'x':.1,'y':.2,'width':.5,'height':.03}}]
    contextual=layout_context(blocks,visual)
    assert [b['text'] for b in contextual]==[b['text'] for b in blocks]
    result=run_graph(contextual)
    assert len(result['values']['projects'])==1 and result['values']['projects'][0]['name']=='Campus Tool'
    project=next(e for e in result['canonical']['entities'] if e['bucket']=='projects')
    assert project['status']=='needs_review'
    assert any(s['detection_method']=='layout_supported' for s in result['canonical']['sections'])
    visual[0]['status']='needs_review'
    assert not any(b.get('section_hint') for b in layout_context(blocks,visual))


@pytest.mark.parametrize('field,value,source',[('links',['https://example.com'],'https://example.com/full-project'),('email','a@example.com','mira@example.com'),('skills',['React'],'React Three Fiber')])
def test_partial_contact_urls_and_parent_technology_fragments_are_not_source_facts(field,value,source):
    blocks=blocks_of('Mira Example\n'+('Skills\n' if field=='skills' else '')+source)
    values,proof,_=deterministic_extract(blocks);values[field]=value
    path=field+'.0' if isinstance(value,list) else field
    proof[path]={'source':{'block_id':len(blocks),'page':1,'quote':source},'confidence':'high','method':'groq'}
    result=run_graph(blocks,values=values,evidence=proof)
    assert not result['values'][field]
    rejected=next(f for e in result['canonical']['entities'] for f in e['fields'].values() if f['profile_path']==path)
    assert rejected['status']=='unsupported' and rejected['proposed_value']==(value[0] if isinstance(value,list) else value)


def test_cancel_during_canonicalization_propagates_without_a_draft():
    from app.services.document_processing import ProcessingStopped
    blocks=blocks_of('Mira Example\nSkills\nPython');values,proof,_=deterministic_extract(blocks)
    data={'id':str(uuid4()),'file_type':'txt','values':values,'evidence':proof,'blocks':blocks,'exact_text':blocks,'quality':{},'layout':[],'warnings':[]}
    def stop(stage):
        if stage=='canonicalizing':raise ProcessingStopped()
    with pytest.raises(ProcessingStopped):ResumeCanonicalFlow(data,Settings(_env_file=None),GroqKeyManager((),0),progress=stop).kickoff()


@pytest.mark.parametrize('technology',['React Native','React Three Fiber'])
def test_prose_technology_preserves_longest_written_name(technology):
    blocks=blocks_of(f'Mira Example\nExperience\nDeveloper Intern | Example Labs\n- Built {technology} components.')
    result=run_graph(blocks)
    assert result['values']['experience'][0]['technologies']==[technology]
    assert result['canonical_validation']['unsupported_fields']==0
