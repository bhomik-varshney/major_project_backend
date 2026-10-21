"""Synthetic-only multimodal evidence, actual CrewAI and private audit regressions."""
from io import BytesIO
import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import ResumePageArtifact, ResumeProcessingAudit
from app.orchestration.vision import _vision_child, VisionResult
from app.providers.groq import GroqKeyManager, GroqProvider, ProviderUnavailable
from app.schemas.intelligence import ProcessingRun, VisionLayout
from app.services.document_intelligence import prepare_document, prepare_isolated
from app.services.intelligence_audit import validate_layout
from test_document_processing import upload, finish, make_pdf, RESUME, verify_payload, BASE


def scanned_pdf():
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(1224,1584),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',30) if Path('/System/Library/Fonts/Supplemental/Arial.ttf').exists() else ImageFont.load_default(size=30)
    for i,line in enumerate(['Mira Example','Email: mira@example.com','Introduction','Engineering student building useful services.','Skills','Python, SQL','Projects','Campus Portal','Technologies: Python, FastAPI']):
        draw.text((80,80+i*70),line,font=font,fill='black')
    output=BytesIO();image.save(output,format='PDF',resolution=144);image.close();return output.getvalue()


def valid_docx():
    output=BytesIO()
    with ZipFile(output,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Mira Example</w:t></w:r></w:p><w:p><w:r><w:t>Introduction</w:t></w:r></w:p><w:p><w:r><w:t>Engineering student building services.</w:t></w:r></w:p><w:p><w:r><w:t>Education</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>B.Tech</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Example University</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>')
    return output.getvalue()


def prepare(content,name,settings):
    with TemporaryDirectory(dir=settings.document_storage_path) as d:
        return prepare_document(content,name,str(uuid4()),settings,d)


@pytest.mark.parametrize('file',['two_column.pdf','table_education.docx','objective_only.txt','multiple_projects.txt'])
def test_exact_evidence_and_quality_for_varied_formats(application,file):
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    content=(Path(__file__).parent/'fixtures/resumes'/file).read_bytes()
    result=prepare(content,file,settings)
    assert result['exact_text'] and result['blocks']
    assert [b['order'] for b in result['exact_text']]==list(range(len(result['exact_text'])))
    assert len({b['block_id'] for b in result['blocks']})==len(result['blocks'])
    if file=='two_column.pdf':
        assert result['quality']['columns']=='multiple_suspected'
        assert result['pages'] and result['layout']
    if file=='table_education.docx': assert result['quality']['tables_detected']


def test_real_pdf_rendering_preserves_proportions_and_only_needed_ocr(application):
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    with patch('app.services.document_intelligence.read_ocr',side_effect=AssertionError('Readable page must not invoke OCR')):
        result=prepare(make_pdf(RESUME),'r.pdf',settings)
    page=result['pages'][0]
    assert page['content'].startswith(b'\x89PNG')
    assert abs(page['width']/page['height']-612/792)<.002
    assert max(page['width'],page['height'])<=settings.render_max_dimension
    assert all(b['method']=='text' for b in result['exact_text'])


def test_real_scanned_pdf_ocr_and_no_fabricated_selectable_text(application):
    import shutil
    if not shutil.which('tesseract'): pytest.skip('Optional Tesseract unavailable')
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    result=prepare(scanned_pdf(),'scan.pdf',settings)
    assert result['quality']['classification']=='image_only'
    assert not result['exact_text'] and result['ocr']
    assert 'mira@example.com' in '\n'.join(b['text'] for b in result['ocr'])
    assert all(0<=b['confidence']<=1 and b['region'] for b in result['ocr'])
    assert all(b['extraction_method']=='ocr' for b in result['blocks'])


def test_docx_page_conversion_or_explicit_optional_dependency_warning(application):
    from app.services.document_intelligence import converter
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    result=prepare(valid_docx(),'resume.docx',settings)
    assert result['quality']['tables_detected']
    if converter(settings):
        assert result['pages'] and result['pages'][0]['renderer']=='libreoffice+pdfium'
        assert result['page_count']>=1
        from app.schemas.intelligence import ConversionAudit
        conversion=ConversionAudit.model_validate(result['conversion'])
        assert conversion.status=='completed'
        assert conversion.attempts[-1].exit_code==0
        assert conversion.attempts[-1].generated_file_exists
    else:
        assert any('LibreOffice' in w for w in result['warnings'])
    assert any(b.get('paragraph') for b in result['exact_text'])


def test_ocr_unavailable_and_partial_rendering_are_explicit(application):
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    with patch('app.services.document_intelligence.shutil.which',return_value=None):
        result=prepare(scanned_pdf(),'scan.pdf',settings)
    assert not result['ocr'] and any('OCR is unavailable' in w for w in result['warnings'])
    from pypdf import PdfWriter
    writer=PdfWriter();writer.add_blank_page(200,300);writer.add_blank_page(200,300);out=BytesIO();writer.write(out)
    settings.render_max_pages=1;settings.ocr_enabled=False
    result=prepare(out.getvalue(),'two.pdf',settings)
    assert result['page_count']==2 and len(result['pages'])==1
    assert any('first 1 pages' in w for w in result['warnings'])


def test_private_audit_images_consent_and_cascade(client,application):
    application.state.settings.max_document_bytes=100000
    doc=upload(client,make_pdf(RESUME),'private.pdf','application/pdf')
    assert client.post(f'{BASE}/{doc}/process',json={'use_vision':True}).status_code==422
    assert finish(client,doc)['status']=='awaiting_review'
    response=client.get(f'{BASE}/{doc}/audit');assert response.status_code==200,response.text
    audit=ProcessingRun.model_validate(response.json())
    assert audit.pages and audit.draft and audit.exact_text and audit.layout
    assert audit.consent=={'text':False,'images':False}
    assert audit.review.action=='pending'
    image=f'{BASE}/{doc}/audit/pages/{audit.pages[0].id}'
    assert client.get(image).content.startswith(b'\x89PNG')
    assert client.get(image).headers['cache-control']=='no-store'
    with TestClient(application,headers={'X-Workspace-Client':'careeros-web'}) as outsider:
        outsider.post('/api/v1/session')
        assert outsider.get(image).status_code==404
        assert outsider.get(f'{BASE}/{doc}/audit').status_code==404
    draft=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert client.post(f'{BASE}/{doc}/verify',json=verify_payload(draft,fields=['name','skills'])).status_code==200
    assert client.get(f'{BASE}/{doc}/audit').json()['review']['action']=='confirmed'
    assert client.delete(f'{BASE}/{doc}').status_code==204
    with application.state.session_factory() as db:
        assert db.scalar(select(ResumePageArtifact)) is None
        assert db.scalar(select(ResumeProcessingAudit)) is None


def test_scanned_api_draft_is_ocr_labeled_and_profile_untouched(client,application):
    import shutil
    if not shutil.which('tesseract'): pytest.skip('Optional Tesseract unavailable')
    application.state.settings.max_document_bytes=1000000
    doc=upload(client,scanned_pdf(),'scan.pdf','application/pdf')
    assert finish(client,doc)['status']=='awaiting_review'
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert data['evidence']['email']['extraction_methods']==['ocr']
    assert data['evidence']['email']['ocr_confidence'] is not None
    assert client.get('/api/v1/profile').status_code==404


def test_publications_introduction_and_unknown_sections_stay_separate(client):
    text=b'Mira Example\nIntroduction\nEngineering student building services.\nPublications\nA Study of Student Portals\nLanguages\nHindi, English\nSkills\nPython\n'
    doc=upload(client,text)
    finish(client,doc)
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert data['values']['publications']==['A Study of Student Portals']
    assert data['values']['career_objective']=='Engineering student building services.'
    audit=client.get(f'{BASE}/{doc}/audit').json()
    assert audit['draft']['other_sections'][0]['heading']=='Languages'
    assert audit['draft']['other_sections'][0]['text']=='Hindi, English'
    assert client.post(f'{BASE}/{doc}/verify',json=verify_payload(data,fields=['name','publications'])).status_code==200
    assert client.get('/api/v1/profile').json()['publications']==['A Study of Student Portals']


def test_conflicting_contact_readings_retained_and_current_edits_reflected(client):
    doc=upload(client,b'Mira Example\nEmail: mira@example.com\nEmail: alternative@example.com\nSkills\nPython\n')
    finish(client,doc)
    audit=client.get(f'{BASE}/{doc}/audit').json()
    proof=next(e for e in audit['draft']['evidence'] if e['field']=='email')
    assert proof['status']=='needs_review' and len(proof['alternatives'])==2
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    values={**data['values'],'email':'corrected@example.com'}
    edited=client.put(f'{BASE}/{doc}/extracted-data',json={'processing_id':data['id'],'expected_revision':data['revision'],'values':values})
    assert edited.status_code==200
    audit=client.get(f'{BASE}/{doc}/audit').json()
    proof=next(e for e in audit['draft']['evidence'] if e['field']=='email')
    assert proof['status']=='candidate_edited' and proof['source_block_ids']==[]
    assert len(proof['alternatives'])==2


def test_vision_layout_validation_retains_disagreement_and_rejects_fake_page():
    blocks=[{'block_id':1,'page':1,'text':'Python','position':{'x':30,'y':700,'font_size':12},'page_width':600,'page_height':800}]
    layout=VisionLayout.model_validate({'blocks':[{'id':'v1','page':1,'raw_text':'Python','source_block_ids':[999]},
        {'id':'v2','page':1,'raw_text':'Rust','region':{'x':.05,'y':.11,'width':.1,'height':.04}},
        {'id':'v3','page':99,'raw_text':'Imaginary'}]})
    result,warnings=validate_layout(layout,blocks,[{'page':1}])
    assert result[0]['source_block_ids']==[1] and result[0]['status']=='supported'
    assert result[1]['status']=='needs_review' and not result[1]['source_block_ids']
    assert warnings[0]['sources'][0]['value']=='Rust'
    assert any(s['value']=='Python' for s in warnings[0]['sources'])
    assert len(result)==2


def run_visual_task(monkeypatch,settings,transport):
    monkeypatch.setattr('app.orchestration.vision.GroqProvider',lambda *args:transport)
    output=Queue()
    with TemporaryDirectory() as scratch:
        _vision_child({'pages':[{'page':1,'width':100,'height':200,'image':'c3ludGhldGlj'}]},settings,GroqKeyManager(['synthetic']),42,time.monotonic()+20,scratch,output)
    messages=[]
    while not output.empty(): messages.append(output.get_nowait())
    return messages


def test_actual_vision_crewai_task_receives_images_and_falls_back_between_models(monkeypatch):
    settings=Settings(_env_file=None,groq_vision_model_primary='synthetic/primary',groq_vision_model_fallback='synthetic/fallback');calls=[]
    class Transport:
        def available_models(self,**kwargs): return {settings.groq_vision_model_primary,settings.groq_vision_model_fallback}
        def complete(self,messages,schema,**kwargs):
            calls.append((messages,kwargs))
            if len(calls)==1: raise ProviderUnavailable('groq_model_unavailable','Primary unavailable')
            return VisionLayout(blocks=[]).model_dump_json()
    result=run_visual_task(monkeypatch,settings,Transport())
    assert result[-1][0]=='result',result
    visual=VisionResult.model_validate(result[-1][1])
    assert visual.model==settings.groq_vision_model_fallback and len(calls)==2
    assert any('Document Layout Analyst' in str(m) for m in calls[0][0])
    assert any(isinstance(m['content'],list) and any(c['type']=='image_url' for c in m['content']) for m in calls[0][0])
    assert all(call[1]['json_mode'] for call in calls)


@pytest.mark.parametrize('outcome',['missing','invalid','malformed_request'])
def test_actual_vision_task_failure_is_bounded_and_safe(monkeypatch,outcome):
    settings=Settings(_env_file=None,groq_vision_model_primary='synthetic/primary',groq_vision_model_fallback='synthetic/fallback');calls=[]
    class Transport:
        def available_models(self,**kwargs): return set() if outcome=='missing' else {settings.groq_vision_model_primary,settings.groq_vision_model_fallback}
        def complete(self,*args,**kwargs):
            calls.append(1)
            if outcome=='malformed_request': raise ProviderUnavailable('groq_request_rejected','Safe request rejection')
            return 'invalid json'
    result=run_visual_task(monkeypatch,settings,Transport())
    assert result[-1][0]=='error'
    assert len(calls)==(0 if outcome=='missing' else 1 if outcome=='malformed_request' else 2)
    assert 'invalid json' not in str(result)


def test_model_availability_and_text_model_configuration():
    settings=Settings(_env_file=None,groq_text_model='configured/text')
    seen=[]
    def handler(request):
        seen.append(request)
        if request.method=='GET': return httpx.Response(200,json={'data':[{'id':'configured/text'}]})
        assert json.loads(request.content)['model']=='configured/text'
        return httpx.Response(404,json={'error':{'code':'model_not_found'}})
    provider=GroqProvider(GroqKeyManager(['synthetic']),settings,client_factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw))
    assert provider.available_models()=={'configured/text'}
    with pytest.raises(ProviderUnavailable) as exc: provider.complete([],{})
    assert exc.value.code=='groq_model_unavailable' and len(seen)==2


