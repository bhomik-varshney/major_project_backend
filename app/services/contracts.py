"""Unimplemented future service sketches, not executable agents.

Resume intelligence is implemented in app.orchestration.resume. The implemented
seeded-job provider interface is app.providers.jobs.JobProvider. These sketches
are reserved for later asynchronous career workflows.

Implementations must return validated evidence-bearing data, preserve owner and
input revisions, and expose provider failures rather than inventing results.
"""
from typing import Protocol


class DocumentExtractor(Protocol):
    async def extract(self, *, owner_id: str, document_id: str, profile_revision: int) -> dict: ...


class JobSource(Protocol):
    async def search(self, *, criteria: dict, operation_id: str) -> dict: ...


class MatchingService(Protocol):
    async def rank(self, *, profile_snapshot: dict, job_snapshot: list[dict], configuration: dict) -> dict: ...


class ApplicationDraftService(Protocol):
    async def prepare(self, *, verified_profile: dict, job_snapshot: dict, document_type: str) -> dict: ...


class CareerOrchestrator(Protocol):
    async def run(self, *, owner_id: str, goal: str, profile_revision: int, operation_id: str) -> dict: ...
