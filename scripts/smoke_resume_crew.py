"""Optional provider smoke check. Sends only this synthetic resume, never uploads."""
import argparse
import time

from app.core.config import Settings
from app.orchestration.resume import enhance
from app.providers.groq import ProviderUnavailable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Explicitly allow Groq requests using backend environment credentials')
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to send the built-in synthetic fixture to Groq.')
    lines = ['Synthetic Student', 'Email: synthetic@example.com', 'Career Objective:',
        'Seeking a backend internship.', 'Skills:', 'Python, SQL', 'Projects:',
        'Campus Tool', 'Technologies: Python', '- Built a student directory.']
    blocks = [{'block_id':i,'page':None,'paragraph':i,'text':line} for i,line in enumerate(lines,1)]
    started = time.monotonic()
    try:
        values, evidence, withheld = enhance(blocks, Settings())
        print({'engine':'crewai', 'tasks':['resume_intelligence','resume_quality'],
            'source_supported_fields':len(evidence), 'withheld':withheld,
            'skills':len(values.get('skills',[])), 'elapsed_seconds':round(time.monotonic()-started,1)})
    except ProviderUnavailable as error:
        print({'engine':'local_fallback', 'code':error.code, 'elapsed_seconds':round(time.monotonic()-started,1)})
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
