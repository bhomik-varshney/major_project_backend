"""Small, source-preserving wire payloads; full evidence stays in the private audit."""
import re
from pydantic import ValidationError
from app.schemas.extraction import AIClaim,AIProposal,CandidateDraft,EducationDraft,ExperienceDraft,ProjectDraft
from app.services.resume_structure import segment,deterministic_extract


def compact_context(blocks,layout=(),file_type='txt',source_blocks=None):
    by_id={b['block_id'] for b in blocks}
    local,proof,_=deterministic_extract(source_blocks or blocks)
    groups={}
    for evidence in proof.values():
        if not evidence.get('group_id'):continue
        refs=evidence.get('references') or ([evidence['source']] if evidence.get('source') else [])
        groups.setdefault(evidence['group_id'],set()).update(ref['block_id'] for ref in refs)
    entries=[]
    for field,anchor in [('projects','name'),('education','qualification'),('experience','role')]:
        for index,entry in enumerate(local[field]):
            evidence=proof.get(f'{field}.{index}.{anchor}',{})
            ids=groups.get(evidence.get('group_id'),set())
            if ids.intersection(by_id) and entry[anchor] and evidence.get('source'):
                entries.append({'field':field,'anchor':entry[anchor],'anchor_block_id':evidence['source']['block_id'],'ids':sorted(ids)[:40]})
    return {'document_type':file_type,'blocks':[
        {k:b[k] for k in ('block_id','text') if k in b} for b in blocks],
        'entry_groups':entries,
        'sections':[{'field':s['field'],'heading':s['heading'],'ids':list(dict.fromkeys(r['block']['block_id'] for r in s['records'] if r['block']['block_id'] in by_id))}
            for s in segment(source_blocks or blocks) if any(r['block']['block_id'] in by_id for r in s['records'])],
        'visual_groups':[{'id':b['id'],'type':b['block_type'],'section':b['section'],'ids':b['source_block_ids'],'related':b['related_block_ids']}
            for b in layout if b.get('method')=='vision' and b.get('status')=='supported' and by_id.intersection(b.get('source_block_ids',[]))][:60]}


def source_batches(blocks,max_chars=9000):
    """Never drop source text or renumber evidence IDs, even for a long paragraph."""
    batches=[];current=[];size=0
    for block in blocks:
        # Metadata is deliberately excluded from budgeting: it is never sent.
        text=block['text']
        pieces=[text[i:i+max_chars] for i in range(0,len(text),max_chars)] or ['']
        for piece in pieces:
            cost=len(piece)+32
            if current and (size+cost>max_chars or any(b['block_id']==block['block_id'] for b in current)):
                batches.append(current);current=[];size=0
            current.append({**block,'text':piece});size+=cost
    if current:batches.append(current)
    return batches


def claim_paths():
    draft=CandidateDraft().model_dump()
    records={'projects':ProjectDraft,'education':EducationDraft,'experience':ExperienceDraft}
    paths=[]
    for field,value in draft.items():
        if field=='additional_sections':continue  # Preserved by canonical section extraction.
        if field in records:
            for key,item in records[field].model_fields.items():
                paths.append(f'{field}.INDEX.{key}'+('.INDEX' if item.annotation==list[str] else ''))
        else:paths.append(field+('.INDEX' if isinstance(value,list) else ''))
    return ', '.join(paths)


def proposal_from_claims(result,blocks=None):
    """Convert bounded flat claims to the legacy typed proposal, without trusting paths."""
    values=CandidateDraft().model_dump();claims=[];withheld=0
    records={'projects':ProjectDraft,'education':EducationDraft,'experience':ExperienceDraft}
    by_id={b["block_id"]:b for b in blocks} if blocks is not None else None
    seen={}
    positions={}
    for claim in result.claims:
        parts=claim.path.split('.')
        for index in (1,3):
            if len(parts)>index and re.fullmatch(r'\d{1,2}',parts[index]):
                positions.setdefault(tuple(parts[:index]),set()).add(int(parts[index]))
    for original in result.claims:
        parts=original.path.split('.')
        for index in (3,1):
            if len(parts)>index and re.fullmatch(r'\d{1,2}',parts[index]):
                key=tuple(parts[:index])
                parts[index]=str(sorted(positions[key]).index(int(parts[index])))
        claim=original.model_copy(update={'path':'.'.join(parts)})
        parts=claim.path.split('.')
        root=parts[0]
        try:
            if root not in values or root=='additional_sections' or claim.path in seen:
                raise ValueError()
            if isinstance(values[root],list):
                if len(parts)<2 or not re.fullmatch(r'\d{1,2}',parts[1]):raise ValueError()
                index=int(parts[1]);limit=100 if root=='skills' else 20 if root in {'education','experience','preferred_roles','preferred_locations'} else 30
                if index>=limit:raise ValueError()
                if root in records:
                    if len(parts)<3 or parts[2] not in records[root].model_fields:raise ValueError()
                    model=records[root];key=parts[2]
                    while len(values[root])<=index:values[root].append(model().model_dump())
                    if model.model_fields[key].annotation==list[str]:
                        if len(parts)!=4 or not re.fullmatch(r'\d{1,2}',parts[3]) or int(parts[3])>=30:raise ValueError()
                        target=values[root][index][key];position=int(parts[3])
                        while len(target)<=position:target.append('')
                        target[position]=claim.value
                    else:
                        if len(parts)!=3:raise ValueError()
                        values[root][index][key]=claim.value
                else:
                    if len(parts)!=2:raise ValueError()
                    while len(values[root])<=index:values[root].append('')
                    values[root][index]=claim.value
            else:
                if len(parts)!=1:raise ValueError()
                values[root]=claim.value
            seen[claim.path]=claim.value
            quote=claim.value
            if by_id is not None:
                source=by_id.get(claim.block_id,{}).get('text','')
                needle=claim.value.removeprefix('https://').removeprefix('http://')
                found=re.search(r'\s+'.join(re.escape(part) for part in needle.split()),source,re.I) if needle.strip() else None
                quote=found.group() if found else ''
            claims.append(AIClaim(path=claim.path,block_id=claim.block_id,quote=quote,confidence=claim.confidence))
        except (ValueError,TypeError,IndexError):withheld+=1
    # Validate individual claims incrementally so one bad email/date/oversize value
    # cannot invalidate otherwise useful source-supported suggestions.
    try:
        return AIProposal(values=CandidateDraft.model_validate(values),claims=claims),withheld
    except ValidationError as exc:
        bad_paths={'.'.join(map(str,error['loc'])) for error in exc.errors()}
        from app.schemas.agents import ResumeClaims
        clean=[claim for claim in result.claims if not any(claim.path==path or claim.path.startswith(path+'.') for path in bad_paths)]
        if len(clean)==len(result.claims):
            return AIProposal(values=CandidateDraft(),claims=[]),withheld+len(claims)
        proposal,rejected=proposal_from_claims(ResumeClaims(claims=clean,warnings=result.warnings),blocks)
        return proposal,withheld+len(result.claims)-len(clean)+rejected
