"""Atomically reseed only the synthetic JSON fixture; never open a workspace DB."""
import argparse
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.providers.job_dataset import ANCHOR_PATH, DATA_PATH, DEFAULT_SEED, dataset_bytes, generate_jobs


def reseed_jobs(path=DATA_PATH, seed=DEFAULT_SEED):
    path = Path(path)
    if path.suffix != '.json' or path.resolve() == ANCHOR_PATH.resolve():
        raise ValueError('Reseeding requires a dedicated job fixture JSON path.')
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (ValueError, OSError):
            # A corrupted dedicated jobs.json can be repaired; arbitrary JSON is protected.
            if path.name != 'jobs.json':
                raise ValueError('Refusing to overwrite an unrecognized fixture.') from None
            existing = []
        if not isinstance(existing, list) or any(not isinstance(row, dict) or row.get('source') != 'seeded_mock' or not str(row.get('id','')).startswith('demo-') for row in existing):
            raise ValueError('Refusing to overwrite non-job data.')
    rows = generate_jobs(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.jobs-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(dataset_bytes(rows)); file.flush(); os.fsync(file.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DATA_PATH)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    try:
        count = reseed_jobs(args.output, args.seed)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Job reseeding failed: {type(error).__name__}. No workspace database was opened.\n')
    print(f'Reseeded {count} synthetic jobs. User profiles, documents and processing runs were not accessed.')


if __name__ == '__main__':
    main()
