"""Anonymized canonical evaluation, or isolated explicit private-document smoke."""
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse,json,sqlite3,time,logging
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.schemas.canonical import CanonicalResume
from app.services.resume_quality import source_check

ROOT=Path(__file__).resolve().parents[1]


def evaluate(files,live=False,private_output=None):
    results=[]
    oracle={case['file']:case for case in json.loads((ROOT/'tests/fixtures/resumes/cases.json').read_text())}
    def leaf(values,path):
        try:
            for key in path.split('.'):values=values[int(key)] if isinstance(values,list) else values[key]
            return values
        except (KeyError,IndexError,TypeError):return None
    with TemporaryDirectory(prefix='canonical-evaluation-') as scratch:
        options=dict(app_env='test',data_dir=Path(scratch),document_storage_path=Path(scratch)/'private',database_url=f'sqlite:///{scratch}/evaluation.db',max_documents_per_user=30,session_secret='canonical-synthetic-'*4)
        if not live:options.update(_env_file=None,groq_api_key='',groq_api_key_1='',groq_api_key_2='',groq_api_key_3='')
        settings=Settings(**options)
        with TestClient(create_app(settings),headers={'X-Workspace-Client':'careeros-web'}) as client:
            client.post('/api/v1/session')
            for filename,content in files:
                suffix=Path(filename).suffix
                mime={'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.txt':'text/plain'}[suffix]
                uploaded=client.post('/api/v1/documents/upload',files={'file':(filename,content,mime)});uploaded.raise_for_status();doc=uploaded.json()['id']
                started=time.monotonic()
                response=client.post(f'/api/v1/documents/{doc}/process',json={'use_groq':live,'allow_external_processing':live,'use_vision':live and suffix!='.txt','allow_image_processing':live and suffix!='.txt'});response.raise_for_status()
                for _ in range(1600):
                    status=client.get(f'/api/v1/documents/{doc}/status').json()
                    if status['status'] not in {'queued','processing'}:break
                    time.sleep(.15)
                if status['status']!='awaiting_review':raise RuntimeError('Evaluation processing failed: '+str(status.get('error_code')))
                audit=client.get(f'/api/v1/documents/{doc}/audit').json();draft=client.get(f'/api/v1/documents/{doc}/extracted-data').json()
                CanonicalResume.model_validate(audit['canonical'])
                raw=audit['raw_evidence'];blocks=[{'block_id':r['source_block_id'],'text':r['source_text']} for r in raw]
                checks=source_check(draft['values'],draft['evidence'],blocks)
                row={'fixture':'private_resume' if private_output else filename,'seconds':round(time.monotonic()-started,3),'status':status['status'],
                    'pages':len(audit['pages']),'raw_evidence':len(raw),'ocr_blocks':len(audit['ocr']),'native_blocks':len(audit['exact_text']),
                    'entities':len(audit['canonical']['entities']),'relations':len(audit['canonical']['relations']),'sections':len(audit['canonical']['sections']),
                    'projects':len(draft['values']['projects']),'education':len(draft['values']['education']),'experience':len(draft['values']['experience']),
                    'additional_sections':len(draft['values']['additional_sections']),'grounding':checks,'conflicts':len(audit['canonical']['conflicts']),
                    'canonical_validation':{'supported_fields':audit['canonical_validation']['supported_fields'],'unsupported_fields':audit['canonical_validation']['unsupported_fields'],'critical_passed':audit['canonical_validation']['critical_passed']},
                    'repair_outcomes':[r['outcome'] for r in audit['repair_iterations']],
                    'agents':[{'name':a['name'],'status':a['status'],'model':a['model'],'method':a['method']} for a in audit['agents']],
                    'profile_unchanged':client.get('/api/v1/profile').status_code==404}
                if not private_output:
                    expected=oracle.get(filename,{})
                    checks_oracle=[leaf(draft['values'],path)==value for path,value in expected.get('expected',{}).items()]
                    checks_oracle += [name in [p['name'] for p in draft['values']['projects']] for name in expected.get('project_names',[])]
                    checks_oracle += [skill in draft['values']['skills'] for skill in expected.get('skills',[])]
                    checks_oracle += [all(t in draft['values']['projects'][i]['technologies'] for t in tech) if len(draft['values']['projects'])>i else False for i,tech in enumerate(expected.get('project_technologies',[]))]
                    if filename=='connected_extras.txt':checks_oracle += [len(draft['values']['projects'])==1,len(draft['values']['additional_sections'])==6,draft['values']['projects'][0]['tools']==['Git','Docker'],draft['values']['projects'][0]['repository']=='https://github.com/example/campus-directory']
                    row['oracle_checks']=len(checks_oracle);row['oracle_correct']=sum(checks_oracle)
                if private_output:
                    private_output.mkdir(parents=True,exist_ok=True,mode=0o700)
                    for name,data in [('audit.json',audit),('draft.json',draft)]:
                        path=private_output/name;path.write_text(json.dumps(data,indent=2));path.chmod(0o600)
                    for page in audit['pages']:
                        data=client.get(f"/api/v1/documents/{doc}/audit/pages/{page['id']}");data.raise_for_status();path=private_output/f"page-{page['page']}.png";path.write_bytes(data.content);path.chmod(0o600)
                results.append(row)
    return {'cases':results,'unsupported_final_fields':sum(r['grounding']['unsupported_fields'] for r in results),'all_profiles_unchanged':all(r['profile_unchanged'] for r in results),'live_requested':live,'oracle_checks':sum(r.get('oracle_checks',0) for r in results),'oracle_correct':sum(r.get('oracle_correct',0) for r in results)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-db',type=Path);parser.add_argument('--document-id');parser.add_argument('--private-output',type=Path);parser.add_argument('--live',action='store_true');parser.add_argument('--output',type=Path);args=parser.parse_args()
    if args.source_db:
        if not args.document_id or not args.private_output:parser.error('Private evaluation requires an explicit document ID and private output directory')
        c=sqlite3.connect(f'file:{args.source_db.resolve()}?mode=ro',uri=True);row=c.execute('select filename,content from documents where id=?',(args.document_id,)).fetchone();c.close()
        if row is None:raise SystemExit('Requested source document not found')
        files=[row]
    else:
        names=['one_column.txt','objective_only.txt','unusual_headings.txt','multiple_projects.txt','project_technologies.txt','work_entries.txt','duplicate_skills.txt','missing_dates.txt','two_column.pdf','table_education.docx','image_resume.pdf','dense_native.pdf','connected_extras.txt']
        files=[(n,(ROOT/'tests/fixtures/resumes'/n).read_bytes()) for n in names]
    report=evaluate(files,args.live,args.private_output)
    if args.output:
        if args.source_db:parser.error('Private evaluation summaries must not be written to repository artifacts')
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
