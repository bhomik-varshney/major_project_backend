"""Build and validate an evidence-connected graph before projecting a profile draft."""
from copy import deepcopy
import re

from app.schemas.canonical import (RawEvidence, CanonicalValue, CanonicalEntity, CanonicalSection,
    CanonicalRelation, CanonicalResume, ResumeConflict, SourceAlternative, CanonicalIssue, CanonicalValidation)
from app.schemas.intelligence import ConfidenceRecord
from app.schemas.extraction import CandidateDraft,FieldEvidence,SourceReference
from app.services.resume_structure import segment,normalize,leaves,reference,heading_key
from app.services.resume_quality import source_check,check_resume
from app.services.document_intelligence import region_for

EXTRA_BUCKETS={
 'tools':'tools','programminglanguages':'programming_languages','frameworks':'frameworks','databases':'databases','cloudtechnologies':'cloud','courses':'courses','onlinecourses':'courses','awards':'awards','honors':'awards','research':'research',
 'researchinterests':'research','volunteerexperience':'volunteer','volunteerwork':'volunteer','volunteering':'volunteer',
 'leadership':'leadership','leadershipexperience':'leadership','extracurricularactivities':'extracurricular',
 'extracurricular':'extracurricular','activities':'extracurricular','languages':'languages','interests':'interests',
 'hobbies':'interests','references':'references','additionalinformation':'other','personaldetails':'other','declaration':'other'}
SUPPORTED_ROOTS={'name':'identity','email':'identity','phone':'identity','location':'identity','career_objective':'summary',
 'education':'education','experience':'experience','projects':'projects','skills':'skills','certifications':'certifications',
 'achievements':'achievements','publications':'publications','links':'links','additional_sections':'other'}


def bucket_for(section):
    heading=heading_key(section['heading'])
    if heading in EXTRA_BUCKETS:return EXTRA_BUCKETS[heading]
    field=section['field']
    if field=='career_objective':return 'objective' if 'objective' in heading else 'summary'
    if field=='experience' and 'intern' in heading:return 'internships'
    if field in {'contact','links'}:return 'identity' if field=='contact' else 'links'
    return SUPPORTED_ROOTS.get(field,'unclassified')


def raw_evidence(blocks):
    result=[]
    for index,b in enumerate(blocks):
        ocr=b.get('extraction_method')=='ocr'
        result.append(RawEvidence(evidence_id=f"ev_{b['block_id']:05d}",source_block_id=b['block_id'],source_text=b['text'],page=b.get('page'),paragraph=b.get('paragraph'),order=index,
            coordinates=b.get('ocr_region') or region_for(b),extraction_method='ocr' if ocr else 'text',
            confidence=ConfidenceRecord(label='low' if ocr and b.get('ocr_confidence',0)<.85 else 'medium' if ocr else 'high',score=b.get('ocr_confidence') if ocr else None,
                basis='OCR recognition score; not factual accuracy.' if ocr else 'Native parser evidence; semantic interpretation remains uncertain.'),
            metadata={k:b[k] for k in ('line','style','bold','cells','table_row','source_engine','source_kind','reading_order_uncertain') if k in b}))
    return result


def add_additional_sections(values,evidence,blocks):
    values=deepcopy(values);evidence=deepcopy(evidence)
    values.setdefault('additional_sections',[])
    for section in segment(blocks):
        if section['field'] not in {'unclassified','additional_sections'}:continue
        rows=[r for r in section['records'] if r['text']]
        heading=section['heading']
        if not heading or not rows:continue
        text='\n'.join(r['text'] for r in rows)[:6000]
        if len(values['additional_sections'])>=30:break
        if any(e['heading']==heading and e['content']==text for e in values['additional_sections']):continue
        index=len(values['additional_sections']);values['additional_sections'].append({'heading':heading,'content':text})
        for key,value,refs in [('heading',heading,[reference({'block':section['heading_block'],'raw':heading,'line':1})]),('content',text,[reference(r) for r in rows])]:
            evidence[f'additional_sections.{index}.{key}']=FieldEvidence(source=refs[0],references=refs,confidence='medium',source_section=section['field'],
                heading=heading,group_id=f'additional_sections:{index}',original_text=value,parser_version='3').model_dump()
    # Keep explicit URL roles/outcomes/tools separate only when the parent entry
    # and its own evidence group establish the association.
    for index,entry in enumerate(values.get('projects',[])):
        title=evidence.get(f'projects.{index}.name',{});group=title.get('group_id')
        for path,proof in list(evidence.items()):
            if not path.startswith(f'projects.{index}.') or not proof.get('source'):continue
            ref=proof['source'];source=next((b['text'] for b in blocks if b['block_id']==ref['block_id']),'')
            if group and proof.get('group_id')!=group:continue
            if path.endswith('.url') or '.links.' in path:
                url=entry.get('url') if path.endswith('.url') else entry.get('links',[])[int(path.rsplit('.',1)[1])]
                kind='repository' if re.search(r'https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org)/',url,re.I) else 'deployment_url' if re.search(r'\b(?:deployment|deployed|demo|live)\s*:',source,re.I) else None
                if kind and not entry.get(kind):entry[kind]=url;evidence[f'projects.{index}.{kind}']=deepcopy(proof)
    return CandidateDraft.model_validate(values).model_dump(),evidence


