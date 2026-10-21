from concurrent.futures import ThreadPoolExecutor
import logging
import time
from threading import BoundedSemaphore
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.db.models import Document, DocumentExtraction, ResumeAgentRun, ResumeProcessingAudit, ResumePageArtifact, now
from app.orchestration import resume as resume_intelligence
from app.providers.groq import GroqKeyManager, ProviderUnavailable
from app.services.resume_quality import check_resume,source_check
from app.services.resume_structure import deterministic_extract, merge_enhancement
from app.services.document_intelligence import prepare_isolated
from app.services.intelligence_audit import validate_layout, enrich_evidence, source_conflicts, other_sections
from app.orchestration.vision import analyse_pages
from app.schemas.intelligence import ValidationWarning


ACTIVE=('queued','processing')
logger=logging.getLogger('careeros.processing')


class ProcessingStopped(Exception):
    pass


class DocumentProcessor:
    """A bounded local queue; consented agent work runs in a killable child."""
    def __init__(self,factory,settings):
        self.factory,self.settings=factory,settings
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='resume')
        self.capacity=BoundedSemaphore(4)
        self.key_manager=GroqKeyManager(settings.groq_keys,settings.groq_key_cooldown_seconds)

    def recover_interrupted(self):
        with self.factory() as db:
            db.execute(update(DocumentExtraction).where(DocumentExtraction.status.in_(ACTIVE)).values(
                status='failed',stage='failed',active_document=None,error_code='processing_interrupted',
                error_message='Processing was interrupted by a restart. Retry when ready.',updated_at=now(),completed_at=now()))
            interrupted = select(DocumentExtraction.id).where(DocumentExtraction.error_code == 'processing_interrupted')
            db.execute(update(ResumeAgentRun).where(ResumeAgentRun.extraction_id.in_(interrupted)).values(
                status='failed',error_code='processing_interrupted',completed_at=now()))
            db.commit()

    def reserve(self):
        if not self.capacity.acquire(blocking=False):
            raise AppError(429,'processing_busy','The local processing queue is full. Try again after another document finishes.')

    def submit(self,extraction_id,use_groq):
        try:
            self.executor.submit(self._run,extraction_id,use_groq)
        except RuntimeError:
            self.capacity.release()
            raise AppError(503,'processing_unavailable','The local worker is shutting down. Retry after restarting.') from None

    def ensure_active(self,extraction_id):
        with self.factory() as db:
            if db.scalar(select(DocumentExtraction.status).where(DocumentExtraction.id==extraction_id)) not in ACTIVE:
                raise ProcessingStopped()

    def agent_state(self,extraction_id,**values):
        with self.factory() as db:
            active=select(DocumentExtraction.id).where(DocumentExtraction.id==extraction_id,DocumentExtraction.status.in_(ACTIVE))
            db.execute(update(ResumeAgentRun).where(ResumeAgentRun.extraction_id.in_(active)).values(**values))
            db.commit()

    def stage(self,extraction_id,stage,**values):
        with self.factory() as db:
            changed=db.execute(update(DocumentExtraction).where(DocumentExtraction.id==extraction_id,DocumentExtraction.status.in_(ACTIVE)).values(status='processing',stage=stage,updated_at=now(),**values))
            if changed.rowcount!=1:
                raise ProcessingStopped()
            db.commit()

    def save_audit(self,extraction_id,data,pages=()):
        with self.factory() as db:
            extraction=db.get(DocumentExtraction,extraction_id)
            if not extraction or extraction.status not in ACTIVE:
                raise ProcessingStopped()
            audit=db.get(ResumeProcessingAudit,extraction_id)
            if not audit:
                audit=ResumeProcessingAudit(extraction_id=extraction_id,user_id=extraction.user_id)
                db.add(audit)
            audit.data=data
            for page in pages:
                db.add(ResumePageArtifact(id=page['id'],extraction_id=extraction_id,user_id=extraction.user_id,page=page['page'],content=page['content']))
            if pages:
                # Keep page blobs for only the two most recent runs of this source.
                older=select(DocumentExtraction.id).where(DocumentExtraction.document_id==extraction.document_id,
                    DocumentExtraction.run_version < extraction.run_version-1)
                db.execute(delete(ResumePageArtifact).where(ResumePageArtifact.extraction_id.in_(older)))
            db.commit()

    def _run(self,extraction_id,use_groq):
        started=time.monotonic()
        audit_data={}
        try:
            self.stage(extraction_id,'extracting_text')
            self.agent_state(extraction_id,status='processing',task='document_preparation',tasks=[{'name':'document_preparation','status':'processing'}])
            with self.factory() as db:
                extraction=db.get(DocumentExtraction,extraction_id)
                document=db.get(Document,extraction.document_id) if extraction else None
                if not document:
                    return
                content,filename,doc_id=document.content,document.filename,document.id
            with self.factory() as db:
                audit=db.get(ResumeProcessingAudit,extraction_id)
                consent=dict(audit.data.get('consent',{})) if audit else {}
            def local_progress(stage):
                self.ensure_active(extraction_id)
                if stage:
                    self.stage(extraction_id,stage)
            prepared=prepare_isolated(content,filename,doc_id,self.settings,local_progress)
            blocks,page_count,warnings=prepared['blocks'],prepared['page_count'],prepared['warnings']
            audit_data={'id':extraction_id,'document_id':doc_id,'pipeline_version':'connected-evidence-4','status':'extracting','stage':'local_preparation',
                'quality':prepared['quality'],'conversion':prepared.get('conversion'),
                'pages':[{k:v for k,v in page.items() if k!='content'} for page in prepared['pages']],
                'exact_text':prepared['exact_text'],'ocr':prepared['ocr'],'layout':prepared['layout'],'agents':[],
                'warnings':source_conflicts(blocks),'consent':consent,'other_sections':other_sections(blocks)}
            self.save_audit(extraction_id,audit_data,prepared['pages'])
            if not any(b['text'].strip() for b in blocks):
                raise AppError(422,'no_readable_text','No readable text was recovered, including OCR. Inspect the audit warnings and upload a clearer document or use manual entry.')
            if use_groq and consent.get('images'):
                if prepared['pages']:
                    local_progress('vision_processing')
                    self.agent_state(extraction_id,engine='crewai',status='processing',task='vision_layout',tasks=[{'name':'document_preparation','status':'completed'},{'name':'vision_layout','status':'processing'}])
                    try:
                        visual=analyse_pages(prepared['pages'],self.settings,self.key_manager,local_progress,blocks=blocks)
                        visual_blocks,visual_warnings=validate_layout(visual.layout,blocks,prepared['pages'][:self.settings.vision_max_pages])
                        audit_data['layout']+=visual_blocks
                        audit_data['warnings']+=visual_warnings
                        warnings.extend(visual.warnings+visual.layout.warnings)
                        if len(prepared['pages'])>self.settings.vision_max_pages:
                            warnings.append(f'Vision analysed only the first {self.settings.vision_max_pages} rendered pages; review the remaining pages locally.')
                        audit_data['agents'].append({'name':'vision_layout','status':'completed' if len(visual.completed_pages)==min(len(prepared['pages']),self.settings.vision_max_pages) else 'partial','model':visual.model,'method':'crewai_vision',
                            'result':{'layout_type':visual.layout.layout_type,'blocks':visual_blocks,'attempted_models':visual.attempted_models,'completed_pages':visual.completed_pages},'warnings':visual.warnings})
                    except ProviderUnavailable as exc:
                        warnings.append('Visual CrewAI fallback: '+exc.message)
                        audit_data['agents'].append({'name':'vision_layout','status':'failed','method':'crewai_vision','error_code':exc.code,'warnings':[exc.message]})
                else:
                    warnings.append('Vision was requested but no page images were available. Text-only extraction will be used.')
                    audit_data['agents'].append({'name':'vision_layout','status':'skipped','method':'crewai_vision','warnings':['No rendered page images.']})
            else:
                audit_data['agents'].append({'name':'vision_layout','status':'skipped','method':'local','warnings':['External image processing was not selected.']})
            self.save_audit(extraction_id,audit_data)
            from app.services.canonical_resume import layout_context
            blocks=layout_context(blocks,audit_data['layout'])
            self.stage(extraction_id,'identifying_fields',text_blocks=blocks,page_count=page_count)
            values,evidence,local_warnings=deterministic_extract(blocks)
            warnings+=local_warnings
            method,engine,run_status,error='deterministic_limited','local_parser','local_only',None
            tasks=[{'name':'document_preparation','status':'completed'}, {'name':'resume_intelligence','status':'skipped'}, {'name':'quality_validation','status':'pending'}]
            if use_groq:
                tasks[1]['status'] = 'pending'
                tasks.insert(2, {'name':'crew_quality','status':'pending'})
                self.agent_state(extraction_id,engine='crewai',task='crew_extraction',tasks=tasks)
                def progress(stage):
                    if stage is None:
                        self.ensure_active(extraction_id)
                        return
                    self.stage(extraction_id,stage)
                    if stage == 'crew_extraction':
                        tasks[1]['status'] = 'processing'
                    elif stage == 'crew_validation':
                        tasks[1]['status'] = 'completed'
                        tasks[2]['status'] = 'processing'
                    self.agent_state(extraction_id,task=stage,tasks=tasks)
                try:
                    enhanced,ai_evidence,rejected=resume_intelligence.enhance(blocks,self.settings,manager=self.key_manager,
                        file_type=Path(filename).suffix.lower().lstrip('.'),warnings=warnings,progress=progress,layout=audit_data['layout'])
                    for field in ('name','email','phone','location','career_objective'):
                        if values.get(field) and enhanced.get(field) and values[field]!=enhanced[field]:
                            audit_data['warnings'].append(ValidationWarning(code='conflicting_agent_value',field=field,severity='needs_review',
                                message='Local and agent readings differ. Both are retained; explicitly review the draft value.',sources=[
                                    {'method':'local','value':values[field],'evidence':evidence.get(field)},
                                    {'method':'crewai_text','value':enhanced[field],'evidence':ai_evidence.get(field)}]).model_dump())
                    agent_statuses=getattr(ai_evidence,'agent_statuses',{})
                    for name,result in getattr(ai_evidence,'agent_results',{}).items():
                        failures=result.get('failures',[])
                        audit_data['agents'].append({'name':name,'status':agent_statuses.get(name,'completed'),'model':self.settings.text_model,
                            'method':'crewai_text','result':result,'error_code':failures[0]['code'] if failures else None,
                            'warnings':[failure['message'] for failure in failures]})
                    values,evidence=merge_enhancement(values,evidence,enhanced,ai_evidence)
                    warnings.extend(getattr(ai_evidence,'warnings',[]))
                    warnings.extend(proof['warning'] for proof in ai_evidence.values() if proof.get('warning'))
                    method,engine,run_status='groq_enhanced','crewai','draft_ready'
                    tasks[1]={'name':'resume_intelligence','status':agent_statuses.get('extraction','completed')}
                    tasks[2]={'name':'crew_quality','status':agent_statuses.get('validation','completed')}
                    if any(status!='completed' for status in agent_statuses.values()):
                        run_status='partial'
                        error=getattr(ai_evidence,'error_code',None)
                        if error:tasks[2]['error_code']=error
                    if rejected:
                        warnings.append('Unsupported or inferred agent fields were withheld. Only source-linked suggestions are shown.')
                except ProviderUnavailable as exc:
                    error,run_status=exc.code,'fallback'
                    audit_data['agents'].append({'name':'text_intelligence','status':'failed','model':self.settings.text_model,'method':'crewai_text','error_code':exc.code,'warnings':[exc.message]})
                    warnings.append('CrewAI fallback: '+exc.message)
                    failed_index = 2 if tasks[1]['status'] == 'completed' else 1
                    tasks[failed_index] = {**tasks[failed_index], 'status':'failed', 'error_code':exc.code}
                    if failed_index == 1:
                        tasks[2]['status'] = 'skipped'
                    logger.warning('agent_fallback operation=%s code=%s',extraction_id,exc.code)
                except ValidationError:
                    error,run_status='crew_profile_limits','fallback'
                    warnings.append('CrewAI suggestions exceeded the combined profile limits. Your limited local extraction is available.')
                    tasks[1]={'name':'resume_intelligence','status':'failed','error_code':error}
                    tasks[2]['status']='skipped'
                    logger.warning('agent_fallback operation=%s code=%s',extraction_id,error)
            else:
                warnings.append('Local processing selected. CrewAI and external providers were not run.')
            if use_groq and consent.get('images'):
                visual_task=next(a for a in audit_data['agents'] if a['name']=='vision_layout')
                tasks.insert(1,{'name':'vision_layout','status':visual_task['status']})
                if visual_task['status']!='completed' and run_status=='draft_ready':
                    run_status='partial'
                    error=visual_task.get('error_code') or 'vision_incomplete'
            evidence=enrich_evidence(evidence,blocks,audit_data['layout'],audit_data['warnings'])
            from app.flows.resume_canonical import ResumeCanonicalFlow
            connected=ResumeCanonicalFlow({'id':extraction_id,'file_type':Path(filename).suffix.lower().lstrip('.'),
                'values':values,'evidence':evidence,'blocks':blocks,'exact_text':prepared['exact_text'],
                'quality':audit_data['quality'],'layout':audit_data['layout'],'warnings':audit_data['warnings']},
                self.settings,self.key_manager,local_progress,use_ai=use_groq).kickoff()
            values,evidence=connected.pop('values'),connected.pop('evidence')
            audit_data.update(connected)
            for issue in connected['canonical_validation']['issues']:
                if issue['severity']=='failure':warnings.append('Canonical validation: '+issue['message'])
            # Legacy extraction statuses and agent metadata remain compatible;
            # the canonical Flow has its own truthful task/model audit.

            self.stage(extraction_id,'quality_validation')
            quality=check_resume(values,evidence,blocks=blocks)
            warnings+=quality.warnings
            tasks[-1]={'name':'quality_validation','status':'completed','issue_count':len(quality.issues),'issues':[issue.model_dump() for issue in quality.issues]}
            audit_data['warnings'] += [ValidationWarning(code=issue.code,field=issue.path,message=issue.code.replace('_',' '),severity='needs_review').model_dump() for issue in quality.issues]
            audit_data['warnings'] += [ValidationWarning(code='processing_warning',message=warning).model_dump() for warning in dict.fromkeys(warnings)]
            audit_data['agents'].append({'name':'source_validation','status':'completed','method':'deterministic','result':{
                **quality.model_dump(),'grounding':source_check(values,evidence,blocks)}})
            audit_data['duration_ms']=round((time.monotonic()-started)*1000)
            self.save_audit(extraction_id,audit_data)
            self.agent_state(extraction_id,engine=engine,status=run_status,task='completed',tasks=tasks,
                warnings=list(dict.fromkeys(warnings)),error_code=error,completed_at=now())
            self.stage(extraction_id,'preparing_review')
            with self.factory() as db:
                db.execute(update(DocumentExtraction).where(DocumentExtraction.id==extraction_id,DocumentExtraction.status.in_(ACTIVE)).values(
                    status='awaiting_review',stage='completed',active_document=None,method=method,values=values,evidence=evidence,
                    warnings=list(dict.fromkeys(warnings)),updated_at=now(),completed_at=now()))
                db.commit()
        except ProcessingStopped:
            pass
        except Exception as exc:
            code=exc.code if isinstance(exc,AppError) else 'processing_failed'
            message=exc.message if isinstance(exc,AppError) else 'This document could not be processed. Retry or enter your profile manually.'
            logger.warning('document_processing_failed operation=%s code=%s',extraction_id,code)
            try:
                with self.factory() as db:
                    db.execute(update(DocumentExtraction).where(DocumentExtraction.id==extraction_id,DocumentExtraction.status.in_(ACTIVE)).values(
                        status='failed',stage='failed',active_document=None,error_code=code,error_message=message,updated_at=now(),completed_at=now()))
                    db.execute(update(ResumeAgentRun).where(ResumeAgentRun.extraction_id==extraction_id,ResumeAgentRun.status!='cancelled').values(
                        status='failed',error_code=code,completed_at=now()))
                    db.commit()
            except SQLAlchemyError:
                logger.error('processing_persistence_failure operation=%s',extraction_id)
        finally:
            self.capacity.release()

    def shutdown(self):
        self.executor.shutdown(wait=True,cancel_futures=False)
