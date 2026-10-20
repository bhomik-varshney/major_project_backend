"""Validate the generated corpus, labeling intended edge cases separately from corruption."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from app.providers.job_catalog import ROLES
from app.providers.job_dataset import DATA_PATH, DEFAULT_SEED, REFERENCE_DATE, dataset_bytes, generate_jobs
from app.schemas.jobs import Job
from app.services.jobs import availability


SECRET_PATTERNS = (
    re.compile(r'gsk_[A-Za-z0-9]{20,}'), re.compile(r'sk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}'), re.compile(r'github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(r'AKIA[A-Z0-9]{16}'), re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'(?i)(?:api[_ -]?key|secret|password|access[_ -]?token)\s*[=:]\s*["\']?[A-Za-z0-9_/-]{16,}'),
)


def validate_dataset(path=DATA_PATH, *, seed=DEFAULT_SEED, reproducible=True):
    path = Path(path)
    content = path.read_bytes()
    rows = json.loads(content)
    if not isinstance(rows, list) or len(rows) != 10000:
        raise ValueError('The dataset must contain exactly 10000 records.')
    # Validate twice from independent loads: no partially written or unstable fixture.
    if json.loads(path.read_bytes()) != rows:
        raise ValueError('Dataset changed during validation.')
    jobs, ids, identities = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(Job.model_fields)-set(row):
            raise ValueError('A record lacks required dataset fields.')
        job = Job.model_validate(row)
        if job.id in ids:
            raise ValueError('Duplicate ID.')
        ids.add(job.id)
        if job.category not in {role.category for role in ROLES}:
            raise ValueError('Unknown career category.')
        if job.metadata.seed != seed:
            raise ValueError('Unexpected generator seed.')
        raw = json.dumps(row, sort_keys=True)
        if any(pattern.search(raw) for pattern in SECRET_PATTERNS):
            raise ValueError('Potential credential found; record content is intentionally omitted.')
        business = {key:value for key,value in row.items() if key not in {'id','source_url','application_url','metadata'}}
        fingerprint = hashlib.sha256(json.dumps(business,sort_keys=True).encode()).hexdigest()
        if fingerprint in identities:
            raise ValueError('Unintentionally identical records.')
        identities.add(fingerprint)
        tags = set(job.metadata.edge_cases)
        if not job.description and 'empty_description' not in tags:
            raise ValueError('Unlabeled empty description.')
        if (not job.department or job.metadata.company_size is None) and 'incomplete_metadata' not in tags:
            raise ValueError('Unlabeled incomplete metadata.')
        from app.services.job_normalization import search_text
        if not job.subcategory or job.title_tokens!=search_text(job.title).split() or job.category_tokens!=search_text(job.category.replace('_',' ')).split():
            raise ValueError('Invalid normalized title/category metadata.')
        if job.embedding_metadata.get('text_sha256')!=hashlib.sha256(job.searchable_text.encode()).hexdigest():
            raise ValueError('Invalid embedding content fingerprint.')
        jobs.append(job)
    categories = Counter(job.category for job in jobs)
    if not {role.category for role in ROLES}.issubset(categories):
        raise ValueError('Missing career domains.')
    edges = Counter(tag for job in jobs for tag in job.metadata.edge_cases)
    required_edges = {'empty_description','missing_salary','no_preferred_skills','overlapping_skills','unrelated_skill','duplicate_like','expired','future_dated','incomplete_metadata','conflicting_requirements'}
    if not required_edges.issubset(edges):
        raise ValueError('Missing controlled evaluation scenarios.')
    from app.providers.job_dataset import DATASET_VERSION
    if any(job.metadata.dataset_version != DATASET_VERSION or job.metadata.reference_date != REFERENCE_DATE for job in jobs):
        raise ValueError('Invalid version or reference date.')
    if reproducible and content != dataset_bytes(generate_jobs(seed)):
        raise ValueError('Fixture differs from deterministic generator output; reseed it.')
    return dict(records=len(jobs), categories=dict(sorted(categories.items())),
        experience_levels=dict(sorted(Counter(job.experience_level for job in jobs).items())),
        employment_types=dict(sorted(Counter(job.employment_type for job in jobs).items())),
        work_modes=dict(sorted(Counter(job.work_mode for job in jobs).items())),
        availability=dict(sorted(Counter(availability(job,REFERENCE_DATE) for job in jobs).items())),
        edge_cases=dict(sorted(edges.items())), reference_date=REFERENCE_DATE.isoformat(),
        seed=seed, sha256=hashlib.sha256(content).hexdigest(), reproducible=reproducible)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=DATA_PATH)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--allow-custom', action='store_true', help='Validate schema/quality without requiring byte-for-byte generator equality')
    args = parser.parse_args()
    try:
        result = validate_dataset(args.path, seed=args.seed, reproducible=not args.allow_custom)
    except (ValueError, OSError, TypeError):
        parser.exit(1, 'Job validation failed. Record contents are omitted to protect against accidental credentials.\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
