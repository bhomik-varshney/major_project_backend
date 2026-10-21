"""A real MCP App for synthetic job applications; never visits an external site."""
import asyncio
import base64
from hashlib import sha256
import json
import os
import sqlite3
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.types import Resource,TextContent,Tool
from pydantic import AnyUrl

URI='ui://ascendra/application'
MIME='text/html;profile=mcp-app'
server=Server('ascendra-application-demo')


def form_spec():
    return {'id':'demo-application','version':'1.0','resource_uri':URI,'provider':'demo','is_demo':True,'fields':[
        {'id':'applicant_name','label':'Full name','type':'text','required':True,'semantic':'name'},
        {'id':'contact_email','label':'Email address','type':'email','required':True,'semantic':'email'},
        {'id':'contact_phone','label':'Phone number','type':'tel','semantic':'phone'},
        {'id':'current_city','label':'Current location','type':'text','semantic':'location'},
        {'id':'academic_background','label':'Education','type':'textarea','semantic':'education'},
        {'id':'work_history','label':'Experience','type':'textarea','semantic':'experience'},
        {'id':'skill_set','label':'Skills','type':'textarea','semantic':'skills'},
        {'id':'project_summary','label':'Projects','type':'textarea','semantic':'projects'},
        {'id':'portfolio_url','label':'Portfolio URL','type':'text','semantic':'links'},
        {'id':'role_motivation','label':'Career objective','type':'textarea','semantic':'career_objective'},
        {'id':'work_permission','label':'Work authorization','type':'radio','options':['Yes','No','Prefer to discuss'],'semantic':'work_authorization','sensitive':True,'required':True},
        {'id':'available_on','label':'Available from','type':'date','semantic':'availability'},
        {'id':'expected_pay','label':'Salary expectations','type':'text','semantic':'salary_expectations','sensitive':True},
        {'id':'work_choices','label':'Preferred work modes','type':'multiselect','options':['Remote','Hybrid','Onsite'],'semantic':'work_modes'},
        {'id':'contact_permission','label':'May the employer contact you?','type':'checkbox','semantic':'consent','sensitive':True},
        {'id':'resume_file','label':'Resume attachment','type':'file','semantic':'resume'},
        {'id':'cover_letter_file','label':'Cover letter attachment (optional)','type':'file','semantic':'cover_letter'},
    ]}


