"""Exercise the actual CrewAI executor with scripted LLM output, without network."""
import json

import pytest
from pydantic import PrivateAttr

from app.schemas.agents import ResumeQualityReport
from app.schemas.extraction import ProviderResume


@pytest.fixture
def crew_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('CREWAI_STORAGE_DIR', str(tmp_path / 'crew-storage'))
    from app.agents.runtime import BaseLLM

    class ScriptedLLM(BaseLLM):
        _answer = PrivateAttr()
        _prompts = PrivateAttr(default_factory=list)

        def __init__(self, answer):
            super().__init__(model='scripted/offline', temperature=0)
            self._answer = answer

        def call(self, messages, **kwargs):
            self._prompts.append(messages)
            return self._answer

        def supports_function_calling(self):
            return False

        def supports_stop_words(self):
            return False

    return ScriptedLLM


def test_actual_crew_executes_two_typed_dependent_tasks(crew_runtime):
    from app.agents.runtime import Crew, Process
    from app.crews.resume import build_resume_crew
    raw = ProviderResume().model_dump()
    raw['personal_info']['name'].update(value='Synthetic Student', evidence={'block_id':1,'quote':'Synthetic Student'}, confidence='high', classification='directly_found')
    first = crew_runtime(json.dumps(raw))
    second = crew_runtime(ResumeQualityReport().model_dump_json())
    crew = build_resume_crew(first, second)
    assert isinstance(crew, Crew) and crew.process == Process.sequential
    assert len(crew.agents) == 2 and len(crew.tasks) == 2
    assert crew.tasks[1].context == [crew.tasks[0]]
    assert all(not a.tools and not a.allow_delegation for a in crew.agents)
    crew.kickoff(inputs={'prepared_document':json.dumps({'blocks':[{'id':1,'text':'Synthetic Student'}]})})
    assert isinstance(crew.tasks[0].output.pydantic, ProviderResume)
    assert isinstance(crew.tasks[1].output.pydantic, ResumeQualityReport)
    assert crew.tasks[0].output.pydantic.personal_info.name.value == 'Synthetic Student'
    assert len(first._prompts) == len(second._prompts) == 1
    assert 'Synthetic Student' in str(second._prompts)


@pytest.mark.parametrize('raw', ['not json', '{}', '{"skills":["invented"]}'])
def test_guardrail_rejects_invalid_or_incomplete_output(crew_runtime, raw):
    from app.tasks.resume import validate_extraction
    from types import SimpleNamespace
    assert validate_extraction(SimpleNamespace(raw=raw))[0] is False


def test_custom_rotating_llm_receives_real_crew_prompts_and_rejects_invalid_json(crew_runtime):
    from types import SimpleNamespace
    import time
    from app.agents.resume import RotatingGroqLLM
    from app.crews.resume import build_resume_crew
    from app.providers.groq import ProviderUnavailable
    calls = []
    class Transport:
        settings = SimpleNamespace(groq_model='offline/synthetic')
        def complete(self, messages, schema, **kwargs):
            calls.append((messages, schema, kwargs))
            return ProviderResume().model_dump_json() if kwargs['schema_name'] == 'resume_extraction' else ResumeQualityReport().model_dump_json()
    first = RotatingGroqLLM(Transport(), ProviderResume, 1, time.monotonic()+30, 'resume_extraction')
    second = RotatingGroqLLM(Transport(), ResumeQualityReport, 1, time.monotonic()+30, 'resume_quality')
    stages = []
    crew = build_resume_crew(first, second, stages.append)
    crew.kickoff(inputs={'prepared_document':'Synthetic source document'})
    assert len(calls) == 2 and stages == ['crew_validation']
    assert all('Synthetic source document' in str(call[0]) for call in calls)
    # Native structured response avoids CrewAI inlining ~114KB of repeated schema.
    assert all(sum(len(m['content']) for m in call[0]) < 10000 for call in calls)
    assert all(call[1]['additionalProperties'] is False for call in calls)
    with pytest.raises(ProviderUnavailable) as error:
        first.call('Do it again')
    assert error.value.code == 'crew_call_limit'
    class BadTransport(Transport):
        def complete(self, *args, **kwargs):
            return '{}'
    with pytest.raises(ProviderUnavailable) as error:
        RotatingGroqLLM(BadTransport(), ProviderResume, 1, time.monotonic()+30, 'resume_extraction').call('untrusted text')
    assert error.value.code == 'groq_invalid_output'


def test_compact_native_schema_preserves_all_model_fields():
    from app.services.groq_extraction import strict_schema
    compact = strict_schema(ProviderResume)
    original = ProviderResume.model_json_schema()
    def compare(before, after):
        if isinstance(before, dict):
            if 'properties' in before:
                assert set(after['properties']) == set(before['properties'])
                assert set(after['required']) == set(before['properties'])
                assert after['additionalProperties'] is False
            for key, value in before.items():
                if key in {'title', 'description', 'default', 'required', 'additionalProperties'}:
                    continue
                if key in {'properties', '$defs'}:
                    for name, child in value.items():
                        compare(child, after[key][name])
                else:
                    compare(value, after[key])
        elif isinstance(before, list):
            for a, b in zip(before, after):
                compare(a, b)
    compare(original, compact)
    assert 'description' in compact['$defs']['ProviderProject']['properties']
    assert 'description' in compact['$defs']['ProviderExperience']['properties']
