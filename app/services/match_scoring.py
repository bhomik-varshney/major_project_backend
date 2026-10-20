"""Configurable evidence-weighted matching; no LLM can change these scores."""
import math
import re
from datetime import date

from app.schemas.matching import MatchComponent,MatchReason,RankedMatch
from app.services.match_normalization import tokens,ROLE_STOP,fingerprint

from app.services.skill_taxonomy import TRANSFER, normalize_label, relationship
from app.services.job_locations import location_match


def component(score,reason,evidence=(),state=None):
    if score is None:state=state or 'missing_information'
    else:score=max(0,min(100,float(score)));state=state or ('confirmed_match' if score==100 else 'explicit_mismatch' if score==0 else 'likely_match')
    return MatchComponent(score=score,state=state,reason=reason,evidence=list(evidence))


def overlap(candidate,requirements):
    by_token={}
    for mapping in candidate['skill_mappings']:by_token.setdefault(mapping['normalized'],[]).append(mapping)
    matched=[];missing=[];seen=set()
    for requirement in requirements:
        if requirement['normalized'] in seen:continue
        seen.add(requirement['normalized'])
        if requirement['normalized'] in by_token:
            matched.append({**requirement,'match_type':'exact' if any(relationship(m['original'],requirement['original'])=='exact' for m in by_token[requirement['normalized']]) else 'synonym','candidate_evidence':by_token[requirement['normalized']]})
        else:missing.append({**requirement,'reason':'No equivalent explicit skill/technology field is present in the confirmed profile; narrative mentions and related tools require review.'})
    return matched,missing


