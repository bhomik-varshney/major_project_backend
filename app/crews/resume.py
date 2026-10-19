from app.agents.runtime import Crew, Process
from app.agents.resume import resume_agent
from app.tasks.resume import resume_tasks


def build_resume_crew(extraction_llm, quality_llm, progress=lambda stage: None):
    if hasattr(quality_llm, 'set_progress'):
        quality_llm.set_progress(progress)
    extractor=resume_agent(extraction_llm)
    validator=resume_agent(quality_llm,quality=True)
    extraction,quality=resume_tasks(extractor,validator)
    crew=Crew(name='Resume Intelligence Crew',agents=[extractor,validator],tasks=[extraction,quality],
        process=Process.sequential, verbose=False, memory=False, cache=False, planning=False,
        tracing=False, checkpoint=False, share_crew=False, output_log_file=False)
    return crew
