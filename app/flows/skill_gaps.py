"""Career Orchestrator extension: genuine CrewAI Flow with typed evidence stages."""
from copy import deepcopy
from datetime import date
import time
from typing import ClassVar
from pydantic import BaseModel, Field, PrivateAttr
from app.agents import runtime
from crewai.flow.flow import Flow,start,listen
from app.agents.skill_gaps import CONTRACTS
from app.agents.matching import MatchingTaskTrace,ORCHESTRATOR
from app.db.models import new_id
from app.schemas.jobs import Job
from app.schemas.skill_gaps import GapResults
from app.services.gap_taxonomy import vocabulary
from app.services.skill_gap_analysis import skill_inventory,job_requirements,compare,aggregate_gaps,validate_analysis,score_context

class GapState(BaseModel):
    id: str = Field(default_factory=new_id)
    result: dict = Field(default_factory=dict)

class SkillGapFlow(Flow[GapState]):
    _skip_auto_memory: ClassVar[bool]=True
    _payload=PrivateAttr();_settings=PrivateAttr();_manager=PrivateAttr();_owner=PrivateAttr();_deadline=PrivateAttr();_callback=PrivateAttr()
    _jobs=PrivateAttr(default_factory=list);_terms=PrivateAttr(default_factory=list);_inventory=PrivateAttr(default_factory=list);_requirements=PrivateAttr(default_factory=list);_comparisons=PrivateAttr(default_factory=list);_gaps=PrivateAttr(default_factory=list);_times=PrivateAttr(default_factory=dict)

    def __init__(self,payload,settings,manager,owner,deadline,callback=lambda kind,data:None):
        super().__init__(initial_state=GapState(),name='Career Orchestrator — Skill Gap Analysis',tracing=False,memory=None,persistence=None,checkpoint=False,suppress_flow_events=True,max_method_calls=10)
        self._payload,self._settings,self._manager,self._owner,self._deadline,self._callback=payload,settings,manager,owner,deadline,callback
        self.state.result={'inventory':[],'requirements':[],'comparisons':[],'gaps':[],'jobs':[],'candidate_snapshot':payload['candidate_snapshot'],'selection':deepcopy(payload['selection']),'warnings':[],'conflicts':[],'validation':{},'tasks':[],'provider_metadata':{'orchestrator':ORCHESTRATOR,'ai_review':{'status':'not_selected','model':None},'scoring':{'provider':'local','strategy':'existing hybrid scorer; fixed TF-IDF features','semantic':'not used in counterfactual estimates'}},'status':'completed'}

    def begin(self,stage):
        if time.monotonic()>=self._deadline:raise TimeoutError('skill_gap_timeout')
        role,inputs,outputs,failure=CONTRACTS[stage];self._times[stage]=time.monotonic();tasks=self.state.result['tasks']
        tasks.append(MatchingTaskTrace(stage=stage,name=role,execution_id=new_id(),status='processing',method='deterministic CrewAI Flow task',inputs={'schema':inputs,'profile_source':'confirmed_fields_only'},output_schema=outputs,depends_on=[tasks[-1]['stage']] if tasks else [],responsibility=role,failure_policy=failure).model_dump())
        self._callback('stage',{'stage':stage,'tasks':deepcopy(tasks)})

    def finish(self,stage,**data):
        task=next(t for t in self.state.result['tasks'] if t['stage']==stage)
        status=data.pop('status','completed')
        task.update(status=status,elapsed_ms=round((time.monotonic()-self._times[stage])*1000),**data)

    @start()
    def prepare(self):
        self.begin('preparing')
        self._jobs=[Job.model_validate(j) for j in self._payload['jobs']]
        self._terms=self._payload['vocabulary']
        if self._payload['mode']=='target' and len(self._jobs)>self._payload['top_k']:
            rows,_=score_context(self._payload['candidate_snapshot']['facts'],self._jobs,self._settings,date.fromisoformat(self._payload['as_of']))
            self._jobs=sorted(self._jobs,key=lambda j:(-rows[j.id].hybrid_score,j.id))[:self._payload['top_k']]
        if not self._jobs:self.state.result['warnings'].append('No jobs passed the selected source/target filters; no role requirements were invented.')
        self.state.result['selection']['analysed_count']=len(self._jobs)
        self.finish('preparing',jobs=len(self._jobs))

    @listen(prepare)
    def profile(self):
        self.begin('analysing_profile');snapshot=self._payload['candidate_snapshot']
        self._inventory=skill_inventory(snapshot['facts'],snapshot['proofs'],self._terms)
        self.state.result['inventory']=[s.model_dump() for s in self._inventory]
        if not self._inventory:self.state.result['warnings'].append('Candidate skill evidence is unavailable. Unknown comparisons are not proof of missing ability.')
        for field in ('skills','projects','experience','education'):
            if not snapshot['facts'].get(field):self.state.result['warnings'].append(f'{field} is empty or unconfirmed; evidence remains unknown.')
        self.finish('analysing_profile',inventory_count=len(self._inventory),proficiency_inferred=False)

    @listen(profile)
    def requirements(self):
        self.begin('analysing_requirements')
        self._requirements,warnings=job_requirements(self._jobs);self.state.result['warnings']+=warnings
        self.state.result['requirements']=[r.model_dump() for r in self._requirements]
        self.finish('analysing_requirements',requirements=len(self._requirements))

    @listen(requirements)
    def comparison(self):
        self.begin('comparing_skills');self._comparisons=compare(self._inventory,self._requirements)
        self.state.result['comparisons']=[c.model_dump() for c in self._comparisons]
        for c in self._comparisons:
            if c.review_status=='needs_review':self.state.result['conflicts'].append({'stage':'comparing_skills','code':'candidate_evidence_conflict','job_id':c.job_id,'skill':c.normalized,'evidence':[e.model_dump() for e in c.candidate_evidence]})
        self.finish('comparing_skills',comparisons=len(self._comparisons))

    @listen(comparison)
    def priority(self):
        self.begin('prioritising_gaps')
        self._gaps,rows=aggregate_gaps(self._payload['candidate_snapshot']['facts'],self._inventory,self._comparisons,self._jobs,self._payload['corpus_counts'],self._payload['corpus_count'],self._settings,date.fromisoformat(self._payload['as_of']),self._payload.get('saved_scores'))
        self.state.result['gaps']=[g.model_dump() for g in self._gaps]
        for job in self._jobs:
            row=rows[job.id]
            self.state.result['jobs'].append({'job':job.model_dump(mode='json'),'current_score':row.hybrid_score,'saved_match_score':self._payload.get('saved_scores',{}).get(job.id),'components':{k:v.model_dump() for k,v in row.components.items()},'calculation':row.calculation})
        self.finish('prioritising_gaps',gaps=len(self._gaps),simulation_profile_mutations=0)

    @listen(priority)
    def guidance(self):
        self.begin('generating_guidance')
        self._validate()
        self._callback('checkpoint',deepcopy(self.state.result))
        if self._payload['use_ai']:
            from app.crews.skill_gaps import review_gap_guidance
            try:
                metadata,conflicts=review_gap_guidance(self._gaps,self._settings,self._manager,self._owner,self._deadline)
                self.state.result['provider_metadata']['ai_review']=metadata
                self.state.result['conflicts']+=conflicts
                self.state.result['gaps']=[g.model_dump() for g in self._gaps]
                self.finish('generating_guidance',status='partial' if conflicts else 'completed',method='crewai.Crew',reviewed_gaps=metadata.get('reviewed_gaps',0))
            except Exception as exc:
                code=getattr(exc,'code','skill_gap_ai_failed')
                self.state.result['status']='partial';self.state.result['warnings'].append('Optional AI guidance review unavailable ('+code+'); validated local comparisons and guidance retained.')
                self.state.result['provider_metadata']['ai_review']={'status':'unavailable','model':self._settings.text_model,'error_code':code}
                self.finish('generating_guidance',status='partial',method='grounded_rules',error_code=code)
        else:self.finish('generating_guidance',method='grounded_rules')

    def _validate(self):
        self.state.result['validation']=validate_analysis(self._payload['candidate_snapshot']['facts'],self._jobs,self._inventory,self._requirements,self._comparisons,self._gaps,self._settings,self._payload['corpus_counts'],self._payload['corpus_count'],self._terms,date.fromisoformat(self._payload['as_of']),self._payload.get('saved_scores'))

    @listen(guidance)
    def validation(self):
        self.begin('validating');self._validate()
        if self.state.result['conflicts']:self.state.result['status']='needs_review'
        self.finish('validating',**self.state.result['validation'])
        self.state.result['warnings']=list(dict.fromkeys(self.state.result['warnings']))
        return GapResults.model_validate(self.state.result).model_dump(mode='json')