def test_mixed_image_text_page_uses_ocr_even_with_plenty_of_selectable_text(application):
    from pypdf import PdfReader,PdfWriter
    settings=application.state.settings;settings.document_storage_path.mkdir(parents=True)
    image=PdfReader(BytesIO(scanned_pdf())).pages[0]
    native=PdfReader(BytesIO(make_pdf(RESUME))).pages[0]
    image.merge_page(native)
    writer=PdfWriter();writer.add_page(image);out=BytesIO();writer.write(out)
    result=prepare(out.getvalue(),'mixed.pdf',settings)
    assert result['quality']['classification']=='mixed'
    assert result['quality']['pages'][0]['image_coverage']>.2
    assert result['quality']['pages'][0]['ocr_recommended']
    assert result['exact_text'] and result['ocr']


def test_malformed_audit_is_controlled_and_legacy_run_does_not_invent_pages(client,application):
    doc=upload(client);finish(client,doc)
    with application.state.session_factory() as db:
        audit=db.scalar(select(ResumeProcessingAudit));audit.data={'exact_text':'invalid'};db.commit()
    response=client.get(f'{BASE}/{doc}/audit')
    assert response.status_code==409 and response.json()['error']['code']=='audit_invalid'
    with application.state.session_factory() as db:
        db.delete(db.scalar(select(ResumeProcessingAudit)));db.commit()
    audit=client.get(f'{BASE}/{doc}/audit').json()
    assert audit['exact_text'] and audit['pages']==[]
    assert audit['warnings'][0]['code']=='legacy_run'


