"""Configure privacy BEFORE importing CrewAI; no model/telemetry defaults."""
import os

os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['CREWAI_TELEMETRY_DISABLED'] = 'true'
os.environ['CREWAI_TRACING_ENABLED'] = 'false'
os.environ['DO_NOT_TRACK'] = 'true'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"

from crewai import Agent, BaseLLM, Crew, Process, Task  # noqa: E402
