"""Versioned, clock-independent synthetic corpus generation. No workspace DB access."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

from app.providers.job_catalog import ROLES
from app.schemas.jobs import Job
from app.services.job_normalization import normalized_skills

DATASET_VERSION = '2026-10-demo-v2'
DEFAULT_SEED = 20261019
REFERENCE_DATE = date(2026, 10, 19)
DATA_PATH = Path(__file__).parent / 'data' / 'jobs.json'
ANCHOR_PATH = Path(__file__).parent / 'data' / 'job_anchors.json'

# Hash-based choices avoid PRNG/platform/version state and Python's randomized hash().
def number(seed, index, field):
    return int.from_bytes(hashlib.sha256(f'{seed}:{index}:{field}'.encode()).digest()[:8], 'big')


def choose(values, seed, index, field):
    return values[number(seed, index, field) % len(values)]


PLACES = (
    ('Bengaluru', 'India', 'INR'), ('Noida', 'India', 'INR'),
    ('Hyderabad', 'India', 'INR'), ('Pune', 'India', 'INR'),
    ('Chennai', 'India', 'INR'), ('Delhi', 'India', 'INR'),
    ('Mumbai', 'India', 'INR'), ('Jaipur', 'India', 'INR'),
    ('Ahmedabad', 'India', 'INR'), ('Kochi', 'India', 'INR'),
    ('Indore', 'India', 'INR'), ('Lucknow', 'India', 'INR'),
    ('London', 'United Kingdom', 'GBP'), ('Berlin', 'Germany', 'EUR'),
    ('Toronto', 'Canada', 'CAD'), ('Singapore', 'Singapore', 'SGD'),
    ('Austin', 'United States', 'USD'),
)
ADJECTIVES = ('Cedar', 'River', 'Lantern', 'Meadow', 'Orbit', 'Harbor', 'Spruce', 'Summit', 'Maple', 'Willow', 'Amber', 'Silver', 'Coral', 'Juniper', 'Pebble', 'Cobalt', 'Birch', 'Saffron', 'Pine', 'Terra')
SUFFIXES = ('Systems', 'Labs', 'Works', 'Digital', 'Technologies', 'Solutions', 'Research', 'Studio', 'Networks', 'Analytics')
LEVELS = ('student',) * 25 + ('fresher',) * 20 + ('graduate',) * 18 + ('entry_level',) * 20 + ('junior',) * 14 + ('limited_experience',) * 3


def metadata(category, country, seed, index):
    return dict(dataset_version=DATASET_VERSION, country=country, category=category,
        is_demo=True, company_size=choose(('small', 'medium', 'large'), seed, index, 'size'),
        edge_cases=[], duplicate_group=None, seed=seed, reference_date=REFERENCE_DATE.isoformat())


def anchors(seed):
    raw = json.loads(ANCHOR_PATH.read_text())
    categories = ['software_engineering', 'backend', 'frontend', 'data_analysis', 'machine_learning', 'full_stack', 'test_automation', 'devops', 'backend', 'web']
    result = []
    for i, (row, category) in enumerate(zip(raw, categories)):
        role = next(role for role in ROLES if role.category == category)
        row.update(industry=role.industry, department=role.department, country='India',
            work_mode='remote' if row['remote'] else 'onsite', experience_years_min=0,
            experience_years_max=0 if row['experience_level']=='student' else 1,
            responsibilities=[row['description']], tools_and_technologies=list(role.tools),
            education_requirements=['Relevant degree or equivalent practical projects; enrolled students welcome.'],
            language_requirements=['English'], category=category, currency='INR', generated_data=True,
            source_url=row['application_url'], normalized_skill_tokens=normalized_skills(row['required_skills']+row['preferred_skills']),
            metadata=metadata(category, 'India', seed, i))
        result.append(row)
    return result


def generated_job(seed, index):
    role = ROLES[(index-10) % len(ROLES)]
    level = choose(LEVELS, seed, index, 'level')
    place = choose(PLACES, seed, index, 'place')
    city, country, currency = place
    mode = choose(('remote', 'hybrid', 'onsite', 'onsite'), seed, index, 'mode')
    employment = 'internship' if level == 'student' else choose(('full_time',)*8+('contract', 'apprenticeship'), seed, index, 'employment')
    label = {'student':'Intern', 'fresher':'— Fresher', 'graduate':'— Graduate', 'entry_level':'— Entry Level', 'junior':'— Junior', 'limited_experience':'— Early Career'}[level]
    title = f'{role.title} {label}'
    if index % 4 == 0:
        title = f'{label} {role.title}' if level == 'junior' else f'{role.title} ({label.removeprefix("— ")})'
    if index % 7 == 0:
        title = title.replace('Backend', 'Back-end').replace('Frontend', 'Front-end')
    extra = index % (len(role.skills)-2)
    required = list(role.skills[:3+extra])
    preferred = list(role.skills[3+extra:])
    # Preserve wording variants, while canonical tokens make JS/JavaScript searchable.
    if index % 9 == 0:
        required = ['JS' if s == 'JavaScript' else s for s in required]
        preferred = ['JS' if s == 'JavaScript' else s for s in preferred]
    details = metadata(role.category, country, seed, index)
    if index % 17 == 0:
        preferred = []
        details['edge_cases'].append('no_preferred_skills')
    if index % 29 == 0:
        preferred.append(required[0])
        details['edge_cases'].append('overlapping_skills')
    if index % 83 == 0:
        preferred.append('SEO' if role.category != 'digital_marketing' else 'Microcontrollers')
        details['edge_cases'].append('unrelated_skill')
    responsibilities = [f'Work with a mentor to {role.task}.', f'Use {required[0]} and {required[1]} to produce reviewable work.', 'Document decisions, review feedback and test results.']
    description = f'{title} at a fictional demonstration company. Join the {role.department.lower()} team to {role.task}. Required foundations include {", ".join(required)}.'
    if index % 3:
        description += f' Collaborate with a mentor using {role.tools[0]}. Practical projects and clear communication are valued; all listed work uses development or synthetic data.'
    if index % 43 == 0:
        description = ''
        details['edge_cases'].append('empty_description')
    education = [choose(('Relevant undergraduate degree or equivalent practical experience.', 'Final-year students or recent graduates with demonstrable projects.', 'Diploma, degree or portfolio demonstrating the listed skills.'), seed, index, 'education')]
    department = role.department
    if index % 97 == 0:
        details['company_size'] = None
        department, education = '', []
        details['edge_cases'].append('incomplete_metadata')
    minimum, maximum = (0, 0) if level == 'student' else (1, 3) if level == 'limited_experience' else (0, 2) if level == 'junior' else (0, 1)
    if index % 131 == 0:
        employment, minimum, maximum = 'internship', 2, 3
        education = ['Postgraduate specialization requested for an internship; review this unusual requirement.']
        details['edge_cases'].append('conflicting_requirements')
    posted = REFERENCE_DATE-timedelta(days=number(seed,index,'posted') % 100)
    closing = posted+timedelta(days=45+number(seed,index,'closing') % 120) if index % 5 else None
    if index % 23 == 0:
        posted, closing = REFERENCE_DATE-timedelta(days=90), REFERENCE_DATE-timedelta(days=15)
    if index % 37 == 0:
        posted, closing = REFERENCE_DATE+timedelta(days=15), REFERENCE_DATE+timedelta(days=75)
    if posted > REFERENCE_DATE:
        details['edge_cases'].append('future_dated')
    elif closing and closing < REFERENCE_DATE:
        details['edge_cases'].append('expired')
    salary = None
    if index % 4:
        monthly = employment in {'internship', 'apprenticeship'}
        base = 12000+1000*(index % 15) if currency=='INR' and monthly else 350000+25000*(index % 20) if currency=='INR' else 900+100*(index%12) if monthly else 28000+1500*(index%20)
        salary = dict(minimum=base, maximum=base+base//2, currency=currency, period='month' if monthly else 'year')
    else:
        details['edge_cases'].append('missing_salary')
    ident = f'demo-{role.category.replace("_", "-")}-{index:04d}'
    url = f'https://example.invalid/jobs/{ident}'
    return dict(id=ident, title=title, company=f'Demo {choose(ADJECTIVES,seed,index,"adjective")} {choose(SUFFIXES,seed,index,"suffix")}',
        industry=role.industry, department=department, location=f'Remote — {country}' if mode=='remote' else city,
        country=country, remote=mode=='remote', work_mode=mode, employment_type=employment,
        experience_level=level, experience_years_min=minimum, experience_years_max=maximum,
        description=description, responsibilities=responsibilities, required_skills=required,
        preferred_skills=preferred, tools_and_technologies=list(role.tools), education_requirements=education,
        language_requirements=['English', 'German'] if country=='Germany' and mode=='onsite' else ['English'],
        normalized_skill_tokens=normalized_skills(required+preferred), category=role.category,
        salary=salary, currency=currency, source='seeded_mock', generated_data=True,
        application_url=url, source_url=url, posted_date=posted.isoformat(),
        closing_date=closing.isoformat() if closing else None, metadata=details)


def generate_jobs(seed=DEFAULT_SEED):
    rows = anchors(seed) + [generated_job(seed, index) for index in range(10, 1000)]
    # Deliberate near-duplicate pairs retain different IDs/URLs and requisition wording.
    for base_index, copy_index in ((10, 980), (11, 981), (12, 982)):
        original = rows[base_index]
        group = f'demo-similar-{base_index}'
        original['metadata']['duplicate_group'] = group
        original['metadata']['edge_cases'].append('duplicate_like')
        ident = rows[copy_index]['id']
        duplicate = deepcopy(original)
        duplicate.update(id=ident, application_url=f'https://example.invalid/jobs/{ident}', source_url=f'https://example.invalid/jobs/{ident}')
        duplicate['description'] += ' This is a separate demonstration requisition for an adjacent team.'
        rows[copy_index] = duplicate
    # Round-trip validation also guarantees JSON-ready values and complete field presence.
    return [Job.model_validate(row).model_dump(mode='json') for row in rows]


def dataset_bytes(rows):
    return (json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True)+'\n').encode('utf-8')