def digest(packet):return sha256(json.dumps(packet,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def journal(application_id,update=None):
    """Persist only action hashes/receipts, never field values/files/credentials."""
    from uuid import UUID
    safe_id=str(UUID(application_id))
    root=Path(os.environ.get('ASCENDRA_MCP_STATE_DIR','.data/application-mcp'))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'journal.db'
    with sqlite3.connect(path,timeout=5) as db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
        row=db.execute('SELECT data FROM sessions WHERE id=?',(safe_id,)).fetchone()
        data=json.loads(row[0]) if row else {'fields':{},'documents':{},'receipts':{},'schema_version':'1.0'}
        if update:
            update(data)
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)',(safe_id,json.dumps(data)))
    return data


def scenario_form(scenario,version='1.0'):
    form=form_spec();form['version']=version
    if scenario=='graduate':
        extra=[('first_name','First name','first_name'),('middle_name','Middle name','middle_name'),('last_name','Last name','last_name'),
            ('city','City','city'),('country','Country of residence','country'),('linkedin','LinkedIn URL','linkedin'),('github','GitHub URL','github'),
            ('institution','Institution','institution'),('degree','Degree','degree'),('graduation_year','Graduation year','graduation_year'),
            ('certifications','Certifications','certifications'),('internships','Internship experience','internships'),
            ('notice_period','Notice period','notice_period'),('motivation','Why do you want to join us?','motivation'),
            ('project_story','Tell us about a project','project_answer'),('relevant_experience','Describe your relevant experience','relevant_experience'),('greatest_achievement','What is your greatest achievement?','greatest_achievement')]
        form['fields'] += [{'id':id,'label':label,'type':'textarea' if semantic in {'motivation','relevant_experience','greatest_achievement','internships','project_answer'} else 'text','semantic':semantic} for id,label,semantic in extra]
        form['fields'] += [{'id':'demographics','label':'Gender (optional)','type':'select','semantic':'gender','sensitive':True,'options':['Woman','Man','Non-binary','Prefer not to answer']}]
    if scenario=='unsupported':form['fields'].append({'id':'custom_challenge','label':'Employer custom verification','type':'captcha','required':True})
    return form


async def notify(completed,total,phase):
    context=server.request_context
    token=getattr(context.meta,'progressToken',None) if context.meta else None
    if token is not None:await context.session.send_progress_notification(token,completed,total,json.dumps({'phase':phase}))


@server.list_tools()
async def tools():
    return [Tool(name='build_application_preview',description='Build a typed, human-readable local application preview. Never prefill or submit.',inputSchema={'type':'object','properties':{'draft':{'type':'object'},'facts':{'type':'object'},'job':{'type':'object'}},'required':['draft','facts','job'],'additionalProperties':False}),
        Tool(name='inspect_application',description='Read a synthetic application schema. No external navigation.',
        inputSchema={'type':'object','properties':{'job_id':{'type':'string'},'application_id':{'type':'string'},'scenario':{'type':'string'},'user_completed_step':{'type':'boolean'}},'required':['job_id'],'additionalProperties':False},
        **{'_meta':{'ui':{'resourceUri':URI}}}),
        Tool(name='prefill_application',description='Validate an approved packet and render it in the local demo app. Never submit.',
        inputSchema={'type':'object','properties':{'packet':{'type':'object'}},'required':['packet'],'additionalProperties':False}),
        Tool(name='application_status',description='Reconcile durable action receipts without applying any values.',inputSchema={'type':'object','properties':{'application_id':{'type':'string'},'operation_id':{'type':'string'}},'required':['application_id','operation_id'],'additionalProperties':False}),
        Tool(name='open_application',description='Open a local mock form session; no submission.',inputSchema={'type':'object','properties':{'application_id':{'type':'string'},'job_id':{'type':'string'}},'required':['application_id','job_id'],'additionalProperties':False})]


@server.list_resources()
async def resources():return [Resource(uri=AnyUrl(URI),name='Reviewed application',mimeType=MIME)]


@server.read_resource()
async def resource(uri:AnyUrl):
    if str(uri)!=URI:raise ValueError('Unknown resource')
    html=Path(__file__).with_name('ui').joinpath('application.html').read_text()
    return [ReadResourceContents(content=html,mime_type=MIME)]


@server.call_tool()
async def execute(name,arguments):
    if name=='build_application_preview':
        from app.schemas.applications import ApplicationDraft
        from app.services.application_transform import build_preview
        result={'preview':build_preview(ApplicationDraft.model_validate(arguments['draft']),arguments['facts'],arguments['job']).model_dump()}
    elif name=='inspect_application':
        scenario=arguments.get('scenario','standard');id=arguments.get('application_id')
        state=journal(id) if id else {'schema_version':'1.0'}
        if id and arguments.get('user_completed_step'):state=journal(id,lambda state:state.update(user_step_resolved=True))
        if scenario in {'authentication','unavailable'} and not state.get('user_step_resolved'):
            code='authentication_required' if scenario=='authentication' else 'external_page_unavailable'
            return [TextContent(type='text',text=json.dumps({'error':{'code':code,'retryable':scenario=='unavailable'}}))]
        result={'job_id':arguments['job_id'],'form':scenario_form(scenario,state['schema_version']),'external_submission_supported':False}
    elif name=='open_application':
        journal(arguments['application_id'],lambda state:state.update(opened=True))
        result={'opened':True,'job_id':arguments['job_id'],'application_id':arguments['application_id'],'is_demo':True,'external_url':URI}
    elif name=='application_status':
        state=journal(arguments['application_id'])
        operation=arguments['operation_id'];receipt=state['receipts'].get(operation)
        expected=state.get('operations',{}).get(operation)
        if not receipt and expected:
            applied=[key for key,value in expected['fields'].items() if state['fields'].get(key)==value]
            attached=[{'document_id':key,'sha256':value} for key,value in expected['documents'].items() if state['documents'].get(key)==value]
            skipped=[{'field_id':key,'code':'interrupted','retryable':True} for key in expected['fields'] if key not in applied]
            failed=[{'document_id':key,'sha256':value,'code':'interrupted','retryable':True} for key,value in expected['documents'].items() if state['documents'].get(key)!=value]
            receipt={'accepted':True,'submitted':False,'is_demo':True,'job_id':expected['job_id'],'operation_id':operation,
                'packet_digest':expected['packet_digest'],'applied_field_ids':applied,'attached_documents':attached,
                'skipped_fields':skipped,'failed_documents':failed,'outcome':'partial' if skipped or failed else 'complete'}
        result={'application_id':arguments['application_id'],'receipt':receipt,'outcome_known':bool(receipt)}
    elif name=='prefill_application':
        packet=arguments['packet'];scenario=packet.get('scenario','standard');state=journal(packet['application_id'])
        known={f['id']:f for f in scenario_form(scenario)['fields']}
        if scenario=='schema_change' and state['schema_version']=='1.0':
            journal(packet['application_id'],lambda state:state.update(schema_version='2.0'))
            return [TextContent(type='text',text=json.dumps({'error':{'code':'form_schema_changed','retryable':False}}))]
        if packet['form_id']!=form_spec()['id'] or packet['form_version']!=state['schema_version']:
            return [TextContent(type='text',text=json.dumps({'error':{'code':'form_schema_changed','retryable':False}}))]
        if any(key not in known or known[key]['type']=='file' for key in packet['values']):raise ValueError('Unknown field')
        for item in packet.get('completed_fields',[]):
            if state['fields'].get(item['field_id'])!=item['value_hash']:raise ValueError('Completed field changed')
        for item in packet.get('completed_documents',[]):
            if state['documents'].get(item['document_id'])!=item['sha256']:raise ValueError('Completed upload changed')
        existing=state['receipts'].get(packet['operation_id'])
        if existing:
            if existing['packet_digest']!=digest(packet):raise ValueError('Operation changed')
            return [TextContent(type='text',text=json.dumps(existing))]
        expected={'packet_digest':digest(packet),'job_id':packet['job_id'],
            'fields':{**{i['field_id']:i['value_hash'] for i in packet.get('completed_fields',[])},**{key:digest(value) for key,value in packet['values'].items()}},
            'documents':{d['document_id']:d['sha256'] for d in packet['documents']+packet.get('completed_documents',[])}}
        journal(packet['application_id'],lambda data:data.setdefault('operations',{}).update({packet['operation_id']:expected}))
        applied=[i['field_id'] for i in packet.get('completed_fields',[])];attached=list(packet.get('completed_documents',[]))
        skipped=[];failed=[];reused_fields=[];reused_docs=[]
        first_attempt=not state['receipts'];total=len(packet['values'])+len(packet['documents']);completed=0
        for index,(field,value) in enumerate(sorted(packet['values'].items())):
            if scenario=='partial' and first_attempt and index==0:
                skipped.append({'field_id':field,'code':'temporary_control_unavailable','retryable':True})
            else:
                value_hash=digest(value)
                if state['fields'].get(field)==value_hash:reused_fields.append(field)
                journal(packet['application_id'],lambda data:data['fields'].update({field:value_hash}));applied.append(field)
            completed+=1;await notify(completed,total,'fields')
        for document in packet['documents']:
            if sha256(base64.b64decode(document['content_base64'],validate=True)).hexdigest()!=document['sha256']:raise ValueError('Attachment integrity failure')
            if scenario=='upload_failure' and first_attempt:
                failed.append({'document_id':document['document_id'],'sha256':document['sha256'],'code':'upload_failed','retryable':True})
            else:
                if state['documents'].get(document['document_id'])==document['sha256']:reused_docs.append(document['document_id'])
                journal(packet['application_id'],lambda data:data['documents'].update({document['document_id']:document['sha256']}))
                attached.append({'document_id':document['document_id'],'sha256':document['sha256']})
            completed+=1;await notify(completed,total,'uploads')
        result={'accepted':True,'submitted':False,'is_demo':True,'job_id':packet['job_id'],
            'operation_id':packet['operation_id'],'packet_digest':digest(packet),
            'outcome':'partial' if skipped or failed else 'complete','skipped_fields':skipped,'failed_documents':failed,
            'applied_field_ids':sorted(applied),'attached_documents':attached,
            'reused_field_ids':reused_fields,'reused_document_ids':reused_docs,
            'message':'Approved values were received by the local demo. No employer was contacted.'}
        journal(packet['application_id'],lambda data:data['receipts'].update({packet['operation_id']:result}))
    else:raise ValueError('Tool not permitted')
    return [TextContent(type='text',text=json.dumps(result))]


async def main():
    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())

if __name__=='__main__':asyncio.run(main())
