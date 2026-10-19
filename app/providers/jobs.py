"""Read-only, cached provider boundary. No external requests or workspace writes."""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import ValidationError

from app.core.errors import AppError
from app.providers.job_dataset import DATA_PATH, DATASET_VERSION, REFERENCE_DATE
from app.schemas.jobs import Job


@dataclass(frozen=True)
class JobSnapshot:
    jobs: tuple[Job, ...]
    version: str
    warnings: tuple[str, ...] = ()
    reference_date: date = REFERENCE_DATE


class JobProvider(Protocol):
    def snapshot(self) -> JobSnapshot: ...


class SeededJobProvider:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_PATH
        self._lock = RLock()
        self._fingerprint = None
        self._snapshot = None

    def reset(self):
        """Invalidate this provider's cache only. Reseeding is a separate safe CLI."""
        with self._lock:
            self._snapshot = self._fingerprint = None

    def snapshot(self):
        with self._lock:
            try:
                stat = self.path.stat()
                fingerprint = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                if self._snapshot is not None and fingerprint == self._fingerprint:
                    return self._snapshot
                if stat.st_size > 12 * 1024 * 1024:
                    raise ValueError()
                raw = json.loads(self.path.read_text())
                if not isinstance(raw, list) or len(raw) > 1000:
                    raise ValueError()
            except (OSError, ValueError):
                raise AppError(503, 'jobs_unavailable', 'The demonstration job dataset is unavailable. Try again shortly.') from None
            records, seen, skipped = [], set(), 0
            for row in raw:
                try:
                    job = Job.model_validate(row)
                    if job.id in seen or job.metadata.dataset_version != DATASET_VERSION or job.metadata.reference_date != REFERENCE_DATE:
                        raise ValueError()
                    seen.add(job.id)
                    records.append(job)
                except (ValidationError, ValueError, TypeError):
                    skipped += 1
            warnings = (f'{skipped} malformed or duplicate demonstration records were excluded.',) if skipped else ()
            snapshot = JobSnapshot(tuple(sorted(records, key=lambda j: j.id)), DATASET_VERSION, warnings)
            self._snapshot, self._fingerprint = snapshot, fingerprint
            return snapshot
