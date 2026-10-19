from app.providers.jobs import JobProvider
from app.schemas.jobs import JobFilters, JobOptions, JobPage
from app.services.job_normalization import normalized_skills, search_text


def availability(job, as_of):
    if job.posted_date > as_of:
        return 'future'
    if job.closing_date and job.closing_date < as_of:
        return 'expired'
    return 'active'


def job_options(provider: JobProvider):
    snapshot = provider.snapshot()
    fields = ('category', 'industry', 'country', 'employment_type', 'experience_level', 'work_mode')
    values = {field: sorted({getattr(job, field) for job in snapshot.jobs}) for field in fields}
    return JobOptions(categories=values['category'], industries=values['industry'], countries=values['country'],
        employment_types=values['employment_type'], experience_levels=values['experience_level'],
        work_modes=values['work_mode'], total=len(snapshot.jobs), dataset_version=snapshot.version,
        reference_date=snapshot.reference_date)


def browse_jobs(provider: JobProvider, filters: JobFilters) -> JobPage:
    snapshot = provider.snapshot()
    keywords = search_text(filters.keyword).split()
    skills = set(normalized_skills(filters.skills))
    rows = []
    for job in snapshot.jobs:
        if keywords:
            haystack = search_text(' '.join([job.title, job.company, job.description, job.category,
                job.industry, job.department, *job.responsibilities, *job.required_skills,
                *job.preferred_skills, *job.normalized_skill_tokens]))
            if not all(word in haystack for word in keywords):
                continue
        if not skills.issubset(job.normalized_skill_tokens):
            continue
        if filters.location.strip().casefold() not in job.location.casefold():
            continue
        if filters.category and filters.category.casefold() != job.category.casefold():
            continue
        if filters.country and filters.country.casefold() != job.country.casefold():
            continue
        if filters.remote is not None and job.remote != filters.remote:
            continue
        if filters.work_mode and job.work_mode != filters.work_mode:
            continue
        if filters.employment_type and job.employment_type != filters.employment_type:
            continue
        if filters.experience and job.experience_level != filters.experience:
            continue
        if filters.company.strip().casefold() not in job.company.casefold():
            continue
        if filters.industry.strip().casefold() not in job.industry.casefold():
            continue
        if filters.posted_from and job.posted_date < filters.posted_from:
            continue
        if filters.posted_to and job.posted_date > filters.posted_to:
            continue
        if filters.availability != 'all' and availability(job, filters.as_of) != filters.availability:
            continue
        rows.append(job)
    total = min(len(rows), filters.max_results)
    start = (filters.page - 1) * filters.page_size
    return JobPage(items=rows[start:min(start+filters.page_size,total)] if start < total else [],
        total=total, matched_total=len(rows), page=filters.page, page_size=filters.page_size,
        dataset_version=snapshot.version, reference_date=filters.as_of,
        warnings=list(snapshot.warnings), filters=filters)