def explicit_years(experience,as_of):
    months={name:i+1 for i,name in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'])}
    intervals=[]
    for entry in experience:
        period=entry.get('period','') or entry.get('start_date','')+' - '+entry.get('end_date','')
        matches=list(re.finditer(r'\b([A-Za-z]{3})[a-z]*\s+((?:19|20)\d{2})\b|\b(present|current|ongoing)\b',period,re.I))
        if len(matches)!=2:continue
        points=[]
        for match in matches:
            if match[3]:points.append(as_of.year*12+as_of.month-1)
            elif match[1].lower() in months:points.append(int(match[2])*12+months[match[1].lower()]-1)
        current=as_of.year*12+as_of.month-1
        if len(points)==2 and 0<=points[1]-points[0]<=600 and points[0]<=current:
            intervals.append([points[0],min(points[1],current)])
    if not intervals:return None
    merged=[]
    for start,end in sorted(intervals):
        if merged and start<=merged[-1][1]+1:merged[-1][1]=max(end,merged[-1][1])
        else:merged.append([start,end])
    return sum(end-start+1 for start,end in merged)/12


def education_component(candidate,job):
    requirements=job.get('education_requirements',[])
    if not requirements:return component(None,'No education requirement is listed.',state='not_applicable')
    def level(text):
        text=text.casefold()
        if re.search(r'postgraduate|master|m\.?tech|m\.?sc|mba|mca',text):return 4
        if re.search(r'undergraduate|bachelor|b\.?tech|b\.?sc|bca',text):return 3
        if 'diploma' in text:return 2
        if re.search(r'\bdegree\b',text):return 3
        return None
    levels=[level(e.get('qualification','')) for e in candidate['education']]
    levels=[v for v in levels if v is not None]
    if not levels:return component(None,'Confirmed education does not establish a comparable qualification level.')
    required=[level(v) for v in requirements];required=[v for v in required if v is not None]
    if not required:return component(None,'The education wording requires employer review; no degree level was inferred.')
    sufficient=max(levels)>=min(required)
    practical=any(re.search(r'equivalent|portfolio|projects',v,re.I) for v in requirements)
    if not sufficient and practical and candidate['project_experience_text']:
        return component(60,'Written project/portfolio alternatives may apply; employer confirmation is needed.',[{'candidate_path':'projects','job_path':'education_requirements'}],state='likely_match')
    return component(100 if sufficient else 0,'Documented qualification level meets the written level; subject and eligibility still need review.' if sufficient else 'The documented qualification level is below the listed level.',[{'candidate_path':'education','job_path':'education_requirements'}],state='likely_match' if sufficient else 'explicit_mismatch')


def experience_component(candidate,job,as_of):
    years=explicit_years(candidate['experience'],as_of)
    if years is not None:
        enough=years>=job.get('experience_years_min',0)
        return component(100 if enough else 0,f'Confirmed dated work covers approximately {years:.2f} years; this listing asks for at least {job.get("experience_years_min",0)}. Internship equivalence is not guaranteed.',[{'candidate_path':'experience','job_path':'experience_years_min'}],state='likely_match' if enough else 'explicit_mismatch')
    level=candidate['experience_level']
    if not level:return component(None,'Experience duration and career stage are not established by confirmed fields.')
    levels={'student':{'student':100,'fresher':85,'graduate':75,'entry_level':70,'junior':35,'limited_experience':45},
        'graduate':{'student':75,'fresher':100,'graduate':100,'entry_level':95,'junior':65,'limited_experience':70},
        'early_career':{'student':60,'fresher':80,'graduate':85,'entry_level':100,'junior':95,'limited_experience':100},
        'experienced':{'student':35,'fresher':50,'graduate':60,'entry_level':70,'junior':80,'limited_experience':80}}
    score=levels.get(level,{}).get(job.get('experience_level'))
    return component(score,'Career-stage compatibility is a heuristic; exact professional years are unknown.',[{'candidate_path':'experience_level','job_path':'experience_level'}],state='likely_match' if score is not None else 'missing_information')


def location_component(candidate,job):
    preferences=candidate['preferred_locations']
    if not preferences:return component(None,'No confirmed work-location preference is available. A home address is not a relocation preference.')
    details=[location_match(p,job,'city') for p in preferences]
    matched=next((d for d in details if d['accepted']),None)
    return component(100 if matched else 0,(matched['reason'] if matched else 'The listing differs from the explicitly confirmed work-location preferences.')+' Country, time-zone and work eligibility need employer verification.',[{'candidate_path':'preferred_locations','job_path':'location/country/work_mode','relation':matched['relation'] if matched else 'mismatch'}])


def role_component(candidate,normalized_job):
    roles=candidate['preferred_roles']
    if not roles:return component(None,'No confirmed preferred role is available.')
    job_words=set(tokens(normalized_job['role_text']))-ROLE_STOP
    scores=[]
    for role in roles:
        words=set(tokens(role))-ROLE_STOP
        if words:scores.append(100*len(words&job_words)/len(words))
    return component(max(scores) if scores else None,'Overlap between explicit preferred-role terms and the job title/category.',[{'candidate_path':'preferred_roles','job_path':'title'},{'candidate_path':'preferred_roles','job_path':'category'}],state='likely_match' if scores and max(scores)>0 else None)


def score_job(candidate,normalized_job,baseline,project_score,interest_score,semantic,settings,as_of,lexical_score=None,lexical_name='tfidf'):
    lexical_score=baseline if lexical_score is None else lexical_score
    job=normalized_job['original'];required,missing=overlap(candidate,normalized_job['required']);preferred,preferred_missing=overlap(candidate,normalized_job['preferred'])
    known_skills=bool(candidate['skill_tokens'])
    required_total=len(required)+len(missing);preferred_total=len(preferred)+len(preferred_missing)
    parts={
        'semantic':component(semantic,'Cosine similarity from the configured semantic embedding model.' if semantic is not None else 'Semantic embeddings are unavailable; this component is not scored.'),
        'lexical_similarity':component(lexical_score,('Visible TF-IDF baseline used as fallback text similarity.' if lexical_name=='tfidf' else 'BM25 query saturation fraction used as fallback text similarity.') if semantic is None else 'Semantic embeddings are active; baseline remains separately visible.',state='likely_match') if semantic is None else component(None,'Semantic model supplies the text-similarity component.',state='not_applicable'),
        'required_skills':component(100*len(required)/required_total if required_total and known_skills else None,'Fraction of distinct listed required skills supported by explicit confirmed skill/technology fields.' if required_total else 'No usable required-skill requirements are listed.',[{'candidate_path':m['candidate_evidence'][0]['source_path'],'job_path':m['source_path']} for m in required],state='not_applicable' if not required_total else None),
        'preferred_skills':component(100*len(preferred)/preferred_total if preferred_total and known_skills else None,'Fraction of distinct preferred skills supported; repeated required skills are not counted twice.',[{'candidate_path':m['candidate_evidence'][0]['source_path'],'job_path':m['source_path']} for m in preferred],state='not_applicable' if not preferred_total else None),
        'title_category':role_component(candidate,normalized_job),
        'project_experience':component(project_score if candidate['project_experience_text'] else None,'Relevance of confirmed project/work text to the listing. Uses semantic cosine when available, otherwise TF-IDF.',[{'candidate_path':'projects/experience','job_path':'description/responsibilities'}],state='likely_match' if candidate['project_experience_text'] else None),
        'education':education_component(candidate,job),'experience':experience_component(candidate,job,as_of),'location':location_component(candidate,job),
        'interests':component(interest_score if candidate['interests_text'] else None,'TF-IDF alignment of the confirmed summary and preferred roles with this opportunity.',[{'candidate_path':'career_objective/preferred_roles','job_path':'title/category/description'}],state='likely_match' if candidate['interests_text'] else None)}
    for key,part in parts.items():part.configured_weight=settings.matching_weights.get('semantic' if key=='lexical_similarity' and semantic is None else key,0)
    if semantic is None:parts['semantic'].configured_weight=0
    available=sum(p.configured_weight for p in parts.values() if p.score is not None)
    for part in parts.values():
        if part.score is not None and available:
            part.effective_weight=part.configured_weight/available;part.contribution=part.score*part.effective_weight
    weighted=sum(p.contribution for p in parts.values())
    penalty=settings.matching_critical_penalty*len(missing)/required_total if required_total and known_skills else 0
    ceiling=settings.matching_unknown_ceiling_base+(100-settings.matching_unknown_ceiling_base)*available
    final=max(0,min(100,ceiling,weighted-penalty))
    transferable=[]
    for a in candidate['skill_tokens']:
        for m in missing:
            if (a,m['normalized']) in TRANSFER:
                transferable.append({'candidate_skill':a,'job_skill':m['original'],'reason':TRANSFER[(a,m['normalized'])],'counts_as_requirement_match':False})
    reasons=[]
    def reason(id,kind,text,evidence=()):reasons.append(MatchReason(id=id,kind=kind,text=text,evidence=list(evidence)))
    if required:reason('required','positive',f'{len(required)} of {required_total} listed required skills are documented: '+', '.join(m['original'] for m in required)+'.',parts['required_skills'].evidence)
    if preferred:reason('preferred','positive','Documented preferred skills: '+', '.join(m['original'] for m in preferred)+'.',parts['preferred_skills'].evidence)
    if missing:reason('missing','gap','Required skills without confirmed explicit skill-field evidence: '+', '.join(m['original'] for m in missing)+'. This does not establish that you lack them.',[{'job_path':m['source_path']} for m in missing])
    if not known_skills:reason('skills_unknown','limitation','No explicit confirmed skill evidence is available. Skill components and the critical-skill penalty are unknown, not zero.')
    for key in ('title_category','experience','education','location'):
        part=parts[key]
        reason(key,'consideration' if part.score is None or part.score>=50 else 'gap',part.reason,part.evidence)
    if transferable:reason('transfer','consideration',' '.join(t['reason'] for t in transferable[:2]),[{'candidate_skill':t['candidate_skill'],'job_skill':t['job_skill']} for t in transferable[:2]])
    reason('method','limitation','Scores are comparison heuristics, not hiring probabilities. All listings are seeded/mock data. '+('Semantic embeddings are active.' if semantic is not None else 'Semantic embeddings are unavailable; '+lexical_name.upper()+' supplies text similarity.'))
    highlights=[r.id for r in reasons if r.kind=='positive'][:2]+[r.id for r in reasons if r.kind=='gap'][:1]
    if not highlights:highlights=[r.id for r in reasons[:2]]
    explanation=' '.join(r.text for id in highlights for r in reasons if r.id==id)
    calculation={'weighted_sum':weighted,'available_weight':available,'critical_skill_penalty':penalty,'critical_policy':'All listed required skills are treated as critical evidence requirements.',
        'critical_penalty_max':settings.matching_critical_penalty,'unknown_ceiling_base':settings.matching_unknown_ceiling_base,
        'uncertainty_ceiling':ceiling,'formula':'max(0, min(100, uncertainty_ceiling, sum(component.score * effective_weight) - critical_skill_penalty))',
        'semantic_fallback':semantic is None,'weights_version':'hybrid-v2-retrieval','unrounded_final':final}
    calculation['reason_catalog_hash']=fingerprint([r.model_dump() for r in reasons])
    return RankedMatch(job=job,rank=1,baseline_score=round(baseline,4),hybrid_score=round(final,4),components=parts,calculation=calculation,
        matched_required=required,matched_preferred=preferred,missing_required=missing,missing_preferred=preferred_missing,transferable_skills=transferable,
        reasons=reasons,explanation=explanation,highlighted_reason_ids=highlights,warnings=['Confirm eligibility, requirements and missing evidence before treating a recommendation as suitable.'])


def validate_ranking(results,candidate,minimum,normalized_jobs):
    seen=set();previous=None;warnings=[]
    for index,row in enumerate(results,1):
        if any(not math.isfinite(score) or not 0<=score<=100 for score in (row.hybrid_score,row.baseline_score)):
            raise ValueError('impossible_score')
        for part in row.components.values():
            if part.score is not None and (not math.isfinite(part.score) or not 0<=part.score<=100):raise ValueError('impossible_component')
            if abs(part.contribution-(part.score or 0)*part.effective_weight)>.000001:raise ValueError('component_contribution_mismatch')
        if row.match_percentage is not None:
            from app.services.match_calibration import calibrate_match
            expected_percentage,metadata=calibrate_match(row)
            if row.match_percentage!=expected_percentage or row.calibration!=metadata:raise ValueError('calibration_mismatch')
        if any(value is not None and (not math.isfinite(value) or value<0 or (name!='bm25_raw' and value>100.000001)) for name,value in row.method_scores.items()):raise ValueError('invalid_retrieval_score')
        job=row.job
        if job.id in seen:raise ValueError('duplicate_recommendation')
        seen.add(job.id)
        order=(-row.hybrid_score,-row.baseline_score,job.id)
        if previous is not None and order<previous:raise ValueError('unstable_ranking')
        previous=order
        if row.rank!=index or row.hybrid_score<minimum:raise ValueError('invalid_rank_or_threshold')
        available=sum(p.configured_weight for p in row.components.values() if p.score is not None)
        if abs(sum(p.effective_weight for p in row.components.values())-(1 if available else 0))>1e-6:
            raise ValueError('weight_normalization_mismatch')
        if abs(available-row.calculation['available_weight'])>1e-6:raise ValueError('coverage_mismatch')
        for part in row.components.values():
            weight=part.configured_weight/available if part.score is not None and available else 0
            if abs(part.effective_weight-weight)>1e-6:raise ValueError('effective_weight_mismatch')
            if part.score is None and part.state not in {'missing_information','not_applicable'}:raise ValueError('unknown_component_state')
        ceiling=row.calculation['unknown_ceiling_base']+(100-row.calculation['unknown_ceiling_base'])*available
        if abs(ceiling-row.calculation['uncertainty_ceiling'])>1e-6:raise ValueError('ceiling_mismatch')
        expected=max(0,min(100,row.calculation['uncertainty_ceiling'],sum(p.contribution for p in row.components.values())-row.calculation['critical_skill_penalty']))
        if abs(expected-row.hybrid_score)>.00011:raise ValueError('score_calculation_mismatch')
        required,missing=overlap(candidate,normalized_jobs[job.id]['required'])
        if row.matched_required!=required or row.missing_required!=missing:raise ValueError('unsupported_matched_skill')
        required_score=100*len(required)/(len(required)+len(missing)) if required or missing else None
        expected_skill=required_score if candidate['skill_tokens'] else None
        if row.components['required_skills'].score!=expected_skill:raise ValueError('required_component_mismatch')
        penalty=row.calculation['critical_penalty_max']*len(missing)/(len(required)+len(missing)) if candidate['skill_tokens'] and (required or missing) else 0
        if abs(penalty-row.calculation['critical_skill_penalty'])>1e-6:raise ValueError('penalty_mismatch')
        preferred,preferred_missing=overlap(candidate,normalized_jobs[job.id]['preferred'])
        if row.matched_preferred!=preferred or row.missing_preferred!=preferred_missing:raise ValueError('unsupported_preferred_skill')
        if row.calculation.get('reason_catalog_hash')!=fingerprint([r.model_dump() for r in row.reasons]):raise ValueError('unsupported_reason_catalog')
        known={r.id:r for r in row.reasons}
        if any(id not in known for id in row.highlighted_reason_ids):raise ValueError('unsupported_explanation')
        text=' '.join(known[id].text for id in row.highlighted_reason_ids)
        if row.explanation!=text:raise ValueError('contradictory_explanation')
        if set(m['normalized'] for m in row.matched_required)&set(m['normalized'] for m in row.missing_required):raise ValueError('contradictory_skill_lists')
    return {'checked':len(results),'unsupported_reasons':0,'duplicate_recommendations':0,'score_errors':0,'stable_order':True,'warnings':warnings}
