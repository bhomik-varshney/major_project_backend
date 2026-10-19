"""Future provider boundaries. No AI, retrieval, or application execution yet.

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