def test_page_artifacts_retained_for_only_two_recent_runs(client,application):
    application.state.settings.max_document_bytes=100000
    doc=upload(client,make_pdf(RESUME),'retention.pdf','application/pdf')
    for index in range(3):
        assert finish(client,doc,restart=index>0)['status']=='awaiting_review'
    with application.state.session_factory() as db:
        assert len(list(db.scalars(select(ResumePageArtifact))))==2
        assert len(list(db.scalars(select(ResumeProcessingAudit))))==3


def test_local_preparation_cancellation_cleans_worker_and_scratch(application):
    import multiprocessing
    from app.services.document_processing import ProcessingStopped
    settings=application.state.settings
    before={p.pid for p in multiprocessing.active_children()}
    def cancel(stage):
        if stage=='extracting_text':raise ProcessingStopped()
    with pytest.raises(ProcessingStopped):
        prepare_isolated(make_pdf(RESUME),'r.pdf',str(uuid4()),settings,cancel)
    assert {p.pid for p in multiprocessing.active_children()}==before
    assert list(settings.document_storage_path.iterdir())==[]


def test_successful_visual_task_feeds_text_crew_and_source_evidence(client,application):
    application.state.settings.max_document_bytes=100000
    doc=upload(client,make_pdf(RESUME),'vision.pdf','application/pdf')
    visual=VisionResult(layout=VisionLayout.model_validate({'blocks':[{'id':'contact','page':1,'raw_text':'aarav@example.com','block_type':'contact','confidence':.8}]}),model='configured/vision',attempted_models=['configured/vision'],warnings=[])
    with patch('app.services.document_processing.analyse_pages',return_value=visual) as vision,patch('app.services.document_processing.resume_intelligence.enhance',return_value=({}, {}, 0)) as text:
        result=finish(client,doc,use_groq=True,allow_external_processing=True,use_vision=True,allow_image_processing=True)
    assert result['status']=='awaiting_review' and vision.call_count==1
    assert any(b['method']=='vision' for b in text.call_args.kwargs['layout'])
    data=client.get(f'{BASE}/{doc}/extracted-data').json()
    assert data['evidence']['email']['extraction_methods']==['text','vision']
    audit=client.get(f'{BASE}/{doc}/audit').json()
    assert audit['agents'][0]['model']=='configured/vision'
    assert client.get('/api/v1/profile').status_code==404