def precision_problem(path,value,text):
    if path=='email':
        actual=re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",text)
        return not any(normalize(value)==normalize(v) for v in actual)
    if path.startswith('links.') or path.endswith(('.url','.repository','.deployment_url')):
        actual=[u.rstrip('.,;)') for u in re.findall(r'(?:https?://|www\.)[^\s<>|]+',text,re.I)]
        return not any(str(value)==('https://'+u if u.startswith('www.') else u) for u in actual)
    if path.startswith('skills.') or '.technologies.' in path or '.tools.' in path:
        patterns={'react':r'(?<!\w)React(?!\w)(?!\s+(?:Native|Three Fiber))',
            'java':r'(?<!\w)Java(?!Script|\w)','c':r'(?<!\w)C(?![\w+#/])'}
        pattern=patterns.get(normalize(value))
        return pattern is not None and re.search(pattern,text,re.I) is None
    return False


def build_canonical(run_id,values,evidence,blocks,layout=(),warnings=()):
    raw=raw_evidence(blocks);by_id={e.source_block_id:e for e in raw};sections=[]
    segments=segment(blocks)
    for i,s in enumerate(segments):
        ids=list(dict.fromkeys([s['heading_block']['block_id']]+[r['block']['block_id'] for r in s['records']]))
        sections.append(CanonicalSection(section_id=f'section_{i:03d}',bucket=bucket_for(s),heading=s['heading'],original_heading=s['heading'],
            evidence_ids=[by_id[id].evidence_id for id in ids if id in by_id],source_block_ids=ids,page=s['heading_block'].get('page'),column=str(s['column']),
            detection_method='layout_supported' if s['heading_block'].get('section_hint') else 'unclassified' if bucket_for(s)=='unclassified' else 'heading' if s['heading'] else 'contact_context'))
    checked=source_check(values,evidence,blocks);bad={p['path']:p['code'] for p in checked['problems']}
    entities={};relations=[]
    for path,value in leaves(values).items():
        parts=path.split('.');root=parts[0];proof=evidence.get(path,{})
        refs=proof.get('references') or ([proof['source']] if proof.get('source') else [])
        ids=list(dict.fromkeys(r['block_id'] for r in refs if r.get('block_id') in by_id))
        sec=[s for s in sections if set(ids)&set(s.source_block_ids)]
        entity_id=f'{root}_{parts[1]}' if len(parts)>1 and parts[1].isdigit() and root in {'projects','education','experience','additional_sections','certifications','publications'} else 'identity' if root in {'name','email','phone','location'} else root
        bucket=SUPPORTED_ROOTS.get(root,'unclassified')
        if root=='additional_sections':
            heading=values[root][int(parts[1])]['heading'];bucket=EXTRA_BUCKETS.get(heading_key(heading),'unclassified')
        elif root=='career_objective' and any(s.bucket=='objective' for s in sec):bucket='objective'
        elif root=='experience' and (any(s.bucket=='internships' for s in sec) or re.search(r'\bintern(?:ship)?\b',values['experience'][int(parts[1])].get('role',''),re.I)):bucket='internships'
        elif root in {'certifications','achievements'} and sec and sec[0].bucket in {'courses','awards'}:bucket=sec[0].bucket
        if root=='skills' and sec and sec[0].bucket in {'tools','programming_languages','frameworks','databases','cloud'}:
            bucket=sec[0].bucket;entity_id='skills_'+bucket
        entity=entities.setdefault(entity_id,CanonicalEntity(entity_id=entity_id,bucket=bucket,fields={}))
        if precision_problem(path,value,'\n'.join(by_id[id].source_text for id in ids)):bad[path]='source_precision_mismatch'
        status='unsupported' if path in bad else 'needs_review' if proof.get('classification')=='ambiguous' or proof.get('confidence') in {'low','unknown',None} else 'supported'
        notes=[proof['warning']] if proof.get('warning') else []
        if path in bad:notes.append('The proposed value does not match its cited original evidence. It is withheld from the final draft.')
        if root in {'projects','education','experience'}:
            index=parts[1];anchor={'projects':'name','education':'qualification','experience':'role'}[root]
            anchor_group=evidence.get(f'{root}.{index}.{anchor}',{}).get('group_id')
            if anchor_group and proof.get('group_id') and anchor_group!=proof['group_id']:
                status='unsupported';notes.append('The source belongs to another entry group. Relationship withheld.')
        original='\n'.join(ref.get('quote','') for ref in refs)
        methods=proof.get('extraction_methods') or list(dict.fromkeys(by_id[id].extraction_method for id in ids))
        fact=CanonicalValue(fact_id='fact_'+path.replace('.','_'),profile_path=path,value=str(value) if status!='unsupported' else None,
            proposed_value=str(value) if status=='unsupported' else None,normalized_value=normalize(value) if status!='unsupported' else None,original_text=original,page=next((by_id[id].page for id in ids if by_id[id].page),None),
            evidence_ids=[by_id[id].evidence_id for id in ids],source_block_ids=ids,extraction_methods=methods,
            confidence=ConfidenceRecord(label=proof.get('confidence','unknown'),score=proof.get('ocr_confidence'),basis='Heuristic source confidence; not calibrated factual accuracy.'),
            status=status,warnings=notes,related_entities=[entity_id])
        entity.fields[path]=fact
        entity.evidence_ids=list(dict.fromkeys(entity.evidence_ids+fact.evidence_ids));entity.section_ids=list(dict.fromkeys(entity.section_ids+[s.section_id for s in sec]))
        relations.append(CanonicalRelation(source_id=entity_id,target_id=fact.fact_id,relation='has_field',evidence_ids=fact.evidence_ids,status='needs_review' if status=='unsupported' else 'supported'))
    conflicts=[]
    for i,warning in enumerate(warnings):
        if not warning.get('sources'):continue
        alternatives=[]
        for source in warning['sources']:
            value=source.get('value')
            if value is None:continue
            ref=source.get('evidence',{}).get('source') or {};id=source.get('block_id') or ref.get('block_id')
            alternatives.append(SourceAlternative(value=str(value),extraction_method=source.get('method','unknown'),source_block_ids=[id] if id in by_id else [],
                evidence_ids=[by_id[id].evidence_id] if id in by_id else [],page=source.get('page') or ref.get('page')))
        if len({a.value for a in alternatives})<2:continue
        field=warning.get('field','');conflicts.append(ResumeConflict(conflict_id=f'conflict_{i:03d}',field=field,code=warning['code'],alternatives=alternatives,message=warning['message']))
        for entity in entities.values():
            for fact in entity.fields.values():
                if fact.profile_path==field or set(fact.source_block_ids)&{id for a in alternatives for id in a.source_block_ids}:
                    fact.status='conflict' if fact.value is not None else 'unsupported';fact.warnings.append('Competing source readings are unresolved; explicit user review is required.')
    # Retain the entire source in section entities, even when a legacy projection
    # has no field for it. This also exposes unclassified/no-heading fragments.
    for section in sections:
        for entity in entities.values():
            if section.section_id in entity.section_ids:
                relations.append(CanonicalRelation(source_id=section.section_id,target_id=entity.entity_id,relation='contains',evidence_ids=[e for e in entity.evidence_ids if e in section.evidence_ids]))
    # Explicit issuer labels and shared source-linked URLs form typed relations;
    # proximity by itself never establishes an association.
    for entity in entities.values():
        if entity.bucket in {'certifications','courses'}:
            for fact in list(entity.fields.values()):
                match=re.search(r'(?:issued by|issuer\s*:)\s*(.+)',fact.value or '',re.I)
                if match:
                    issuer=fact.model_copy(deep=True);issuer.fact_id=fact.fact_id+'_issuer';issuer.profile_path=None
                    issuer.value=issuer.original_text=match[1];issuer.normalized_value=normalize(match[1]);entity.fields['issuer']=issuer
                    relations.append(CanonicalRelation(source_id=entity.entity_id,target_id=issuer.fact_id,relation='has_field',evidence_ids=issuer.evidence_ids))
        if entity.bucket=='links':
            for fact in entity.fields.values():
                for target in entities.values():
                    shared=set(fact.evidence_ids)&set(target.evidence_ids)
                    if target.bucket in {'projects','experience','education','internships'} and shared:
                        relations.append(CanonicalRelation(source_id=fact.fact_id,target_id=target.entity_id,relation='associated_with',evidence_ids=sorted(shared)))
        entity.status='conflict' if any(f.status=='conflict' for f in entity.fields.values()) else 'needs_review' if any(f.status!='supported' for f in entity.fields.values()) else 'supported'
    root=CanonicalEntity(entity_id='resume',bucket='other',fields={},evidence_ids=[r.evidence_id for r in raw],section_ids=[s.section_id for s in sections],status='supported')
    for section in sections:
        relations.append(CanonicalRelation(source_id='resume',target_id=section.section_id,relation='contains',evidence_ids=section.evidence_ids))
    graph=CanonicalResume(run_id=run_id,entities=[root,*entities.values()],sections=sections,relations=relations,conflicts=conflicts,
        warnings=['Unknown sections remain unclassified. Native spelling is never corrected from visual guesses.'])
    return graph,raw


