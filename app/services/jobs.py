"""One filter implementation for browsing, matching, and auditable stage counts."""
from app.providers.jobs import JobProvider
from app.schemas.jobs import JobFilters, JobOptions, JobPage
from app.services.job_normalization import normalized_skills, search_text
from app.services.job_locations import parse_location,location_match,clean,COUNTRIES
from app.services.job_roles import role_match,role_spec,words


def availability(job, as_of):
    if job.posted_date > as_of:return 'future'
    if job.closing_date and job.closing_date < as_of:return 'expired'
    return 'active'


def job_options(provider: JobProvider):
    snapshot=provider.snapshot();fields=('category','industry','country','employment_type','experience_level','work_mode')
    values={field:sorted({getattr(job,field) for job in snapshot.jobs}) for field in fields}
    return JobOptions(categories=values['category'],industries=values['industry'],countries=values['country'],employment_types=values['employment_type'],experience_levels=values['experience_level'],work_modes=values['work_mode'],total=len(snapshot.jobs),dataset_version=snapshot.version,reference_date=snapshot.reference_date)


def category_match(category,job,scope='family'):
    if not category:return {'accepted':True,'relation':'not_requested','reason':'No category requested.','evidence':[]}
    spec=role_spec(category)
    if scope=='family' and spec['family']:return role_match(category,job)
    aliases={'ai':'artificial_intelligence','ml':'machine_learning','nlp':'natural_language_processing','genai':'generative_ai','vision':'computer_vision','deep':'deep_learning'}
    expected=aliases.get(spec['family'],category.strip().casefold().replace(' ','_'))
    actual=job.get('category','') if isinstance(job,dict) else job.category
    return {'accepted':actual==expected,'relation':'exact_category' if actual==expected else 'different_category','reason':'Exact category selection is respected.','evidence':[{'job_path':'category','value':actual}]}


def keyword_match(keyword,job):
    if not keyword:return True
    spec=role_spec(keyword)
    if spec['family']:return role_match(keyword,job)['accepted']
    geo=parse_location(job.location,job.country,job.work_mode)
    haystack=search_text(' '.join([job.title,job.company,job.description,job.category,job.subcategory,job.industry,job.department,job.location,job.country,geo['state'] or '',*job.responsibilities,*job.required_skills,*job.preferred_skills,*job.normalized_skill_tokens]))
    # Whole tokens avoid AI/Java matches inside unrelated words or technologies.
    padded=' '+haystack+' '
    return all(' '+term+' ' in padded for term in search_text(keyword).split())


def filter_context(job,filters):
    return {'location':location_match(filters.location,job,filters.location_scope),
        'role':role_match(filters.role,job),'category':category_match(filters.category,job,filters.category_scope)}


def filter_jobs_with_audit(snapshot,filters:JobFilters,include_excluded=False):
    wanted=set(normalized_skills(filters.skills));country=COUNTRIES.get(clean(filters.country),filters.country.strip())
    tests=[
        ('location',lambda j:not filters.location or location_match(filters.location,j,filters.location_scope)['accepted']),
        ('role',lambda j:not filters.role or role_match(filters.role,j)['accepted']),
        ('category',lambda j:category_match(filters.category,j,filters.category_scope)['accepted']),
        ('keyword',lambda j:keyword_match(filters.keyword,j)),
        ('skills',lambda j:wanted.issubset(j.normalized_skill_tokens)),
        ('country',lambda j:not country or j.country.casefold()==country.casefold()),
        ('remote',lambda j:filters.remote is None or j.remote==filters.remote),
        ('work_mode',lambda j:not filters.work_mode or j.work_mode==filters.work_mode),
        ('employment_type',lambda j:not filters.employment_type or j.employment_type==filters.employment_type),
        ('experience',lambda j:not filters.experience or j.experience_level==filters.experience),
        ('company',lambda j:filters.company.casefold() in j.company.casefold()),
        ('industry',lambda j:filters.industry.casefold() in j.industry.casefold()),
        ('posted_dates',lambda j:(not filters.posted_from or j.posted_date>=filters.posted_from) and (not filters.posted_to or j.posted_date<=filters.posted_to)),
        ('availability',lambda j:filters.availability=='all' or availability(j,filters.as_of)==filters.availability),
    ]
    rows=list(snapshot.jobs);steps=[];excluded=[]
    for name,test in tests:
        before=len(rows);kept=[]
        for job in rows:
            if test(job):kept.append(job)
            elif include_excluded:excluded.append({'job_id':job.id,'reason':'hard_filter_'+name})
        rows=kept;steps.append({'filter':name,'input_count':before,'kept_count':len(rows),'excluded_count':before-len(rows)})
    audit={'version':'structured-filters-v1','request_filters':filters.model_dump(mode='json'),'normalized_filters':{
        'location':parse_location(filters.location),'location_scope':filters.location_scope,'role':role_spec(filters.role),
        'category':role_spec(filters.category),'keyword_role_expansion':role_spec(filters.keyword),'category_scope':filters.category_scope,'keyword':search_text(filters.keyword),'country':country,'skills':sorted(wanted),
        'work_mode':filters.work_mode,'availability':filters.availability,'as_of':str(filters.as_of)},
        'total_jobs':len(snapshot.jobs),'steps':steps,'after_hard_filters':len(rows),'max_results':filters.max_results,
        'after_result_limit':min(len(rows),filters.max_results),'result_limit_excluded':max(0,len(rows)-filters.max_results),
        'score_scale':{'minimum':0,'maximum':100,'unit':'raw_hybrid_points','example':'0.5 means 0.5 points; 50 means 50 points.'},
        'empty_state':'no_jobs_in_dataset' if not snapshot.jobs else 'jobs_filtered_out' if not rows else 'eligible_jobs',
        'first_empty_filter':next((step['filter'] for step in steps if step['input_count'] and not step['kept_count']),None)}
    if include_excluded:
        audit['excluded_jobs']=excluded+[{'job_id':j.id,'reason':'max_results_limit'} for j in rows[filters.max_results:]]
    return rows,audit


def filter_jobs(snapshot,filters:JobFilters):return filter_jobs_with_audit(snapshot,filters)[0]


def browse_jobs(provider:JobProvider,filters:JobFilters)->JobPage:
    snapshot=provider.snapshot();rows,audit=filter_jobs_with_audit(snapshot,filters)
    total=min(len(rows),filters.max_results);start=(filters.page-1)*filters.page_size
    items=rows[start:min(start+filters.page_size,total)] if start<total else []
    return JobPage(items=items,total=total,matched_total=len(rows),page=filters.page,page_size=filters.page_size,
        dataset_version=snapshot.version,reference_date=filters.as_of,warnings=list(snapshot.warnings),filters=filters,audit=audit,
        match_context={j.id:filter_context(j,filters) for j in items})
