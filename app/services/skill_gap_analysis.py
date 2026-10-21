"""Pure evidence analysis reused by the CrewAI Flow and evaluation fixtures."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from statistics import mean
import math
import re

from app.schemas.skill_gaps import InventorySkill, Requirement, SkillEvidence, SkillComparison, GapRecord, ImpactEstimate
from app.services.skill_taxonomy import canonical_skill, relationship, TRANSFER
from app.services.gap_taxonomy import category, mentions, vocabulary, FOUNDATIONS
from app.services.match_normalization import normalize_candidate, normalize_job, fingerprint, flatten_text
from app.services.match_baseline import TfidfBaseline
from app.services.match_scoring import score_job, explicit_years

GAP_FIELDS=('skills','projects','experience','education','certifications','career_objective','preferred_roles','preferred_locations','location','experience_level')

def gap_facts(confirmed):
    return {key:confirmed[key] for key in GAP_FIELDS if key in confirmed}

def skill_inventory(facts,proofs,terms):
    collected=defaultdict(list)
    def add(value,path,origin,strength,denied=False):
        normalized=canonical_skill(value)
        proof=proofs.get(path,{})
        collected[normalized].append(SkillEvidence(path=path,original=value,origin=origin,strength=strength,confidence=str(proof.get('confidence','unknown')),source=proof,denied=denied))
    for i,skill in enumerate(facts.get('skills',[])): add(skill,f'skills.{i}','direct','confirmed')
    for field,origin,label in [('projects','project','supported_by_project'),('experience','experience','supported_by_experience')]:
        for i,entry in enumerate(facts.get(field,[])):
            for name in ('technologies','tools'):
                for j,skill in enumerate(entry.get(name,[])):add(skill,f'{field}.{i}.{name}.{j}',origin,label)
            for name in ('description','responsibilities','outcomes','achievements'):
                value=entry.get(name,[] if name!='description' else '')
                values=value if isinstance(value,list) else [value]
                for j,text in enumerate(values):
                    path=f'{field}.{i}.{name}'+(f'.{j}' if isinstance(value,list) else '')
                    for _,word,denied in mentions(text,terms): add(word,path,origin,'inferred',denied)
    for i,text in enumerate(facts.get('certifications',[])):
        for _,word,denied in mentions(text,terms):add(word,f'certifications.{i}','certification','supported_by_certification',denied)
    for _,word,denied in mentions(facts.get('career_objective',''),terms): add(word,'career_objective','objective','mentioned_only',denied)
    return [InventorySkill(normalized=key,originals=sorted({e.original for e in evidence}),category=category(key),evidence=list({(e.path,e.original,e.denied):e for e in evidence}.values()),explicitly_confirmed=any(e.strength=='confirmed' and not e.denied for e in evidence)) for key,evidence in sorted(collected.items())]

def job_requirements(jobs):
    requirements=[];warnings=[]
    for job in jobs:
        seen=set()
        for field,kind in [('required_skills','required'),('preferred_skills','preferred')]:
            for i,word in enumerate(getattr(job,field)):
                key=canonical_skill(word)
                if not key:continue
                if key in seen:
                    warnings.append(f'{job.id}: repeated requirement {key}; explicit required entry takes precedence and is counted once.')
                    continue
                seen.add(key)
                requirements.append(Requirement(job_id=job.id,normalized=key,original=word,path=f'{field}.{i}',type=kind,category=category(key),critical=kind=='required'))
        explicit=bool(seen)
        for i,word in enumerate(job.tools_and_technologies):
            key=canonical_skill(word)
            if key and key not in seen:
                seen.add(key)
                requirements.append(Requirement(job_id=job.id,normalized=key,original=word,path=f'tools_and_technologies.{i}',type='optional',category=category(key)))
        if not explicit:
            requirements.append(Requirement(job_id=job.id,normalized='__requirements_unknown__',original='No explicit skill requirements provided',path='required_skills/preferred_skills',type='unknown',category='unknown'))
        if not job.description:warnings.append(f'{job.id}: description is empty; only explicit requirement fields were compared.')
    return requirements,warnings

def compare(inventory,requirements):
    by_skill={s.normalized:s for s in inventory};has_inventory=any(any(e.origin!='objective' and not e.denied for e in s.evidence) for s in inventory)
    output=[]
    for req in requirements:
        exact=by_skill.get(req.normalized);evidence=exact.evidence if exact else []
        positive=[e for e in evidence if not e.denied];denied=[e for e in evidence if e.denied]
        explicit=[e for e in positive if e.strength=='confirmed']
        supported=[e for e in positive if e.strength in {'supported_by_project','supported_by_experience'}]
        related=[]
        for candidate in inventory:
            relation=relationship(candidate.normalized,req.normalized)
            if relation in {'related','transferable'} and any(e.strength in {'confirmed','supported_by_project','supported_by_experience'} and not e.denied for e in candidate.evidence):
                related.append({'skill':candidate.normalized,'relationship':relation,'evidence_paths':[e.path for e in candidate.evidence if not e.denied],'counts_as_requirement_match':False,'reason':TRANSFER.get((candidate.normalized,req.normalized),'Related concepts are not equivalent proficiency.')})
        state='missing';rel='unrelated';credit=False;review='unreviewed'
        reason='No equivalent explicit skill/technology evidence is present. This is an evidence gap, not proof of inability.'
        if req.type=='unknown':state,rel,reason='unknown','unknown','Employer skill requirements are unavailable; do not infer mandatory skills from the title.'
        elif denied and positive:
            state,rel,review,reason='unknown','unknown','needs_review','Confirmed records contain both a mention and an explicit denial; review the conflict before relying on this skill.'
        elif denied:state,rel,reason='explicitly_mismatched','exact','An explicit statement denies experience with this skill; review whether that statement is current.'
        elif explicit:
            state,credit='confirmed_match',True
            rel='exact' if any(relationship(e.original,req.original)=='exact' for e in explicit) else 'synonym'
            reason='Equivalent skill is explicitly listed in the confirmed profile. Proficiency level is not established.'
        elif supported:
            state,credit='partial_match',True
            rel='exact' if any(relationship(e.original,req.original)=='exact' for e in supported) else 'synonym'
            reason='A confirmed project/work technology field supports use; it is not a separately confirmed proficiency claim.'
        elif positive:
            state,rel='partial_match','weak_association'
            if all(e.origin=='objective' for e in positive):state='unknown'
            reason='Narrative/certification/objective mention needs review. It is not automatically credited as demonstrated skill.'
        elif related:
            if any(r['relationship']=='transferable' for r in related):state,rel='transferable_match','transferable'
            else:state,rel=('partial_match' if req.category=='programming_languages' else 'strong_related_match'),'related'
            reason='Existing skills provide related or transferable foundations; the listed requirement remains unconfirmed.'
        elif not has_inventory:state,rel,reason='unknown','unknown','Candidate skill evidence is unavailable. Verify ability before treating this as a learning gap.'
        output.append(SkillComparison(job_id=req.job_id,normalized=req.normalized,classification=state,relationship=rel,requirement=req,candidate_evidence=evidence,related_skills=related,counts_as_requirement_match=credit,reason=reason,review_status=review))
    return output

def score_context(facts,jobs,settings,as_of):
    candidate=normalize_candidate(facts);normalized=[normalize_job(j) for j in jobs]
    baseline=TfidfBaseline([candidate['text']]+[j['text'] for j in normalized])
    rows={};features={}
    for job in normalized:
        score=baseline.similarity(baseline.vector(candidate['text']),baseline.vector(job['text']))
        project=baseline.similarity(baseline.vector(candidate['project_experience_text']),baseline.vector(job['project_text']))
        interests=baseline.similarity(baseline.vector(candidate['interests_text']),baseline.vector(job['text']))
        features[job['original']['id']]=(job,score,project,interests)
        rows[job['original']['id']]=score_job(candidate,job,score,project,interests,None,settings,as_of)
    return rows,features

def priority_components(gap,selected_count,corpus_count,inventory,weights):
    proximity=1 if gap['related_skills'] else .25
    if gap['candidate_evidence']:proximity=.75
    foundational=gap['normalized'] in FOUNDATIONS
    components={'mandatory':1 if gap['required_count'] else 0,'selected_frequency':gap['affected_count']/max(1,selected_count),
        'corpus_frequency':min(1,(gap['corpus_required_count']+gap['corpus_preferred_count'])/max(1,corpus_count)),
        'role_centrality':gap['required_count']/max(1,gap['affected_count']),'proximity':proximity,
        'simulated_impact':max(0,min(1,gap['average_score_impact']/100)),
        'foundation':1 if foundational else 0,'effort':.75 if gap['category'] in {'tools','frameworks','libraries'} else .5}
    details={k:{'value':round(v,6),'weight':weights[k],'contribution':round(v*weights[k]*100,6)} for k,v in components.items()}
    score=round(sum(v['contribution'] for v in details.values()),4)
    level='Critical' if score>=75 and gap['required_count'] else 'High' if score>=55 else 'Medium' if score>=30 else 'Low'
    if gap['requirement_type']=='unknown':level='Low'
    return score,level,details

def aggregate_gaps(facts,inventory,comparisons,jobs,corpus_counts,corpus_count,settings,as_of,saved_scores=None):
    rows,features=score_context(facts,jobs,settings,as_of);grouped=defaultdict(list)
    for comparison in comparisons:
        if comparison.classification!='confirmed_match':grouped[comparison.normalized].append(comparison)
    gaps=[]
    for skill,entries in sorted(grouped.items()):
        affected=sorted({e.job_id for e in entries});required={e.job_id for e in entries if e.requirement.type=='required'};preferred={e.job_id for e in entries if e.requirement.type=='preferred'}
        evidence=list({(e.path,e.original,e.denied):e for c in entries for e in c.candidate_evidence}.values())
        related=list({(r['skill'],r['relationship']):r for c in entries for r in c.related_skills}.values())
        optional={e.job_id for e in entries if e.requirement.type=='optional'}
        kind='unknown' if skill=='__requirements_unknown__' else 'mixed' if required and preferred else 'required' if required else 'preferred' if preferred else 'optional'
        impacts=[]
        for id in affected:
            current=rows[id];simulated=current
            if skill!='__requirements_unknown__':
                hypothetical=deepcopy(facts);hypothetical['skills']=list(hypothetical.get('skills',[]))+[entries[0].requirement.original]
                job,lexical,project,interests=features[id]
                simulated=score_job(normalize_candidate(hypothetical),job,lexical,project,interests,None,settings,as_of)
            impacts.append(ImpactEstimate(job_id=id,current_score=current.hybrid_score,simulated_score=simulated.hybrid_score,delta=round(simulated.hybrid_score-current.hybrid_score,4),saved_match_score=(saved_scores or {}).get(id)))
        counts=corpus_counts.get(skill,{'required':0,'preferred':0})
        gap=dict(id='gap-'+fingerprint(skill)[:20],skill=entries[0].requirement.original,normalized=skill,category=entries[0].requirement.category,requirement_type=kind,
            original_wordings=sorted({e.requirement.original for e in entries}),affected_jobs=affected,affected_count=len(affected),required_count=len(required),preferred_count=len(preferred),optional_count=len(optional),corpus_required_count=counts['required'],corpus_preferred_count=counts['preferred'],
            candidate_evidence=evidence,related_skills=related,classification_counts=dict(Counter(e.classification for e in entries)),impact=impacts,average_score_impact=round(mean(i.delta for i in impacts),4))
        score,level,parts=priority_components(gap,len(jobs),corpus_count,inventory,settings.skill_gap_priority_weights)
        review='needs_review' if any(e.review_status=='needs_review' for e in entries) else 'unreviewed'
        verify=any(e.classification=='unknown' for e in entries)
        experience=bool(re.search(r'years|professional experience|commercial experience|leadership',skill))
        gap.update(priority=level,priority_score=score,priority_components=parts,
            priority_reason=f"Required by {len(required)}, preferred by {len(preferred)} and optional tools in {len(optional)} of {len(jobs)} selected jobs; corpus required/preferred frequency {counts['required']+counts['preferred']}/{corpus_count}. Priority combines requirement frequency, centrality, foundations, proximity, effort heuristic and simulated impact.",
            learnability='Experience-dependent; practical evidence takes time.' if experience else 'Learning and evidence-building; exact effort is unknown.',
            confidence='low' if verify or review=='needs_review' or not evidence else 'medium',review_status=review,
            explanation=('Requirements are unavailable; ask the employer for details.' if kind=='unknown' else f"{entries[0].requirement.original} has no independently confirmed skill claim for {len(affected)} selected jobs. {entries[0].reason}")+(' Verify existing ability before learning.' if verify else ''),
            guidance=[{'id':'verify','text':'Review the listed evidence and confirm whether this skill is already demonstrated. No profile changes are automatic.','evidence_job_ids':affected},
                {'id':'practice','text':f"Build a small, assessable exercise using {entries[0].requirement.original}; record what you personally implemented and tested.",'evidence_job_ids':affected},
                {'id':'recheck','text':'After genuine practice, review and confirm any new profile evidence, then rerun matching. Simulations are not employment guarantees.','evidence_job_ids':affected}] if kind!='unknown' else [{'id':'verify','text':'Obtain explicit skill requirements before planning learning.','evidence_job_ids':affected}])
        gaps.append(GapRecord(**gap))
    gaps.sort(key=lambda g:(-g.priority_score,-g.required_count,g.normalized))
    return gaps,rows

def validate_analysis(facts,jobs,inventory,requirements,comparisons,gaps,settings,corpus_counts,corpus_count,terms=None,as_of=None,saved_scores=None):
    expected_inventory=skill_inventory(facts,{},terms or vocabulary(jobs))
    # Evidence source metadata can be richer than reconstruction; paths/terms must agree.
    allowed={(s.normalized,e.path,e.original,e.denied) for s in expected_inventory for e in s.evidence}
    actual_evidence={(s.normalized,e.path,e.original,e.denied) for s in inventory for e in s.evidence}
    if actual_evidence!=allowed:raise ValueError('inventory_incomplete_or_unsupported')
    def evidence_shape(item):
        data=item.model_dump()
        for proof in data['evidence']:proof.pop('source');proof.pop('confidence')
        return data
    if [evidence_shape(s) for s in inventory]!=[evidence_shape(s) for s in expected_inventory]:raise ValueError('unsupported_inventory_claim')
    for s in inventory:
        for e in s.evidence:
            if (s.normalized,e.path,e.original,e.denied) not in allowed: raise ValueError('unsupported_candidate_evidence')
    actual,ignored=job_requirements(jobs)
    if [r.model_dump() for r in requirements]!=[r.model_dump() for r in actual]: raise ValueError('unsupported_requirement')
    expected=compare(inventory,requirements)
    if [c.model_dump() for c in comparisons]!=[c.model_dump() for c in expected]:raise ValueError('comparison_conflict')
    if len({g.normalized for g in gaps})!=len(gaps):raise ValueError('duplicate_gap')
    expected_keys={c.normalized for c in comparisons if c.classification!='confirmed_match'}
    if {g.normalized for g in gaps}!=expected_keys:raise ValueError('missing_or_unsupported_gap')
    ids={j.id for j in jobs};checks=0
    for g in gaps:
        entries=[c for c in comparisons if c.normalized==g.normalized and c.classification!='confirmed_match']
        if g.affected_jobs!=sorted({e.job_id for e in entries}) or g.affected_count!=len(g.affected_jobs):raise ValueError('unsupported_affected_jobs')
        if not set(g.affected_jobs)<=ids:raise ValueError('unknown_job')
        if g.required_count!=len({e.job_id for e in entries if e.requirement.type=='required'}):raise ValueError('requirement_type_conflict')
        score,level,parts=priority_components(g.model_dump(),len(jobs),corpus_count,inventory,settings.skill_gap_priority_weights)
        if score!=g.priority_score or level!=g.priority or parts!=g.priority_components:raise ValueError('priority_conflict')
        for impact in g.impact:
            if not math.isfinite(impact.delta) or abs(impact.simulated_score-impact.current_score-impact.delta)>.00011:raise ValueError('impact_calculation')
        for item in g.guidance:
            if not set(item['evidence_job_ids'])<=set(g.affected_jobs):raise ValueError('unsupported_guidance')
        checks+=len(entries)
    expected_gaps,_=aggregate_gaps(facts,inventory,comparisons,jobs,corpus_counts,corpus_count,settings,as_of or (jobs[0].metadata.reference_date if jobs else date(2026,10,19)),saved_scores)
    expected_by_skill={g.normalized:g for g in expected_gaps}
    for gap in gaps:
        original=expected_by_skill[gap.normalized]
        for key in ('explanation','required_count','preferred_count','optional_count','corpus_required_count','corpus_preferred_count','average_score_impact','original_wordings','confidence','learnability','candidate_evidence','related_skills','classification_counts','impact'):
            if getattr(gap,key)!=getattr(original,key):raise ValueError('unsupported_gap_output')
        actions={a['id']:a for a in original.guidance}
        if not gap.guidance or any(a!=actions.get(a.get('id')) for a in gap.guidance):raise ValueError('unsupported_guidance_text')
    return {'schema_valid':True,'comparison_checks':checks,'unsupported_claims':0,'duplicate_gaps':0,'requirement_checks':len(requirements),'evidence_checks':len(allowed),'priority_checks':len(gaps),'profile_modified':False}