def validate_canonical(graph,raw,values,evidence,blocks):
    graph=CanonicalResume.model_validate(graph.model_dump())
    sources={s.evidence_id:s for s in raw};issues=[];checked=supported=unsupported=0
    def issue(code,path,message,severity='needs_review',repairable=False):issues.append(CanonicalIssue(code=code,field=path,message=message,severity=severity,repairable=repairable))
    for entity in graph.entities:
        for path,fact in entity.fields.items():
            checked+=1
            valid=bool(fact.evidence_ids) and all(id in sources for id in fact.evidence_ids)
            quotes=normalize('\n'.join(sources[id].source_text for id in fact.evidence_ids if id in sources))
            if fact.value is None or not valid or not all(normalize(line).removeprefix('https://').removeprefix('http://') in quotes for line in (fact.value or '').splitlines()):
                unsupported+=1;issue('unsupported_value',path,'Value or relationship is not supported by its exact source evidence.','failure',True)
            else:supported+=1
            if fact.status in {'needs_review','conflict'}:issue('source_conflict' if fact.status=='conflict' else 'uncertain_value',path,'Review the original wording and source confidence.')
            if fact.value and path=='phone' and not 7<=len(re.sub(r'\D','',fact.value))<=15:issue('phone_format',path,'Phone length is unusual; confirm against the original.','needs_review',True)
            if fact.value and (path.endswith(('.url','.repository','.deployment_url')) or path.startswith('links.')) and not re.fullmatch(r'https?://[^\s]+',fact.value):issue('url_format',path,'URL format needs review.','failure',True)
    for relation in graph.relations:
        if not set(relation.evidence_ids)<=set(sources):issue('invalid_relation_evidence',relation.source_id,'A relationship references unknown evidence.','failure')
    reachable={'resume'}
    for _ in range(len(graph.relations)+1):
        found={r.target_id for r in graph.relations if r.source_id in reachable}
        if found<=reachable:break
        reachable.update(found)
    graph_ids={e.entity_id for e in graph.entities}|{s.section_id for s in graph.sections}|{f.fact_id for e in graph.entities for f in e.fields.values()}
    if not graph_ids<=reachable:issue('disconnected_graph','','Some canonical entities lack a path from the resume root.','failure')
    quality=check_resume(values,evidence,blocks=blocks)
    for problem in quality.issues:
        if problem.code=='low_confidence':continue
        issue(problem.code,problem.path,'Source validation flagged '+problem.code.replace('_',' ')+'.',repairable=problem.code in {'missing_source','invalid_source_reference','source_value_mismatch'})
    for field in ('name','email','education','skills'):
        if not values.get(field):issue('missing_field',field,'No supported '+field+' was detected; no value was invented.')
    for section in graph.sections:
        if section.bucket=='unclassified':issue('unclassified_section',section.section_id,'Section remains unclassified for manual review.')
    return CanonicalValidation(checked_fields=checked,supported_fields=supported,unsupported_fields=unsupported,critical_passed=not any(i.severity=='failure' for i in issues),issues=issues,
        checks={'source_fields':checked,'entity_relationships':len(graph.relations),'source_conflicts':len(graph.conflicts),'sections':len(graph.sections),'schema_errors':0})


