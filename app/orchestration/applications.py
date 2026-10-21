"""Reuse bounded worker/key leases; MCP owns its subprocess tree via its launcher."""
from contextlib import redirect_stdout,redirect_stderr
import logging
import os
from app.orchestration.matching import execute_matching

def _application_child(payload,settings,manager,owner,deadline,scratch,output):
    logging.disable(logging.CRITICAL)
    os.environ['CREWAI_STORAGE_DIR']=scratch
    os.environ['OTEL_SDK_DISABLED']='true';os.environ['CREWAI_DISABLE_TELEMETRY']='true'
    with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
        try:
            if payload.get('operation')=='preview':
                from app.integrations.application_mcp import preview
                output.put(('result',{'preview':preview(settings,payload['draft'],payload['facts'],payload['job']).model_dump()}))
            elif payload.get('operation') in {'prefill','open','reconcile'}:
                from app.integrations.application_mcp import call,MCPUnavailable
                import json
                operation=payload['operation'];provider=payload['provider']
                output.put(('stage',{'stage':'Connecting to application MCP app','event_type':'MCP_CONNECTION','payload':{'status':'connecting'}}))
                async def progress(completed,total,message):
                    try:phase=json.loads(message or '{}').get('phase')
                    except (ValueError,TypeError):phase=None
                    if phase not in {'fields','uploads'}:return
                    if not isinstance(completed,(int,float)) or not isinstance(total,(int,float)) or not 0<=completed<=total<=100:return
                    output.put(('stage',{'stage':'Prefilling fields' if phase=='fields' else 'Uploading approved documents',
                        'event_type':'FIELD_PREFILL_PROGRESS' if phase=='fields' else 'UPLOAD_PROGRESS',
                        'payload':{'completed':completed,'total':total}}))
                if operation=='prefill':
                    packet=payload['packet']
                    arguments={'job_id':packet['job_id'],'application_id':packet['application_id']}
                    if provider=='demo':arguments['scenario']=packet.get('scenario','standard')
                    inspected=call(settings,provider,'inspect_application',arguments)['result']
                    if inspected.get('job_id')!=packet['job_id'] or inspected.get('form',{}).get('id')!=packet['form_id'] or inspected.get('form',{}).get('version')!=packet['form_version']:
                        raise MCPUnavailable('form_schema_changed')
                    from app.schemas.applications import ApplicationForm
                    from app.services.match_normalization import fingerprint
                    form=ApplicationForm.model_validate(inspected['form']);form.provider=provider;form.is_demo=provider=='demo'
                    if packet.get('form_fingerprint')!=fingerprint(form.model_dump()):raise MCPUnavailable('form_schema_changed')
                    result=call(settings,provider,'prefill_application',{'packet':packet},on_progress=progress)
                elif operation=='open':result=call(settings,provider,'open_application',{'application_id':payload['application_id'],'job_id':payload['job_id']})
                else:result=call(settings,provider,'application_status',{'application_id':payload['application_id'],'operation_id':payload['operation_id']})
                output.put(('result',result))
            else:
                from app.flows.applications import ApplicationFlow
                flow=ApplicationFlow(payload,settings,manager,owner,deadline,lambda kind,data:output.put((kind,data)))
                output.put(('result',flow.kickoff()))
        except Exception as exc:
            code=str(exc) if type(exc).__name__=='MCPUnavailable' else 'application_flow_failed'
            output.put(('error',{'code':code,'message':'Application workflow failed. Your documents and profile are unchanged.'}))

def execute_application(payload,settings,manager,progress=lambda stage:None):
    return execute_matching(payload,settings.model_copy(update={'matching_timeout_seconds':settings.application_timeout_seconds}),manager,progress,child_target=_application_child)
