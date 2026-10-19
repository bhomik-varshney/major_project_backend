from concurrent.futures import ThreadPoolExecutor
import logging
from threading import BoundedSemaphore
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError
from app.db.models import Document, DocumentExtraction, ResumeAgentRun, now
from app.orchestration import resume as resume_intelligence
from app.providers.groq import GroqKeyManager, ProviderUnavailable
from app.services.resume_quality import check_resume
from app.services.resume_structure import deterministic_extract, merge_enhancement
from app.services.resume_text import extract_text

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

    def _run(self,extraction_id,use_groq):
        try:
            self.stage(extraction_id,'extracting_text')
            self.agent_state(extraction_id,status='processing',task='document_preparation',tasks=[{'name':'document_preparation','status':'processing'}])
            with self.factory() as db:
                extraction=db.get(DocumentExtraction,extraction_id)
                document=db.get(Document,extraction.document_id) if extraction else None
                if not document:
                    return
                content,filename,doc_id=document.content,document.filename,document.id
            blocks,page_count,warnings=extract_text(content,filename,doc_id,self.settings)
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
                        file_type=Path(filename).suffix.lower().lstrip('.'),warnings=warnings,progress=progress)
                    values,evidence=merge_enhancement(values,evidence,enhanced,ai_evidence)
                    warnings.extend(getattr(ai_evidence,'warnings',[]))
                    warnings.extend(proof['warning'] for proof in ai_evidence.values() if proof.get('warning'))
                    method,engine,run_status='groq_enhanced','crewai','draft_ready'
                    tasks[1]={'name':'resume_intelligence','status':'completed'}
                    tasks[2]={'name':'crew_quality','status':'completed'}
                    if rejected:
                        warnings.append('Unsupported or inferred agent fields were withheld. Only source-linked suggestions are shown.')
                except ProviderUnavailable as exc:
                    error,run_status=exc.code,'fallback'
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
            self.stage(extraction_id,'quality_validation')
            quality=check_resume(values,evidence)
            warnings+=quality.warnings
            tasks[-1]={'name':'quality_validation','status':'completed','issue_count':len(quality.issues),'issues':[issue.model_dump() for issue in quality.issues]}
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
