"""Run the genuine CrewAI workflow in a bounded, private child process."""
from contextlib import redirect_stdout, redirect_stderr
import json
import logging
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import secrets
from tempfile import TemporaryDirectory
import time

from app.providers.groq import GroqKeyManager, GroqProvider, ProviderUnavailable
from app.schemas.agents import CrewExtractionResult, ResumeCrewInput,ResumeClaims,CompactCrewResult
from app.schemas.extraction import ProviderResume
from app.schemas.agents import ResumeQualityReport, CompactQualityReport, CrewTaskFailure
from app.services.groq_extraction import EnhancementEvidence, extraction_context, require_complete
from app.services.resume_presenter import proposal_from_structured
from app.services.resume_structure import anchored_ai_values


def _crew_child(input_data, settings, manager, owner, deadline, scratch, output):
    # Every framework cache/task-output file is ephemeral and private. No prompts,
    # outputs, headers, keys, provider bodies or exception strings reach stdout.
    logging.disable(logging.CRITICAL)
    extract_llm = quality_llm = None
    os.environ['CREWAI_STORAGE_DIR']=scratch
    os.environ['OTEL_SDK_DISABLED']='true'
    os.environ['CREWAI_DISABLE_TELEMETRY']='true'
    os.environ['CREWAI_TRACING_ENABLED']='false'
    with open(os.devnull,'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        try:
            from app.agents.resume import RotatingGroqLLM
            from app.crews.resume import build_resume_crew
            prepared=ResumeCrewInput.model_validate(input_data)
            provider=GroqProvider(manager,settings)
            if settings.text_model not in provider.available_models(owner=owner,deadline=deadline):
                raise ProviderUnavailable('groq_text_model_unavailable','The configured text model is unavailable. Limited local extraction remains available.')
            extract_llm=RotatingGroqLLM(provider,ResumeClaims if prepared.compact_output else ProviderResume,owner,deadline,'resume_extraction')
            quality_llm=RotatingGroqLLM(provider,CompactQualityReport if prepared.compact_output else ResumeQualityReport,owner,deadline,'resume_quality')
            provider.on_wait=lambda:output.put(('stage','provider_rate_wait'))
            crew=build_resume_crew(extract_llm,quality_llm,lambda stage:output.put(('stage',stage)),compact=prepared.compact_output)
            if prepared.compact_output:
                def checkpoint(task_output):
                    claims=ResumeClaims.model_validate(task_output.pydantic)
                    require_complete(claims)
                    output.put(('checkpoint',claims.model_dump()))
                crew.tasks[0].callback=checkpoint
            output.put(('stage','crew_extraction'))
            context=prepared.context if prepared.compact_output else extraction_context(prepared.blocks)
            context['document_type']=prepared.file_type
            if not prepared.compact_output:
                context['warnings']=prepared.warnings
                context['visual_layout']=prepared.layout
            crew.kickoff(inputs={'prepared_document':json.dumps(context,ensure_ascii=False)})
            extraction=crew.tasks[0].output.pydantic
            quality=crew.tasks[1].output.pydantic
            if not isinstance(extraction,ResumeClaims if prepared.compact_output else ProviderResume) or not isinstance(quality,ResumeQualityReport):
                raise ProviderUnavailable('crew_invalid_output','CrewAI returned invalid structured tasks. Limited local extraction is available.')
            require_complete(extraction); require_complete(quality)
            result_type=CompactCrewResult if prepared.compact_output else CrewExtractionResult
            output.put(('result',result_type(extraction=extraction,quality=quality).model_dump()))
        except ProviderUnavailable as exc:
            output.put(('error',{'code':exc.code,'message':exc.message}))
        except Exception:
            failure = next((llm._last_error for llm in (extract_llm, quality_llm) if llm and llm._last_error), None)
            output.put(('error', {'code': failure.code, 'message': failure.message} if failure else {
                'code':'crew_failed','message':'The Resume Intelligence Crew failed or returned invalid output. Limited local extraction is available.'}))


def run_crew(prepared, settings, manager, *, progress=lambda stage: None, child_target=_crew_child, result_model=CrewExtractionResult, deadline=None):
    if not manager.configured:
        raise ProviderUnavailable('groq_not_configured','CrewAI could not run because Groq is not configured. Limited local extraction is available.')
    context=multiprocessing.get_context('spawn')
    root=settings.document_storage_path
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    owner=secrets.randbits(62) or 1
    deadline=deadline or time.monotonic()+settings.crew_timeout_seconds
    output=context.Queue()
    process=None
    checkpoint=None
    def failed(code,message):
        if checkpoint is not None and result_model is CompactCrewResult:
            return CompactCrewResult(extraction=checkpoint,quality_error=CrewTaskFailure(code=code,message=message))
        raise ProviderUnavailable(code,message)
    try:
        with TemporaryDirectory(prefix='crew-',dir=root) as scratch:
            process=context.Process(target=child_target,args=(prepared.model_dump(),settings,manager,owner,deadline,str(Path(scratch).resolve()),output),daemon=False)
            process.start()
            try:
                while time.monotonic()<deadline:
                    # Parent callback checks cancellation/ownership before every poll.
                    progress(None)
                    try:
                        kind,data=output.get(timeout=min(.2,max(.01,deadline-time.monotonic())))
                    except Empty:
                        if not process.is_alive():
                            return failed('crew_failed','The Resume Intelligence Crew stopped unexpectedly. Review the retained draft.')
                        continue
                    if kind=='stage':
                        progress(data)
                    elif kind=='error':
                        return failed(data['code'],data['message'])
                    elif kind=='checkpoint' and result_model is CompactCrewResult:
                        checkpoint=ResumeClaims.model_validate(data)
                        require_complete(checkpoint)
                    elif kind=='result':
                        return result_model.model_validate(data)
                return failed('crew_timeout','The Resume Intelligence Crew timed out. Review the retained draft or retry when ready.')
            finally:
                process.join(timeout=.3)
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill();process.join(timeout=2)
    finally:
        manager.release_owner(owner)
        output.close()
        output.cancel_join_thread()


def enhance(blocks,settings,*,manager=None,file_type='txt',warnings=(),progress=lambda stage:None,layout=()):
    from app.schemas.extraction import CandidateDraft
    from app.services.crew_payload import compact_context,source_batches,proposal_from_claims
    from app.services.resume_structure import merge_enhancement
    manager=manager or GroqKeyManager(settings.groq_keys,settings.groq_key_cooldown_seconds)
    deadline=time.monotonic()+settings.crew_timeout_seconds
    batches=source_batches(blocks,settings.groq_source_batch_chars)
    values,evidence=CandidateDraft().model_dump(),{}
    messages=[];withheld=0;outputs=[];validations=[];completed=0;quality_failures=[];incomplete=False;attempts=0;source_decisions=[];format_withheld=0
    pending=[(batch,0) for batch in batches[:settings.groq_max_batches]]
    while pending:
        batch,depth=pending.pop(0)
        progress(None)
        if attempts>=settings.groq_max_batches*2:
            if not completed:
                raise ProviderUnavailable('crew_batch_limit','The text extraction retry limit was reached. Review the local draft or retry.')
            messages.append('AI retry limit reached. Remaining source text is retained for local/manual review.')
            incomplete=True
            break
        attempts+=1
        if time.monotonic()>=deadline:
            if not completed:
                raise ProviderUnavailable('crew_timeout','The text crew time budget expired. Limited local extraction remains available.')
            messages.append('AI processing reached its time budget. Remaining source text stays available in the local draft and audit.')
            incomplete=True
            break
        prepared=ResumeCrewInput(file_type=file_type,blocks=batch,compact_output=True,
            context=compact_context(batch,layout,file_type,source_blocks=blocks))
        try:
            result=run_crew(prepared,settings,manager,progress=progress,result_model=CompactCrewResult,deadline=deadline)
        except ProviderUnavailable as exc:
            # Split by this batch's actual size, so already-small requests can
            # shrink too. Two levels, a shared deadline and a total call cap.
            if exc.code in {'groq_input_limit','groq_invalid_output','groq_output_truncated'} and depth<2:
                smaller=source_batches(batch,max(300,sum(len(b['text'])+32 for b in batch)//2))
                if len(smaller)>1:
                    pending=[(piece,depth+1) for piece in smaller]+pending
                    messages.append('The provider could not complete structured extraction; smaller source batches were used. Source IDs and full audit text were preserved.')
                    continue
            if not completed:raise
            messages.append('Some AI batches could not complete ('+exc.code+'). Their source text remains in the local draft and audit.')
            incomplete=True
            break
        proposal,rejected=proposal_from_claims(result.extraction,blocks)
        accepted,proof,unsupported=anchored_ai_values(proposal,blocks,decisions=source_decisions)
        format_withheld+=rejected
        values,evidence=merge_enhancement(values,evidence,accepted,proof)
        withheld+=rejected+unsupported;completed+=1
        outputs.append(result.extraction.model_dump())
        if result.quality is not None:
            validations.append(result.quality.model_dump())
            messages.extend(result.quality.warnings)
            if result.quality.issues:messages.append('CrewAI quality review flagged source claims for manual review.')
        else:
            quality_failures.append(result.quality_error.model_dump())
            messages.append('AI extraction completed, but its quality review could not complete ('+result.quality_error.code+'). Source checks still run locally; manually review every proposed fact.')
    if len(batches)>settings.groq_max_batches:
        messages.append('AI batch limit reached. Remaining text is retained in the local draft and original audit.')
        incomplete=True
    audit_evidence=EnhancementEvidence(evidence,list(dict.fromkeys(messages)))
    audit_evidence.agent_results={'extraction':{'format':'source_claims','batches':outputs,'completed_batches':completed,'source_batches':max(len(batches),completed+len(pending)+(1 if incomplete else 0)),'original_source_batches':len(batches),'attempted_batches':attempts,
        'grounding':{'accepted_claims':sum(d['status']=='accepted' for d in source_decisions),'withheld_claims':withheld,'format_withheld':format_withheld,'decisions':source_decisions}},
        'validation':{'batches':validations,'failures':quality_failures}}
    audit_evidence.agent_statuses={'extraction':'partial' if incomplete else 'completed',
        'validation':('partial' if validations else 'failed') if quality_failures else ('partial' if validations else 'skipped') if incomplete else 'completed'}
    audit_evidence.error_code=quality_failures[0]['code'] if quality_failures else 'crew_incomplete' if incomplete else None
    return values,audit_evidence,withheld
