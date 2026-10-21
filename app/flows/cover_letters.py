"""Typed CrewAI cover-letter flow; source verification remains authoritative."""
from typing import ClassVar
from pydantic import BaseModel,Field,PrivateAttr
from app.agents import runtime  # privacy configuration before importing Flow
from crewai.flow.flow import Flow,start,listen
from app.db.models import new_id
from app.schemas.cover_letters import SECTION_KEYS
from app.services.cover_letter_content import research,sentence_catalog,assemble,validate_content

class CoverState(BaseModel):
    id:str=Field(default_factory=new_id)
    result:dict=Field(default_factory=dict)

class CoverLetterFlow(Flow[CoverState]):
    _skip_auto_memory:ClassVar[bool]=True
    _p=PrivateAttr();_settings=PrivateAttr();_manager=PrivateAttr();_owner=PrivateAttr();_deadline=PrivateAttr();_callback=PrivateAttr()
    def __init__(self,payload,settings,manager,owner,deadline,callback=lambda kind,data:None):
        super().__init__(initial_state=CoverState(),name='Career Orchestrator — Cover Letters',tracing=False,memory=None,persistence=None,checkpoint=False,suppress_flow_events=True,max_method_calls=6)
        self._p,self._settings,self._manager,self._owner,self._deadline,self._callback=payload,settings,manager,owner,deadline,callback
    def stage(self,name):self._callback('stage',{'stage':name})
    @start()
    def relevance(self):
        self.stage('Researching relevant confirmed evidence')
        self.state.result['plan']=research(self._p['facts'],self._p['job'],self._p['context'])
    @listen(relevance)
    def write(self):
        self.stage('Writing evidence-grounded cover letter')
        p=self._p;options=p['options'];plan=self.state.result['plan']
        catalog=sentence_catalog(p['facts'],p['job'],plan,options['tone'])
        ids=[s.id for s in catalog]
        if options['length']!='expanded':
            projects=[id for id in ids if id.startswith('projects-')]
            ids=[id for id in ids if not id.startswith('achievements-') and (id not in projects or id==projects[0])]
        if options['length']=='short':ids=[id for id in ids if id not in {'approach','objective'}]
        if options['use_ai']:
            from app.crews.cover_letters import generate
            try:ids,metadata=generate(plan,[s for s in catalog if s.id in ids],self._settings,self._manager,self._owner,self._deadline)
            except Exception as exc:
                # One bounded repair: replace rejected agent selection with fully
                # checked local sentences. The fallback is always explicit.
                ids=[s.id for s in catalog]
                metadata={'engine':'local_evidence_composer','status':'fallback','model':self._settings.text_model,
                    'error_code':getattr(exc,'code',str(exc) if str(exc) in {'cover_invalid_output','unsupported_evidence','unsupported_claim','cover_validation_failed'} else 'cover_agent_validation_failed'),
                    'error_type':type(exc).__name__,'correction':'Rejected agent selection; rebuilt once from trusted sentence catalogue.'}
                if getattr(exc,'agents',None):metadata['agents']=exc.agents
        else:metadata={'engine':'local_evidence_composer','status':'local_selected','model':None}
        # Shorten only optional complete source-backed sentences; never invent or
        # truncate a factual claim just to hit a page target.
        known={s.id:s for s in catalog}
        cap=240 if options['length']=='short' else 450
        while sum(len(known[id].text.split()) for id in ids)>cap:
            optional=[id for id in ids if id in {'approach','objective'} or id.startswith(('achievements-','projects-','experience-'))]
            if not optional:break
            ids.remove(optional[-1])
        content=assemble(p['facts'],p['job'],options,catalog,ids)
        self.state.result['generated_content']=content.model_dump(mode='json')
        # Selected-section regeneration preserves every other previous paragraph.
        user_sections={};retained_sections=[]
        if p.get('base_content') and options['regenerate_section']!='all':
            base=p['base_content']
            for key in SECTION_KEYS:
                if key!=options['regenerate_section']:
                    setattr(content,key,base[key]);user_sections[key]=base[key];retained_sections.append(key)
        for key,text in options.get('edits',{}).items():setattr(content,key,text);user_sections[key]=text
        if options.get('personal_sentence'):
            content.motivation=(content.motivation+' '+options['personal_sentence']).strip();user_sections['motivation']=content.motivation
        retained_sections=[key for key in retained_sections if key not in options.get('edits',{}) and not (key=='motivation' and options.get('personal_sentence'))]
        content.claims=[c for c in content.claims if c.section not in user_sections]
        self.state.result.update(content=content.model_dump(mode='json'),catalog=[s.model_dump() for s in catalog],provider=metadata,user_sections=user_sections,retained_sections=retained_sections)
    @listen(write)
    def validate(self):
        self.stage('Validating every claim and paragraph')
        from app.schemas.cover_letters import LetterContent,Sentence
        result=self.state.result
        validation=validate_content(LetterContent.model_validate(result['content']),[Sentence.model_validate(s) for s in result['catalog']],self._p['facts'],self._p['job'],user_sections=result['user_sections'],retained_sections=result['retained_sections'])
        result['validation']=validation
        result['warnings']=result['plan']['warnings']+validation['warnings']
        if result['provider']['status']=='fallback':result['warnings'].append('CrewAI provider/validation failed. Explicit local evidence composition is active; AI generation did not complete.')
        return result
