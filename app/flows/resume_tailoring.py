from copy import deepcopy
from typing import ClassVar
from pydantic import BaseModel,Field,PrivateAttr
from app.agents import runtime
from crewai.flow.flow import Flow,start,listen
from app.db.models import new_id
from app.schemas.resume_tailoring import ResumeDraft
from app.services.resume_tailoring_content import catalog,assemble,validate,edit_draft,report

def build_result(payload,source,selection=None,provider=None):
    facts,job,options=payload['facts'],payload['job'],payload['options']
    draft=assemble(facts,job,source,selection)
    # Repair only a rejected proposal, once, from known source selections.
    validation=validate(draft,source,facts,job)
    if not validation['valid']:raise ValueError('resume_unsupported_proposal')
    retained=[]
    if payload.get('base_draft') and options['regenerate_section']!='all':
        base=ResumeDraft.model_validate(payload['base_draft']);section=options['regenerate_section']
        draft.entries=[e.model_copy(deep=True) for e in base.entries if e.section!=section]+[e for e in draft.entries if e.section==section]
        draft.section_order=base.section_order[:]
        if section!='summary':draft.summary=base.summary;draft.summary_id=base.summary_id;draft.summary_evidence=base.summary_evidence
        retained=[s for s in draft.section_order if s!=section]
    if options.get('edits'):draft=edit_draft(draft,source,options['edits'])
    validation=validate(draft,source,facts,job)
    return {'catalog':source,'draft':draft.model_dump(),'validation':validation,'report':report(draft,source,facts),
        'provider':provider or {'engine':'local_source_selector','status':'local_selected','model':None},'retained_sections':retained}

class ResumeState(BaseModel):
    id:str=Field(default_factory=new_id)
    result:dict=Field(default_factory=dict)

class ResumeTailoringFlow(Flow[ResumeState]):
    _skip_auto_memory:ClassVar[bool]=True
    _p=PrivateAttr();_settings=PrivateAttr();_manager=PrivateAttr();_owner=PrivateAttr();_deadline=PrivateAttr();_callback=PrivateAttr()
    def __init__(self,payload,settings,manager,owner,deadline,callback=lambda kind,data:None):
        super().__init__(initial_state=ResumeState(),name='Career Orchestrator — Resume Tailoring',tracing=False,memory=None,persistence=None,checkpoint=False,suppress_flow_events=True,max_method_calls=5)
        self._p,self._settings,self._manager,self._owner,self._deadline,self._callback=payload,settings,manager,owner,deadline,callback
    @start()
    def relevance(self):
        self._callback('stage',{'stage':'Resume Relevance Analysis Agent'})
        self.state.result['catalog']=catalog(self._p['facts'],self._p['job'],self._p['context'])
    @listen(relevance)
    def tailoring(self):
        self._callback('stage',{'stage':'Resume Content Tailoring Agent'})
        source=self.state.result['catalog'];selection=None;metadata=None
        if self._p['options']['use_ai']:
            from app.crews.resume_tailoring import propose
            try:
                selection,metadata=propose(source,self._settings,self._manager,self._owner,self._deadline)
                # Independent validation before any user changes or base-version merge.
                candidate=assemble(self._p['facts'],self._p['job'],source,selection)
                checked=validate(candidate,source,self._p['facts'],self._p['job'])
                missing=[id for id in source['protected_ids'] if id not in selection['entry_ids']]
                by_id={e['id']:e for e in source['entries']}
                fixes=[]
                for id in missing:
                    entry=by_id[id];selection['entry_ids'].append(id)
                    fixes.append({'field':id,'correction':'Restored protected confirmed content omitted by the agent.'})
                for id in selection['entry_ids']:
                    entry=by_id[id]
                    if entry['section'] in {'experience','projects'} and entry['bullets'] and not any(b['id'] in selection['bullet_ids'] for b in entry['bullets']):
                        selection['bullet_ids'].append(entry['bullets'][0]['id'])
                        fixes.append({'field':id,'correction':'Restored a supported responsibility for the preserved entry.'})
                    if entry['section'] not in selection['section_order']:selection['section_order'].append(entry['section'])
                if fixes:
                    candidate=assemble(self._p['facts'],self._p['job'],source,selection)
                    checked=validate(candidate,source,self._p['facts'],self._p['job'])
                    metadata.update(status='repaired',correction='Host preservation checks restored confirmed content; agent ordering and valid wording retained.',corrections=fixes)
                if not checked['valid']:raise ValueError('resume_unsupported_proposal')
                if metadata['status']=='needs_host_validation':metadata.update(status='repaired',correction='One independent source check verified the exact selected facts, summary and wording; the model critique is retained.')
            except Exception as exc:
                selection=None;metadata={'engine':'local_source_selector','status':'fallback','model':self._settings.text_model,
                    'error_code':getattr(exc,'code','resume_proposal_rejected'),'correction':'Rejected/unavailable model proposal rebuilt once from confirmed evidence. No new facts were accepted.'}
        self.state.result=build_result(self._p,source,selection,metadata)
    @listen(tailoring)
    def validation(self):
        self._callback('stage',{'stage':'Resume Validation Agent'})
        return self.state.result
