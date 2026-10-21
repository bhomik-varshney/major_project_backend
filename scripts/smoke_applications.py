"""Synthetic API/MCP smoke checks; --live reviews redacted metadata with Groq."""
import argparse
from contextlib import redirect_stdout,redirect_stderr
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.providers.groq import GroqKeyManager
from app.providers.jobs import SeededJobProvider
from app.orchestration.applications import execute_application
from app.services.match_normalization import fingerprint

FACTS={'name':'Synthetic Application Example','email':'application@example.com','skills':['Python','SQL'],
    'education':[{'institution':'Example University','qualification':'B.Tech'}],
    'projects':[{'name':'Synthetic API','description':'Built an API','technologies':['Python']}],
    'experience':[],'career_objective':'Backend internship','preferred_locations':['Bengaluru, India']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    began=time.monotonic()
    with TemporaryDirectory(prefix='application-smoke-') as temporary:
        root=Path(temporary)
        settings=Settings(_env_file=None,app_env='test',database_url=f'sqlite:///{root}/test.db',data_dir=root,
            document_storage_path=root/'documents',session_secret='synthetic-application-smoke-'*3)
        if args.live:
            configured=Settings()
            settings=settings.model_copy(update={key:getattr(configured,key) for key in ['groq_api_key','groq_api_key_1','groq_api_key_2','groq_api_key_3','groq_model','groq_text_model']})
            job=next(j for j in SeededJobProvider().snapshot().jobs if j.category=='backend' and 'Bengaluru' in j.location)
            payload={'facts':FACTS,'job':job.model_dump(mode='json'),'documents':[],'provider':'demo','use_ai':True}
            result=execute_application(payload,settings,GroqKeyManager(settings.groq_keys))
            draft=result['draft'];review=draft['ai_review']
            report={'mode':'synthetic_redacted_live_groq','engine':'crewai.Flow + crewai.Crew + MCP stdio',
                'status':review['status'],'model':review.get('model'),'agents':[{'name':t['agent'],'status':t['status']} for t in review.get('tasks',[])],
                'stages':result['tasks'],'field_count':len(draft['fields']),'automatically_approved_fields':sum(f['approved'] for f in draft['fields']),
                'automatic_attachments':len(draft['documents']),'profile_database_loaded':False,
                'export_scope':'field IDs, field types/classifications, confirmed document IDs, discovered tool names; no candidate values',
                'elapsed_seconds':round(time.monotonic()-began,4)}
        else:
            app=create_app(settings);headers={'X-Workspace-Client':'careeros-web'}
            with TestClient(app,headers=headers) as client:
                def send(method,path,**kwargs):
                    response=client.request(method,'/api/v1'+path,**kwargs)
                    if not response.is_success:raise RuntimeError(f'{method} {path}: {response.status_code}')
                    return response.json()
                send('POST','/session');profile=send('POST','/profile',json=FACTS)
                send('POST','/profile/verify',json={'expected_revision':profile['revision'],'confirmed':True})
                before=send('GET','/profile')
                job=send('GET','/jobs?category=backend&location=Bengaluru&availability=active&page_size=1')['items'][0]
                send('PUT',f'/matching/bookmarks/{job["id"]}')
                record=send('POST','/applications',json={'job_id':job['id']});states=[record['status']]
                def act(action,**extra):
                    nonlocal record
                    record=send('POST',f'/applications/{record["id"]}/actions',json={'action':action,'expected_revision':record['revision'],**extra})
                    states.append(record['status'])
                def wait():
                    nonlocal record
                    end=time.monotonic()+30
                    while time.monotonic()<end:
                        record=send('GET',f'/applications/{record["id"]}')
                        if record['status'] not in {'preparing','prefilling'}:
                            states.append(record['status']);return
                        time.sleep(.1)
                    raise RuntimeError('application_timeout')
                act('prepare');wait()
                if record['status']!='needs_user_review':raise RuntimeError('preparation_failed')
                fields={f['id']:{'value':f['value'],'approved':f['value'] is not None} for f in record['draft']['fields'] if f['id'] not in {'resume_file','cover_letter_file'}}
                fields['work_permission']={'value':'Yes','approved':True}
                act('review',fields=fields);act('approve_prefill',confirmed=True);act('prefill',confirmed=True);wait()
                if record['status']!='prefill_complete':raise RuntimeError('prefill_failed')
                receipt=record['receipt'];act('approve_submission',confirmed=True);act('record_submission',confirmed=True);act('start_tracking')
                timeline=send('GET',f'/applications/{record["id"]}/timeline')
                replay=client.get(f'/api/v1/applications/{record["id"]}/events?follow=false')
                report={'mode':'synthetic_api_real_spawned_flow_and_stdio','status':'passed','states':states,
                    'field_count':len(record['draft']['fields']),'submitted_by_provider':receipt['submitted'],
                    'tracking_source':record['receipt']['submission']['source'],'is_demo':record['is_demo'],
                    'profile_unchanged':fingerprint(before)==fingerprint(send('GET','/profile')),
                    'timeline_event_count':len(timeline),'sequential_events':[e['seq'] for e in timeline]==list(range(1,len(timeline)+1)),
                    'ag_ui_replay_status':replay.status_code,'elapsed_seconds':round(time.monotonic()-began,4)}
    if args.output:args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
