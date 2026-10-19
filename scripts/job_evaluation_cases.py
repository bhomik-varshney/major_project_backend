"""Export deterministic matching-evaluation inputs without implementing a matcher."""
import json

from app.providers.job_dataset import generate_jobs
from app.providers.job_evaluation import evaluation_cases

if __name__ == '__main__':
    print(json.dumps(evaluation_cases(generate_jobs()), indent=2, sort_keys=True))
