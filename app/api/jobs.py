from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from app.api.dependencies import current_user
from app.core.errors import AppError
from app.db.models import User
from app.providers.job_dataset import REFERENCE_DATE
from app.schemas.jobs import EmploymentType, ExperienceLevel, JobFilters, JobOptions, JobPage, WorkMode
from app.services.jobs import browse_jobs, job_options

router = APIRouter(tags=['Seeded demonstration jobs'])


@router.get('/jobs/options', response_model=JobOptions)
def options(request: Request, user: User = Depends(current_user)):
    return job_options(request.app.state.job_provider)


@router.get('/jobs', response_model=JobPage)
def jobs(request: Request, keyword: str = Query(default='', max_length=120),
         skill: list[str] = Query(default=[], max_length=20),
         role: str = Query(default='',max_length=120),
         category_scope: Literal['exact','family'] = 'family', location_scope: Literal['city','compatible','state','country'] = 'city',
         category: str = Query(default='', max_length=80),
         location: str = Query(default='', max_length=120),
         country: str = Query(default='', max_length=80), remote: bool | None = None,
         work_mode: WorkMode | None = None, employment_type: EmploymentType | None = None,
         experience: ExperienceLevel | None = None,
         company: str = Query(default='', max_length=160), industry: str = Query(default='', max_length=160),
         posted_from: date | None = None, posted_to: date | None = None,
         availability: Literal['all','active','expired','future'] = 'all', as_of: date = REFERENCE_DATE,
         page: int = Query(default=1, ge=1, le=10000), page_size: int = Query(default=6, ge=1, le=50),
         max_results: int = Query(default=10000, ge=1, le=10000), user: User = Depends(current_user)):
    try:
        filters = JobFilters(role=role,category_scope=category_scope,location_scope=location_scope,keyword=keyword, skills=skill, category=category, location=location,
            country=country, remote=remote, work_mode=work_mode, employment_type=employment_type,
            experience=experience, company=company, industry=industry, posted_from=posted_from,
            posted_to=posted_to, availability=availability, as_of=as_of,
            page=page, page_size=page_size, max_results=max_results)
    except ValidationError:
        raise AppError(422, 'job_filters_invalid', 'Check the job filters; the posted date range must be ordered.') from None
    return browse_jobs(request.app.state.job_provider, filters)
