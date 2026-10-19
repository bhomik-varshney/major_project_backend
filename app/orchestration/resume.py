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
from app.schemas.agents import CrewExtractionResult, ResumeCrewInput
from app.schemas.extraction import ProviderResume
from app.schemas.agents import ResumeQualityReport
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
            extract_llm=RotatingGroqLLM(provider,ProviderResume,owner,deadline,'resume_extraction')
            quality_llm=RotatingGroqLLM(provider,ResumeQualityReport,owner,deadline,'resume_quality')
            crew=build_resume_crew(extract_llm,quality_llm,lambda stage:output.put(('stage',stage)))
            output.put(('stage','crew_extraction'))
            context=extraction_context(prepared.blocks)
            context['document_type']=prepared.file_type
            context['warnings']=prepared.warnings
            crew.kickoff(inputs={'prepared_document':json.dumps(context,ensure_ascii=False)})
            extraction=crew.tasks[0].output.pydantic
            quality=crew.tasks[1].output.pydantic
            if not isinstance(extraction,ProviderResume) or not isinstance(quality,ResumeQualityReport):
                raise ProviderUnavailable('crew_invalid_output','CrewAI returned invalid structured tasks. Limited local extraction is available.')
            require_complete(extraction); require_complete(quality)
            output.put(('result',CrewExtractionResult(extraction=extraction,quality=quality).model_dump()))
        except ProviderUnavailable as exc:
            output.put(('error',{'code':exc.code,'message':exc.message}))
        except Exception:
            failure = next((llm._last_error for llm in (extract_llm, quality_llm) if llm and llm._last_error), None)
            output.put(('error', {'code': failure.code, 'message': failure.message} if failure else {
                'code':'crew_failed','message':'The Resume Intelligence Crew failed or returned invalid output. Limited local extraction is available.'}))


def run_crew(prepared, settings, manager, *, progress=lambda stage: None, child_target=_crew_child):
    if not manager.configured:
        raise ProviderUnavailable('groq_not_configured','CrewAI could not run because Groq is not configured. Limited local extraction is available.')
    context=multiprocessing.get_context('spawn')
    root=settings.document_storage_path
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    owner=secrets.randbits(62) or 1
    deadline=time.monotonic()+settings.crew_timeout_seconds
    output=context.Queue()
    process=None
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
                            raise ProviderUnavailable('crew_failed','The Resume Intelligence Crew stopped unexpectedly. Limited local extraction is available.')
                        continue
                    if kind=='stage':
                        progress(data)
                    elif kind=='error':
                        raise ProviderUnavailable(data['code'],data['message'])
                    elif kind=='result':
                        return CrewExtractionResult.model_validate(data)
                raise ProviderUnavailable('crew_timeout','The Resume Intelligence Crew timed out. Limited local extraction is available; retry when ready.')
            finally:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill();process.join(timeout=2)
    finally:
        manager.release_owner(owner)
        output.close()
        output.cancel_join_thread()


def enhance(blocks,settings,*,manager=None,file_type='txt',warnings=(),progress=lambda stage:None):
    manager=manager or GroqKeyManager(settings.groq_keys,settings.groq_key_cooldown_seconds)
    result=run_crew(ResumeCrewInput(file_type=file_type,blocks=blocks,warnings=list(warnings)),settings,manager,progress=progress)
    values,evidence,withheld=anchored_ai_values(proposal_from_structured(result.extraction),blocks)
    messages=list(result.quality.warnings)
    if result.quality.issues:
        messages.append('CrewAI quality review flagged fields for manual confirmation; source validation remains authoritative.')
    if result.extraction.warnings:
        messages.append('CrewAI extraction reported uncertainty. Review the original source evidence.')
    return values,EnhancementEvidence(evidence,messages),withheld
