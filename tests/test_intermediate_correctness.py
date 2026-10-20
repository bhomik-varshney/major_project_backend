"""Independent content oracles for native PDF, OCR decisions and source support."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject,ArrayObject,FloatObject,TextStringObject

from app.services.document_intelligence import prepare_document
from app.services.resume_structure import deterministic_extract,segment
from app.services.resume_quality import source_check
from app.services.intelligence_audit import validate_layout
from app.schemas.intelligence import VisionLayout
from test_multimodal_intelligence import scanned_pdf


def dense_native_pdf():
    pages=[[
        'Mira Example','mira@example.com | +91 90000 00000',
        'Computer engineering undergraduate specializing in application development and data systems.',
        'Projects','Native Portal — Campus Directory','Python, FastAPI, React GitHub',
        '- Built a campus directory using Python.','Hosted records and search on the campus server.',
        '', 'Voice Response Evaluation — Technical Assessment with Multiple Independently Written Title Words',
        'PyTorch, PEFT, FastAPI','- Built a model evaluation service.','Measured scores, speed and failure cases.',
        '', 'Health Assistant — Team Project','FastAPI, React/TypeScript','- Built reminders and reports.',
        '', 'Traffic Grid — Simulation Project','React Three Fiber, Three.js','- Built a traffic visualization.',
        'Experience','Example Labs — Remote','Backend Developer Intern | May 2025 – Jul 2025',
        '- Built validated API endpoints.','Training Institute — Remote','Machine Learning Trainee | Dec 2024 – Jan 2025',
        '- Compared classifiers on sample data.'],[
        'Education','Example University','B.Tech, Computer Engineering, CGPI: 9.2 | Expected Graduation 2027',
        'Example Senior Secondary School','Class XII, State Board | 95% | 2022-2023',
        'Example Public School','Class X | 98% | 2020-2021',
        'Technical Skills','Languages: Python, JavaScript, TypeScript, C/C++',
        'AI/ML: PyTorch, PEFT, QLoRA','Frameworks & Frontend: FastAPI, React, React Native, React Three Fiber',
        'Tools: Docker, Git','Achievements','- Won the campus hackathon.','- Finished the evaluation challenge.'
    ]]
    writer=PdfWriter()
    for page_index,lines in enumerate(pages):
        page=writer.add_blank_page(width=612,height=792)
        fonts=DictionaryObject()
        for name,font in [('F1','Helvetica'),('F2','Helvetica-Bold')]:
            fonts[NameObject('/'+name)]=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/'+font)})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):fonts})
        commands=['BT /F1 9 Tf 35 755 Td']
        for line in lines:
            safe=line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)').replace('—','-').replace('–','-')
            # Font changes deliberately fragment the content stream inside a title.
            if safe.startswith('Native Portal'):
                commands.append('(Nat) Tj /F2 9 Tf (ive Portal'+safe[len('Native Portal'):]+') Tj /F1 9 Tf')
            else:commands.append('('+safe+') Tj')
            commands.append('0 -16 Td')
        commands.append('ET');stream=DecodedStreamObject();stream.set_data('\n'.join(commands).encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
        if page_index==0:
            page[NameObject('/Annots')]=ArrayObject([DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Link'),
                NameObject('/Rect'):ArrayObject([FloatObject(v) for v in (35,670,250,682)]),
                NameObject('/A'):DictionaryObject({NameObject('/S'):NameObject('/URI'),NameObject('/URI'):TextStringObject('https://example.com/native-portal')})})])
    output=BytesIO();writer.write(output);return output.getvalue()


def prepare(content,name,settings):
    settings.document_storage_path.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=settings.document_storage_path) as scratch:
        return prepare_document(content,name,str(uuid4()),settings,scratch)


def test_native_intermediate_values_match_independent_document_oracle(application):
    result=prepare(dense_native_pdf(),'dense-native.pdf',application.state.settings)
    values,evidence,warnings=deterministic_extract(result['blocks'])
    assert len(values['projects'])==4
    assert [p['name'].split(' - ')[0] for p in values['projects']]==['Native Portal','Voice Response Evaluation','Health Assistant','Traffic Grid']
    assert values['projects'][0]['technologies']==['Python','FastAPI','React']
    assert values['projects'][3]['technologies']==['React Three Fiber','Three.js']
    assert 'Measured scores, speed and failure cases.' in values['projects'][1]['description']
    assert values['projects'][0]['url']=='https://example.com/native-portal'
    assert [e['role'] for e in values['experience']]==['Backend Developer Intern','Machine Learning Trainee']
    assert [e['organization'] for e in values['experience']]==['Example Labs','Training Institute']
    assert [e['period'] for e in values['experience']]==['May 2025 - Jul 2025','Dec 2024 - Jan 2025']
    assert [e['qualification'] for e in values['education']]==['B.Tech, Computer Engineering','Class XII, State Board','Class X']
    assert [e['institution'] for e in values['education']]==['Example University','Example Senior Secondary School','Example Public School']
    assert values['education'][0]['expected_graduation']=='Expected Graduation 2027'
    assert {'C/C++','QLoRA','Docker','Git','React Native'}<=set(values['skills'])
    assert len(values['achievements'])==2
    assert values['career_objective']=='Computer engineering undergraduate specializing in application development and data systems.'
    assert source_check(values,evidence,result['blocks'])['unsupported_fields']==0
    assert all(page['ocr_status']=='not_required' for page in result['quality']['pages'])
    assert not result['ocr'] and len(result['pages'])==2
    assert not any(b.get('reading_order_uncertain') for b in result['blocks'])
    assert any(b.get('source_kind')=='link_destination' for b in result['blocks'])


def test_languages_category_and_control_bullets_do_not_create_entries():
    blocks=[{'block_id':i+1,'text':line} for i,line in enumerate(['Mira Example','Technical Skills','Languages:','Python, C/C++','Frameworks: React',
        'Projects','Campus Portal','\x88','\x88 Built a campus directory.'])]
    values,_,_=deterministic_extract(blocks)
    assert {'Python','C/C++','React'}<=set(values['skills'])
    assert [p['name'] for p in values['projects']]==['Campus Portal']
    assert '\x88' not in values['projects'][0]['description']


def test_real_scanned_page_has_explicit_completed_ocr_and_correct_content(application):
    result=prepare(scanned_pdf(),'scan.pdf',application.state.settings)
    assert result['quality']['pages'][0]['ocr_status']=='completed'
    assert result['quality']['pages'][0]['ocr_blocks']==len(result['ocr'])>0
    assert 'mira@example.com' in '\n'.join(b['text'] for b in result['ocr'])
    values,proof,_=deterministic_extract(result['blocks'])
    assert values['email']=='mira@example.com'
    assert [p['name'] for p in values['projects']]==['Campus Portal']
    assert source_check(values,proof,result['blocks'])['unsupported_fields']==0


def test_disabled_ocr_is_distinct_from_not_required(application):
    application.state.settings.ocr_enabled=False
    result=prepare(scanned_pdf(),'disabled.pdf',application.state.settings)
    assert result['quality']['pages'][0]['ocr_status']=='disabled'
    assert result['quality']['pages'][0]['ocr_recommended'] and not result['ocr']


def test_source_checker_does_not_accept_model_status_or_unrelated_quotes():
    blocks=[{'block_id':1,'text':'Python'}]
    result=source_check({'skills':['Rust']},{'skills.0':{'source':{'block_id':1,'quote':'Python'},'confidence':'high','verified':True}},blocks)
    assert result['supported_fields']==0 and result['problems']==[{'path':'skills.0','code':'source_value_mismatch'}]


def test_visual_text_match_with_wrong_geometry_still_requires_review():
    layout=VisionLayout.model_validate({'blocks':[{'id':'v1','page':1,'raw_text':'Python','region':{'x':.8,'y':.8,'width':.1,'height':.1}}]})
    blocks=[{'block_id':1,'text':'Python','page':1,'page_width':100,'page_height':100,'native_bbox':[5,85,30,95]}]
    result,warnings=validate_layout(layout,blocks,[{'page':1}])
    assert result[0]['source_block_ids']==[1] and result[0]['status']=='needs_review'
    assert warnings[0]['code']=='vision_geometry_unresolved'


def test_source_entry_anchor_survives_a_batch_that_starts_inside_its_entry():
    from app.services.crew_payload import compact_context
    blocks=[{'block_id':i+1,'text':line} for i,line in enumerate(['Projects','Campus Directory','Technologies: Python, FastAPI','- Built a directory.'])]
    context=compact_context(blocks[2:],source_blocks=blocks)
    assert context['entry_groups']==[{'field':'projects','anchor':'Campus Directory','anchor_block_id':2,'ids':[2,3,4]}]
    assert {b['block_id'] for b in context['blocks']}=={3,4}


def test_ai_cannot_shorten_a_written_technology_into_an_implied_parent():
    from app.schemas.agents import ResumeClaims
    from app.services.crew_payload import proposal_from_claims
    from app.services.resume_structure import anchored_ai_values
    blocks=[{'block_id':1,'text':'Projects'},{'block_id':2,'text':'Traffic Grid'},
        {'block_id':3,'text':'React Three Fiber, Three.js','native_line':True}]
    claims=ResumeClaims(claims=[{'path':'projects.0.name','value':'Traffic Grid','block_id':2,'confidence':'high'},
        {'path':'projects.0.technologies.0','value':'React','block_id':3,'confidence':'high'}])
    proposal,_=proposal_from_claims(claims,blocks);decisions=[]
    values,_,withheld=anchored_ai_values(proposal,blocks,decisions=decisions)
    assert values['projects'][0]['technologies']==[] and withheld==1
    assert decisions[-1]['reason']=='technology_name_fragment'
