"""Genuine CrewAI Flow for evidence -> canonical graph -> validated draft."""
from copy import deepcopy
import re
import time
from typing import ClassVar
from pydantic import BaseModel,Field,PrivateAttr
from app.agents import runtime  # Disable implicit telemetry before importing Flow.
from crewai.flow.flow import Flow,start,listen

from app.schemas.canonical import CanonicalRepairInput,RepairIteration
from app.schemas.extraction import CandidateDraft,FieldEvidence,SourceReference
from app.services.canonical_resume import raw_evidence,add_additional_sections,build_canonical,validate_canonical,project_draft
from app.services.resume_structure import leaves,normalize


class CanonicalState(BaseModel):
    id:str=''
    tasks:list[dict]=Field(default_factory=list)


class ResumeCanonicalFlow(Flow[CanonicalState]):
    _skip_auto_memory:ClassVar[bool]=True
    _data=PrivateAttr();_settings=PrivateAttr();_manager=PrivateAttr();_progress=PrivateAttr();_repair=PrivateAttr();_use_ai=PrivateAttr()
    _values=PrivateAttr();_proof=PrivateAttr();_graph=PrivateAttr(default=None);_raw=PrivateAttr(default_factory=list)
    _validation=PrivateAttr(default=None);_iterations=PrivateAttr(default_factory=list);_initial=PrivateAttr(default=None)
    _started=PrivateAttr(default_factory=dict)

    def __init__(self,data,settings,manager,progress=lambda stage:None,use_ai=False,repair=None):
        super().__init__(initial_state=CanonicalState(id=data['id']),name='Evidence Connected Resume Workflow',memory=None,tracing=False,
            checkpoint=False,persistence=None,suppress_flow_events=True,max_method_calls=12)
        self._data,self._settings,self._manager,self._progress,self._use_ai=data,settings,manager,progress,use_ai
        if repair is None:
            from app.crews.canonical_repair import request_repair
            repair=request_repair
        self._repair=repair;self._values=deepcopy(data['values']);self._proof=deepcopy(data['evidence'])

    def begin(self,stage,label):
        self._progress(stage);self._started[stage]=time.monotonic()
        self.state.tasks.append({'name':stage,'label':label,'method':'crewai.Flow / deterministic','status':'processing'})

    def complete(self,stage,**metadata):
        next(t for t in self.state.tasks if t['name']==stage).update(status='completed',elapsed_ms=round((time.monotonic()-self._started[stage])*1000),**metadata)

    @start()
    def quality_analysis(self):
        self.begin('analysing','Document Quality and Layout')
        quality=self._data['quality'];pages=quality.get('pages',[])
        quality.update(document_type=self._data['file_type'],text_available=any(b['text'].strip() for b in self._data['exact_text']),
            columns_detected=2 if quality.get('columns')=='multiple_suspected' else None,
            quality_warnings=quality.get('limitations',[]),
            visual_features={kind:sum(b.get('method')=='vision' and b.get('block_type')==kind for b in self._data['layout']) for kind in ('table','sidebar','card','icon')},
            scanned_pages=[p['page'] for p in pages if p['selectable_characters']<40],pages_requiring_ocr=[p['page'] for p in pages if p['ocr_recommended']],
            pages_requiring_vision=[p['page'] for p in pages if p['ocr_recommended'] or p['page'] in quality.get('image_heavy_pages',[]) or any(b.get('page')==p['page'] and b.get('reading_order_uncertain') for b in self._data['blocks'])],
            layout_type='multi_column_suspected' if quality.get('columns')=='multiple_suspected' else 'table_based' if quality.get('tables_detected') else 'one_column_or_unknown',
            recommended_processing_path='native_plus_page_ocr' if quality.get('ocr_recommended') else 'native_text_with_layout')
        self.complete('analysing',pages=quality.get('page_count'))

    @listen(quality_analysis)
    def exact_evidence(self):
        self.begin('extracting','Exact Text and Evidence')
        self._raw=raw_evidence(self._data['blocks'])
        if len({r.evidence_id for r in self._raw})!=len(self._raw):raise ValueError('duplicate_evidence_id')
        self.complete('extracting',evidence_records=len(self._raw))

    @listen(exact_evidence)
    def multimodal_evidence(self):
        self.begin('ocr_vision_evidence','OCR and Vision Evidence Reconciliation')
        self.complete('ocr_vision_evidence',ocr_blocks=sum(r.extraction_method=='ocr' for r in self._raw),
            supported_visual_blocks=sum(b.get('method')=='vision' and b.get('status')=='supported' for b in self._data['layout']))

    @listen(multimodal_evidence)
    def canonicalize(self):
        self.begin('canonicalizing','Connected Canonical Resume Representation')
        self._values,self._proof=add_additional_sections(self._values,self._proof,self._data['blocks'])
        self.rebuild();self._initial=self._graph.model_dump(mode='json')
        self.complete('canonicalizing',entities=len(self._graph.entities),relationships=len(self._graph.relations))

    def rebuild(self):
        self._graph,self._raw=build_canonical(self._data['id'],self._values,self._proof,self._data['blocks'],self._data['layout'],self._data['warnings'])
        self._validation=validate_canonical(self._graph,self._raw,self._values,self._proof,self._data['blocks'])

    @listen(canonicalize)
    def validate(self):
        self.begin('validating','Source, Schema and Relationship Validation')
        self.rebuild();self.complete('validating',**self._validation.checks)

    @listen(validate)
    def repair_loop(self):
        self.begin('repairing','Bounded Targeted Verification and Repair')
        # Restore exact spelling from native/OCR text, never a visual guess.
        start_time=time.monotonic();changes=[]
        for path,value in leaves(self._values).items():
            proof=self._proof.get(path,{})
            refs=proof.get('references') or ([proof['source']] if proof.get('source') else [])
            if len(refs)!=1 or '\n' in str(value) or path.endswith('.heading') or str(value).startswith(('https://','http://')):continue
            ref=refs[0];block=next((b for b in self._data['blocks'] if b['block_id']==ref['block_id']),None)
            if not block:continue
            found=re.search(r'\s+'.join(re.escape(p) for p in str(value).split()),block['text'],re.I)
            if found and found.group()!=value and normalize(found.group())==normalize(value):
                self.set_path(path,found.group());changes.append({'field':path,'before':value,'after':found.group(),'source_block_id':ref['block_id']})
        self.rebuild()
        self._iterations.append(RepairIteration(iteration=0,detected_problems=self._validation.issues,repair_action='Native/OCR spelling reconciliation and full source validation',affected_fields=[c['field'] for c in changes],
            validation_result=self._validation,elapsed_ms=round((time.monotonic()-start_time)*1000),outcome='validated' if self._validation.critical_passed else 'needs_review').model_dump())
        self._iterations[-1]['changes']=changes
        deadline=time.monotonic()+self._settings.resume_repair_timeout_seconds
        for iteration in range(1,self._settings.resume_max_repair_cycles+1):
            problems=[i for i in self._validation.issues if i.repairable and i.field in leaves(self._values)]
            if not problems:break
            self._progress(None);started=time.monotonic();allowed=sorted({p.field for p in problems})
            if not self._use_ai or not self._manager.configured:
                self._iterations.append(RepairIteration(iteration=iteration,detected_problems=problems,repair_action='External repair not selected or unavailable; retained source evidence for manual review',affected_fields=allowed,
                    validation_result=self._validation,elapsed_ms=0,outcome='skipped').model_dump());break
            prior=deepcopy(self._values);changed=[];code=None
            # Send only affected blocks and their established entry groups, bounded
            # by an independent repair deadline and source-character budget.
            ids={id for e in self._graph.entities for f in e.fields.values() if f.profile_path in allowed for id in f.source_block_ids}
            groups={self._proof.get(p,{}).get('group_id') for p in allowed}-{None,''}
            ids.update((proof.get('source') or {}).get('block_id') for proof in self._proof.values() if proof.get('group_id') in groups)
            blocks=[b for b in self._data['blocks'] if b['block_id'] in ids]
            if sum(len(b['text']) for b in blocks)>self._settings.groq_source_batch_chars:blocks=[]
            if not blocks:
                action='No bounded source context for a safe repair; manual review required';outcome='no_progress'
            else:
                payload=CanonicalRepairInput(file_type=self._data['file_type'],blocks=[{'block_id':b['block_id'],'text':b['text']} for b in blocks],
                    issues=[p.model_dump() for p in problems],allowed_paths=allowed,context={'entry_groups':{p:self._proof.get(p,{}).get('group_id') for p in allowed}})
                try:
                    result=self._repair(payload,self._settings,self._manager,deadline,self._progress)
                    changed=self.apply_claims(result.claims,allowed)
                    self.rebuild();outcome='validated' if self._validation.critical_passed else 'needs_review'
                    action='Applied only source-supported patches to permitted fields'
                    if self._values==prior:outcome='no_progress'
                except Exception as exc:
                    # Cancellation must reach the owning worker, never become a fallback.
                    self._progress(None)
                    code=getattr(exc,'code','canonical_repair_failed');action='AI repair unavailable; deterministic draft retained';outcome='unavailable'
            self._iterations.append(RepairIteration(iteration=iteration,detected_problems=problems,repair_action=action,affected_fields=changed,
                validation_result=self._validation,model=self._settings.text_model if blocks else None,provider='groq' if blocks else 'local',elapsed_ms=round((time.monotonic()-started)*1000),outcome=outcome,error_code=code).model_dump())
            if outcome in {'no_progress','unavailable'} or time.monotonic()>=deadline:break
        self.complete('repairing',cycles=len([r for r in self._iterations if r['iteration']>0]),maximum_cycles=self._settings.resume_max_repair_cycles)

    def set_path(self,path,value):
        parts=path.split('.');node=self._values
        for key in parts[:-1]:node=node[int(key)] if isinstance(node,list) else node[key]
        if isinstance(node,list):node[int(parts[-1])]=value
        else:node[parts[-1]]=value

    def apply_claims(self,claims,allowed):
        changed=[];by_id={b['block_id']:b for b in self._data['blocks']}
        for claim in claims:
            if claim.path not in allowed or claim.block_id not in by_id:continue
            old=self._proof.get(claim.path,{});group=old.get('group_id')
            permitted={p['source']['block_id'] for p in self._proof.values() if p.get('source') and group and p.get('group_id')==group}
            if permitted and claim.block_id not in permitted:continue
            block=by_id[claim.block_id];match=re.search(r'\s+'.join(re.escape(p) for p in claim.value.split()),block['text'])
            if not match:continue
            prior_values,prior_proof=deepcopy(self._values),deepcopy(self._proof)
            self.set_path(claim.path,match.group())
            self._proof[claim.path]=FieldEvidence(source=SourceReference(block_id=claim.block_id,page=block.get('page'),paragraph=block.get('paragraph'),quote=match.group()).model_dump(),
                confidence=claim.confidence,method='groq',group_id=group or '',original_text=match.group(),classification='ambiguous' if block.get('extraction_method')=='ocr' else 'directly_found').model_dump()
            try:self._values=CandidateDraft.model_validate(self._values).model_dump();changed.append(claim.path)
            except ValueError:self._values,self._proof=prior_values,prior_proof
        return changed

    @listen(repair_loop)
    def prepare_review(self):
        self.begin('review_preparation','Final Validated Profile Draft Preparation')
        values,evidence=project_draft(self._graph,self._values,self._proof)
        self.complete('review_preparation',review_required=True,profile_updated=False)
        return {'raw_evidence':[r.model_dump(mode='json') for r in self._raw],'canonical':self._graph.model_dump(mode='json'),
            'canonical_initial':self._initial,'canonical_validation':self._validation.model_dump(mode='json'),'repair_iterations':self._iterations,
            'canonical_workflow':{'engine':'crewai.Flow','tasks':self.state.tasks,'maximum_repair_cycles':self._settings.resume_max_repair_cycles},
            'values':values,'evidence':evidence,'canonical_status':'needs_review' if self._validation.issues else 'draft_ready'}