def project_draft(graph,values,evidence):
    """Only graph-approved values enter the review projection; never the profile."""
    result=deepcopy(values);proof=deepcopy(evidence)
    for entity in graph.entities:
        for path,fact in entity.fields.items():
            if not fact.profile_path:continue
            if fact.value is not None and fact.status!='unsupported':
                if path in proof:proof[path]['canonical_fact_id']=fact.fact_id;proof[path]['evidence_ids']=fact.evidence_ids
                continue
            parts=path.split('.');node=result
            for key in parts[:-1]:node=node[int(key)] if isinstance(node,list) else node[key]
            if isinstance(node,list):node[int(parts[-1])]=''
            else:node[parts[-1]]=''
            proof.pop(path,None)
    # Compact list leaves and remove blank rows while remapping all evidence paths.
    compact={};remapped={}
    def walk(value,path='',new_path=''):
        if isinstance(value,dict):return {k:walk(v,f'{path}.{k}' if path else k,f'{new_path}.{k}' if new_path else k) for k,v in value.items()}
        if isinstance(value,list):
            output=[]
            for i,item in enumerate(value):
                if item=='' or isinstance(item,dict) and not any(item.values()):continue
                output.append(walk(item,f'{path}.{i}',f'{new_path}.{len(output)}'))
            return output
        if path in proof:remapped[new_path]=proof[path]
        return value
    compact=walk(result)
    return CandidateDraft.model_validate(compact).model_dump(),remapped


