"""Labeled inputs for future matching experiments; no matcher or ranking scores."""
from app.providers.job_dataset import REFERENCE_DATE


def evaluation_cases(rows):
    by_id = {row['id']: row for row in rows}
    limited = next(row for row in rows if row['experience_level']=='limited_experience' and row['experience_years_min']>0)
    nlp = next(row for row in rows if row['category']=='natural_language_processing')
    synonym = next(row for row in rows if 'JS' in row['required_skills'])
    expired = next(row for row in rows if 'expired' in row['metadata']['edge_cases'])
    future = next(row for row in rows if 'future_dated' in row['metadata']['edge_cases'])
    similar = [row['id'] for row in rows if row['metadata']['duplicate_group']=='demo-similar-10']
    return {
        'reference_date': REFERENCE_DATE.isoformat(),
        'purpose': 'Synthetic evaluation inputs and factual checks; no matching scores or rankings are implemented.',
        'cases': [
            dict(id='exact_required', job_ids=['demo-backend-intern'], candidate=dict(skills=['Python','FastAPI','SQL'],years=0), checks=dict(required_skill_overlap=3,missing_required=[])),
            dict(id='partial_required', job_ids=['demo-backend-intern'], candidate=dict(skills=['Python','SQL']), checks=dict(required_skill_overlap=2,missing_required=['fastapi'])),
            dict(id='synonym_js', job_ids=[synonym['id']], candidate=dict(skills=['JavaScript']), checks=dict(canonical_overlap=['javascript'])),
            dict(id='multi_word', job_ids=['demo-ml-intern'], candidate=dict(skills=['ML','Python']), checks=dict(canonical_overlap=['machine learning','python'])),
            dict(id='semantic_review', job_ids=[nlp['id']], candidate=dict(skills=['Text classification','Language modeling']), checks=dict(human_review_note='Related NLP concepts for future semantic evaluation; no exact-match or ranking oracle is asserted.')),
            dict(id='missing_skills', job_ids=['demo-frontend-intern'], candidate=dict(skills=['SQL','Linux']), checks=dict(required_skill_overlap=0)),
            dict(id='overqualified', job_ids=['demo-backend-intern'], candidate=dict(years=5), checks=dict(above_posted_experience_range=True)),
            dict(id='underqualified', job_ids=[limited['id']], candidate=dict(years=0), checks=dict(below_posted_experience_range=True)),
            dict(id='location_mismatch', job_ids=['demo-backend-intern'], candidate=dict(location='Delhi',work_mode='onsite'), checks=dict(location_match=False)),
            dict(id='remote_preference', job_ids=['demo-frontend-intern'], candidate=dict(work_mode='remote'), checks=dict(work_mode_match=True)),
            dict(id='internship_preference', job_ids=['demo-backend-intern'], candidate=dict(employment_type='internship'), checks=dict(employment_type_match=True)),
            dict(id='domain_preference', job_ids=[nlp['id']], candidate=dict(category='natural_language_processing'), checks=dict(category_match=True)),
            dict(id='preferred_only', job_ids=['demo-backend-intern'], candidate=dict(skills=['Docker','PostgreSQL']), checks=dict(required_skill_overlap=0,preferred_skill_overlap=2)),
            dict(id='duplicate_like', job_ids=similar, candidate=dict(skills=by_id[similar[0]]['required_skills']), checks=dict(distinct_ids=True,shared_duplicate_group=True)),
            dict(id='expired', job_ids=[expired['id']], candidate={}, checks=dict(availability='expired')),
            dict(id='future', job_ids=[future['id']], candidate={}, checks=dict(availability='future')),
        ],
    }
