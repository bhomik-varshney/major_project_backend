"""Application Orchestrator: typed CrewAI Flow, deterministic approval boundaries."""
from copy import deepcopy
import time
from typing import ClassVar
from pydantic import BaseModel,Field,PrivateAttr
from app.agents import runtime
from crewai.flow.flow import Flow,start,listen
from app.db.models import new_id
from app.integrations.application_mcp import call,MCPUnavailable
from app.schemas.applications import ApplicationDraft,ApplicationForm
from app.services.application_preparation import prepare_draft,validate_draft

class ApplicationState(BaseModel):
    id:str=Field(default_factory=new_id)
    result:dict=Field(default_factory=dict)


class ApplicationFlow(Flow[ApplicationState]):
    _skip_auto_memory:ClassVar[bool]=True
    _payload=PrivateAttr();_settings=PrivateAttr();_manager=PrivateAttr();_owner=PrivateAttr();_deadline=PrivateAttr();_callback=PrivateAttr()
    _tools=PrivateAttr(default_factory=list)
    _form=PrivateAttr(default=None)
    _connection=PrivateAttr(default_factory=lambda:{'status':'connected','form_source':'mcp'})
    def __init__(self,payload,settings,manager,owner,deadline,callback=lambda kind,data:None):
        super().__init__(initial_state=ApplicationState(),name='Career Orchestrator — Applications',tracing=False,memory=None,persistence=None,checkpoint=False,suppress_flow_events=True,max_method_calls=10)
        self._payload,self._settings,self._manager,self._owner,self._deadline,self._callback=payload,settings,manager,owner,deadline,callback
        self.state.result={'draft':None,'tasks':[],'warnings':[],'status':'completed','provider_metadata':{'ai_review':{'status':'not_selected'}}}
    def stage(self,role,callback):
        if time.monotonic()>=self._deadline:raise TimeoutError('application_timeout')
        tasks=self.state.result['tasks'];task={'name':role,'execution_id':new_id(),'status':'processing','method':'deterministic CrewAI Flow task'};tasks.append(task)
        self._callback('stage',{'stage':role,'tasks':deepcopy(tasks)})
        began=time.monotonic()
        result=callback()
        task.update(status='completed',elapsed_ms=round((time.monotonic()-began)*1000))
        return result
    @start()
    def inspect(self):
        def run():
            arguments={'job_id':self._payload['job']['id'],'application_id':self._payload.get('application_id',self.state.id)}
            if self._payload['provider']=='demo':arguments.update(scenario=self._payload.get('scenario','standard'),user_completed_step=self._payload.get('user_completed_step',False))
            self._callback('stage',{'stage':'Connecting to application MCP app','event_type':'MCP_CONNECTION','payload':{'status':'connecting'}})
            def retried():self._callback('stage',{'stage':'Retrying read-only form discovery','event_type':'RETRY','payload':{'attempt':2}})
            try:reply=call(self._settings,self._payload['provider'],'inspect_application',arguments,on_retry=retried)
            except MCPUnavailable as exc:
                from app.mcp.application_server import form_spec
                self._form=ApplicationForm.model_validate(form_spec());self._form.provider=self._payload['provider'];self._form.is_demo=self._payload['provider']=='demo'
                self._connection={'status':'unavailable','form_source':'local_fallback','error_code':exc.code,'retryable':exc.retryable}
                self._callback('stage',{'stage':'Local preparation fallback','tasks':deepcopy(self.state.result['tasks']),
                    'event_type':'MCP_CONNECTION','payload':{'status':'unavailable','code':exc.code}})
                return
            if reply['result'].get('job_id')!=self._payload['job']['id']:raise ValueError('job_identity_mismatch')
            self._tools=reply['tools']
            try:form=ApplicationForm.model_validate(reply['result']['form'])
            except (ValueError,KeyError):
                from app.mcp.application_server import form_spec
                form=ApplicationForm.model_validate(form_spec())
                self._connection={'status':'unavailable','form_source':'local_fallback','error_code':'unsupported_form','retryable':False}
            form.provider=self._payload['provider'];form.is_demo=self._payload['provider']=='demo'
            if form.resource_uri!='ui://ascendra/application':raise ValueError('unregistered_resource')
            self._form=form
            self._callback('stage',{'stage':'Application form discovered','tasks':deepcopy(self.state.result['tasks']),
                'event_type':'FORM_DISCOVERED','payload':{'field_count':len(form.fields),'unsupported_count':sum(f.type=='unsupported' for f in form.fields)}})
        self.stage('MCP Application Interaction Agent',run)
    @listen(inspect)
    def prepare(self):
        def run():
            self.state.result['draft']=prepare_draft(self._form,self._payload['facts'],self._payload['job'],self._payload['documents']).model_dump()
            self.state.result['draft']['connection']=self._connection
        self.stage('Application Preparation Agent',run)
    @listen(prepare)
    def map(self):
        self.stage('Application Form Mapping Agent',lambda:validate_draft(ApplicationDraft.model_validate(self.state.result['draft'])))
    @listen(map)
    def documents(self):
        def check():
            draft=self.state.result['draft'];options=draft['document_options']
            if len({d['id'] for d in options})!=len(options) or draft['documents']:raise ValueError('invalid_document_selection')
            draft['requirements']['document_generation_available']=True
            draft['requirements']['cover_letter_generation']='Owned cover-letter workspace; preview and exact-PDF approval required before attachment.'
            draft['requirements']['recommended_resume_ids']=[d['id'] for d in options if d['confirmed_resume']]
        self.stage('Document Selection Agent',check)
    @listen(documents)
    def preview(self):
        def run():
            from app.integrations.application_mcp import preview
            self._callback('stage',{'stage':'Generating readable MCP application preview','event_type':'PREVIEW_GENERATING'})
            self.state.result['draft']['preview']=preview(self._settings,self.state.result['draft'],self._payload['facts'],self._payload['job']).model_dump()
            self._callback('stage',{'stage':'Application preview generated','event_type':'PREVIEW_READY'})
        self.stage('Application Preview',run)
    @listen(preview)
    def tracking(self):
        def plan():
            self.state.result['draft']['requirements']['tracking_policy']={
                'job_id':self._payload['job']['id'],'duplicate_guard':'owner and normalized job identity',
                'submission_source':'explicit user report only','automatic_submission':False,
                'timeline_events':['note','deadline','follow_up','interview','status']}
        self.stage('Application Tracking Agent',plan)
    @listen(tracking)
    def review(self):
        draft=ApplicationDraft.model_validate(self.state.result['draft'])
        draft.tasks=deepcopy(self.state.result['tasks']);self.state.result['draft']=draft.model_dump()
        self._callback('checkpoint',deepcopy(self.state.result))
        if self._payload.get('use_ai'):
            from app.crews.applications import review_application
            try:
                draft.ai_review=review_application(draft,self._tools,self._settings,self._manager,self._owner,self._deadline)
            except Exception as exc:
                draft.ai_review={'status':'unavailable','error_code':getattr(exc,'code','application_agent_failed'),'model':self._settings.text_model}
                draft.warnings.append('Optional six-agent review unavailable; the grounded local draft remains available for your review.')
            self.state.result['provider_metadata']['ai_review']=draft.ai_review
        self.state.result['draft']=draft.model_dump()
    @listen(review)
    def validate(self):
        def run():
            draft=ApplicationDraft.model_validate(self.state.result['draft']);draft.validation=validate_draft(draft)
            if draft.validation['invalid_fields']:raise ValueError('application_mapping_invalid')
            self.state.result['draft']=draft.model_dump()
        self.stage('Application Validation Agent',run)
        self.state.result['draft']['tasks']=deepcopy(self.state.result['tasks'])
        return self.state.result