def layout_context(blocks,layout):
    """Interpret supported visual headings without changing original text/IDs."""
    from app.services.resume_structure import detect_heading
    output=deepcopy(blocks);by_id={b['block_id']:b for b in output}
    source_sections={r['block']['block_id']:s['field'] for s in segment(blocks) for r in s['records']}
    first_id=next((b['block_id'] for b in blocks if b['text'].strip()),None)
    fields={'project':'projects','projects':'projects','work experience':'experience','experience':'experience','education':'education','skills':'skills','technical skills':'skills','summary':'career_objective','objective':'career_objective','contact':'contact','research':'additional_sections','leadership':'additional_sections'}
    for group in layout:
        if group.get('method')!='vision' or group.get('status')!='supported' or group.get('block_type')!='heading' or group.get('confidence',0)<.85 or not group.get('region'):continue
        section=normalize(group.get('section','')).replace('_',' ')
        detected=detect_heading(group.get('section',''))
        field=detected[0] if detected else fields.get(section)
        if field is None or field in {'contact','links'}:continue
        for id in group.get('source_block_ids',[]):
            block=by_id.get(id)
            if id==first_id or source_sections.get(id) not in {'contact','unclassified','additional_sections'}:continue
            if not block or detect_heading(block['text']) or normalize(block['text'])!=normalize(group.get('raw_text','')):continue
            block.update(section_hint=field,layout_heading_id=group['id'])
    # Real typography can establish a boundary, but not its semantic bucket.
    sizes=[b.get('position',{}).get('font_size',0) for b in output if b['text'].strip()]
    median=sorted(sizes)[len(sizes)//2] if sizes else 0
    seen_heading=False
    for block in output:
        if detect_heading(block['text']):seen_heading=True
        if seen_heading and block['block_id']!=first_id and not detect_heading(block['text']) and not block.get('section_hint') and block.get('bold') and median and block.get('position',{}).get('font_size',0)>median*1.5 and len(block['text'].split())<=8:
            block['structural_heading']=True
    return output
