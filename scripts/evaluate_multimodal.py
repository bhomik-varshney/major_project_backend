"""Synthetic end-to-end evaluation with external calls deliberately unavailable."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.services.resume_structure import leaves,normalize

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tests/fixtures/resumes'


def get(value,path):
    try:
        for key in path.split('.'):
            value=value[int(key)] if isinstance(value,list) else value[key]
        return value
    except (KeyError,IndexError,TypeError):return None


def evaluate():
    cases=json.loads((FIXTURES/'cases.json').read_text())
    cases += [dict(file='image_resume.pdf',expected={'name':'Mira Example','email':'mira@example.com','career_objective':'Engineering student building useful services.'},skills=['Python','SQL','FastAPI'],project_names=['Campus Portal'],project_technologies=[['Python','FastAPI']]),
        dict(file='visual_table.docx',expected={'name':'Mira Example','career_objective':'Engineering student building services.'},skills=[],project_names=[],project_technologies=[]),
        dict(file='dense_native.pdf',expected={'name':'Mira Example','career_objective':'Computer engineering undergraduate specializing in application development and data systems.',
            'education.0.institution':'Example University','education.0.expected_graduation':'Expected Graduation 2027',
            'education.1.qualification':'Class XII, State Board','education.2.qualification':'Class X',
            'experience.0.organization':'Example Labs','experience.1.role':'Machine Learning Trainee','projects.0.url':'https://example.com/native-portal'},
            skills=['Python','FastAPI','React','PyTorch','PEFT','TypeScript','React Three Fiber','Three.js','JavaScript','C/C++','QLoRA','React Native','Docker','Git'],
            project_names=['Native Portal - Campus Directory','Voice Response Evaluation - Technical Assessment with Multiple Independently Written Title Words','Health Assistant - Team Project','Traffic Grid - Simulation Project'],
            project_technologies=[['Python','FastAPI','React'],['PyTorch','PEFT','FastAPI'],['FastAPI','React','TypeScript'],['React Three Fiber','Three.js']])]
    totals={'documents':len(cases),'field_correct':0,'field_total':0,'project_correct':0,'project_total':0,'skills_correct':0,'skills_total':0,
        'technology_associations_correct':0,'technology_associations_total':0,'statement_correct':0,'statement_total':0,
        'supported_values':0,'extracted_values':0,'unsupported_values':0,'provider_fallbacks':0,'ai_requested_runs':0,'expected_unreadable':0,'unexpected_failures':0}
    rows=[]
    with TemporaryDirectory(prefix='synthetic-intelligence-evaluation-') as scratch:
        settings=Settings(_env_file=None,app_env='test',data_dir=Path(scratch),document_storage_path=Path(scratch)/'documents',
            database_url=f'sqlite:///{scratch}/evaluation.db',session_secret='synthetic-evaluation-'*4,
            max_documents_per_user=30,groq_api_key='',groq_api_key_1='',groq_api_key_2='',groq_api_key_3='')
        with TestClient(create_app(settings),headers={'X-Workspace-Client':'careeros-web'}) as client:
            client.post('/api/v1/session')
            for case in cases:
                filename=case['file'];suffix=Path(filename).suffix
                mime={'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.txt':'text/plain'}[suffix]
                uploaded=client.post('/api/v1/documents/upload',files={'file':(filename,(FIXTURES/filename).read_bytes(),mime)})
                if uploaded.status_code not in {200,201}: raise RuntimeError('Synthetic fixture upload failed')
                doc=uploaded.json()['id'];started=time.perf_counter()
                client.post(f'/api/v1/documents/{doc}/process',json={'use_groq':True,'allow_external_processing':True})
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    status=client.get(f'/api/v1/documents/{doc}/status').json()
                    if status['status'] not in {'queued','processing'}:break
                    time.sleep(.05)
                row={'file':filename,'seconds':round(time.perf_counter()-started,3),'status':status['status'],'issues':[]}
                if status['status']=='failed':
                    if status['error_code']==case.get('error'):totals['expected_unreadable']+=1
                    else:totals['unexpected_failures']+=1;row['issues'].append(status['error_code'])
                    rows.append(row);continue
                totals['ai_requested_runs']+=1
                if (status.get('agent_run') or {}).get('status')=='fallback':totals['provider_fallbacks']+=1
                data=client.get(f'/api/v1/documents/{doc}/extracted-data').json();values=data['values']
                audit=client.get(f'/api/v1/documents/{doc}/audit').json()
                for field,wanted in case['expected'].items():
                    totals['field_total']+=1
                    if get(values,field)==wanted:totals['field_correct']+=1
                    else:row['issues'].append(field)
                    if field=='career_objective':
                        totals['statement_total']+=1
                        if values['career_objective']==wanted:totals['statement_correct']+=1
                totals['project_total']+=1
                if [p['name'] for p in values['projects']]==case['project_names']:totals['project_correct']+=1
                else:row['issues'].append('projects')
                if case.get('skills') is not None:
                    totals['skills_total']+=1
                    if {normalize(s) for s in values['skills']}=={normalize(s) for s in case['skills']}:totals['skills_correct']+=1
                    else:row['issues'].append('skills')
                for name,wanted in zip(case['project_names'],case['project_technologies']):
                    totals['technology_associations_total']+=1
                    project=next((p for p in values['projects'] if p['name']==name),None)
                    if project and {normalize(t) for t in project['technologies']}=={normalize(t) for t in wanted}:totals['technology_associations_correct']+=1
                    else:row['issues'].append('technologies')
                if not case['project_names']:
                    totals['technology_associations_total']+=1
                    if not values['projects']:totals['technology_associations_correct']+=1
                # All field leaves checked independently of confidence/status labels.
                by_id={b['block_id']:b for b in audit['exact_text']+audit['ocr']}
                for path,value in leaves(values).items():
                    totals['extracted_values']+=1;proof=data['evidence'].get(path,{})
                    refs=proof.get('references') or ([proof['source']] if proof.get('source') else [])
                    quotes='\n'.join(r['quote'] for r in refs)
                    supported=bool(refs) and all(r['block_id'] in by_id and normalize(r['quote']) in normalize(by_id[r['block_id']]['text']) for r in refs) and all(normalize(piece).removeprefix('https://').removeprefix('http://') in normalize(quotes) for piece in str(value).splitlines())
                    totals['supported_values' if supported else 'unsupported_values']+=1
                rows.append(row)
    ratios={label:round(totals[a]/totals[b],4) if totals[b] else None for label,a,b in [
        ('field_accuracy','field_correct','field_total'),('project_accuracy','project_correct','project_total'),('skill_accuracy','skills_correct','skills_total'),
        ('technology_association_accuracy','technology_associations_correct','technology_associations_total'),('statement_accuracy','statement_correct','statement_total'),
        ('evidence_support_rate','supported_values','extracted_values'),('unsupported_value_rate','unsupported_values','extracted_values'),('fallback_frequency','provider_fallbacks','ai_requested_runs')]}
    ratios['mean_processing_seconds']=round(sum(r['seconds'] for r in rows)/len(rows),3)
    return totals,ratios,rows


def main():
    import logging
    logging.disable(logging.CRITICAL)
    totals,ratios,rows=evaluate()
    report=f'# Multimodal evidence evaluation\n\n{len(rows)} synthetic documents: TXT, readable PDF, two-column PDF, dense two-page native PDF with fragmented font runs and a hyperlink annotation, DOCX table, blank/unreadable PDF, image-only PDF with real Tesseract OCR, and valid DOCX with LibreOffice rendering. No private resumes or live model calls.\n\n'
    report+='The full API/worker/audit path requests text AI with deliberately empty credentials. Fallback frequency measures this controlled provider-unavailable experiment, **not** a live-provider reliability estimate. Vision task behavior is evaluated separately with actual CrewAI execution and scripted transport in the test suite. These results describe the conservative local evidence pipeline, not model accuracy on arbitrary resumes.\n\n'
    report+='| Metric | Result |\n| --- | --- |\n'+''.join(f'| {key.replace("_"," ")} | {value} |\n' for key,value in ratios.items())
    report+='\nField accuracy uses literal oracle checks. Project and skill accuracy use exact per-document names/sets, including empty expectations. Technology associations are checked within the named project, including expected absence. Evidence support checks every emitted value against its actual native/OCR block and quote. Confidence is uncalibrated.\n\n'
    report+='| Fixture | Time (s) | Status | Oracle differences |\n| --- | --- | --- | --- |\n'+''.join(f'| {r["file"]} | {r["seconds"]} | {r["status"]} | {", ".join(r["issues"]) or "none"} |\n' for r in rows)
    report+='\nRaw counts:\n\n```json\n'+json.dumps(totals,indent=2)+'\n```\n\nReproduce: `.venv/bin/python -m scripts.evaluate_multimodal`. Tesseract and LibreOffice are optional runtime integrations but required to reproduce their successful fixture results. Tests simulate their absence/failure. More real-world layout varieties and human-labeled evidence are needed before general accuracy claims.\n'
    (ROOT/'docs/multimodal-evaluation.md').write_text(report)
    print(json.dumps({'counts':totals,'metrics':ratios},indent=2))


if __name__=='__main__':main()