def test_partially_anchored_visual_text_cannot_be_called_supported():
    layout=VisionLayout.model_validate({'blocks':[{'id':'v','page':1,'raw_text':'SQL and imaginary Rust experience'}]})
    result,warnings=validate_layout(layout,[{'block_id':1,'page':1,'text':'SQL'}],[{'page':1}])
    assert result[0]['status']=='needs_review' and result[0]['source_block_ids']==[]


def partial_holding_child(content,filename,document_id,settings,scratch,output):
    import os
    if os.name=='posix':os.setsid()
    Path(scratch,'temporary-source.txt').write_text('Synthetic evidence only')
    output.put(('partial',{'blocks':[{'block_id':1,'text':'Synthetic evidence'}],'exact_text':[],
        'page_count':2,'warnings':[],'pages':[{'page':1}],'ocr':[],'layout':[],'quality':{'classification':'incomplete'}}))
    time.sleep(60)


def test_local_timeout_preserves_partial_evidence_and_cleans_process(application):
    import multiprocessing
    settings=application.state.settings;settings.preparation_timeout_seconds=1
    before={p.pid for p in multiprocessing.active_children()}
    result=prepare_isolated(b'synthetic','r.txt',str(uuid4()),settings,child_target=partial_holding_child)
    assert result['blocks'][0]['text']=='Synthetic evidence' and result['pages']==[{'page':1}]
    assert any('timed out' in w for w in result['warnings'])
    assert list(settings.document_storage_path.iterdir())==[]
    assert {p.pid for p in multiprocessing.active_children()}==before


def test_truncated_vision_output_retries_one_changed_shorter_layout_request(monkeypatch):
    settings=Settings(_env_file=None,groq_vision_model_primary='synthetic/vision');calls=[]
    class Transport:
        def available_models(self,**kwargs):return {'synthetic/vision'}
        def complete(self,messages,schema,**kwargs):
            calls.append((messages,kwargs))
            if len(calls)==1:raise ProviderUnavailable('groq_output_truncated','Layout output limit reached.')
            return VisionLayout(blocks=[]).model_dump_json()
    result=run_visual_task(monkeypatch,settings,Transport())
    assert result[-1][0]=='result' and len(calls)==2
    assert calls[0][1]['max_output_tokens']==3000 and calls[1][1]['max_output_tokens']==3500
    assert calls[0][0]!=calls[1][0] and 'at most 6 structural blocks' in str(calls[1][0])
