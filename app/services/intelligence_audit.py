"""Source validation and presentation of intermediate data; no profile writes."""
import re
from app.schemas.intelligence import ConfidenceRecord, EvidenceItem, LayoutBlock, ResumeDraft, ValidationWarning
from app.services.resume_structure import leaves, normalize, segment


def validate_layout(layout,blocks,pages):
    allowed={p['page'] for p in pages};result=[];warnings=[];used=set()
    for candidate in layout.blocks:
        item=candidate.model_dump()
        if item['page'] not in allowed or item['id'] in used:
            warnings.append(ValidationWarning(code='invalid_layout_reference',message='A duplicate or out-of-page visual block was withheld.',severity='needs_review').model_dump());continue
        used.add(item['id'])
        # IDs asserted by models do not acquire source authority. Derive lexical anchors.
        candidates=[b for b in blocks if (b.get('page') or b.get('rendered_page'))==item['page'] and b['text'].strip()]
        visible=normalize(item['raw_text'])
        matches=[b['block_id'] for b in candidates if visible and visible in normalize(b['text'])]
        if not matches and visible and visible in normalize(' '.join(b['text'] for b in candidates)):
            matches=[b['block_id'] for b in candidates if normalize(b['text']) in visible]
        item['source_block_ids']=matches[:40];item['method']='vision'
        item['status']='supported' if matches else 'needs_review'
        if matches and item['region']:
            from app.services.document_intelligence import region_for
            regions=[b.get('ocr_region') or region_for(b) for b in candidates if b['block_id'] in matches]
            regions=[r for r in regions if r]
            target=item['region']
            overlap=any(min(r['x']+r['width'],target['x']+target['width'])>max(r['x'],target['x']) and min(r['y']+r['height'],target['y']+target['height'])>max(r['y'],target['y']) for r in regions)
            if regions and not overlap:
                item['status']='needs_review'
                item['warnings'].append('The visual region does not overlap its native/OCR source location. Text support does not establish correct layout.')
                warnings.append(ValidationWarning(code='vision_geometry_unresolved',message='A visual region disagrees with the source text location. Inspect the page overlay.',severity='needs_review').model_dump())
        if not matches:
            item['warnings'].append('Visual text is not corroborated by exact/OCR evidence. It cannot create a profile fact.')
            nearby=[]
            if item['region']:
                from app.services.document_intelligence import region_for
                for b in blocks:
                    r=b.get('ocr_region') or region_for(b)
                    target=item['region']
                    if (b.get('page') or b.get('rendered_page'))==item['page'] and r and abs(r['x']-target['x'])<.15 and abs(r['y']-target['y'])<.06:
                        nearby.append({'method':b.get('extraction_method','text'),'value':b['text'],'block_id':b['block_id'],'page':b.get('page')})
            warnings.append(ValidationWarning(code='vision_text_unresolved',message='Visual and extracted evidence could not be reconciled. Both readings are retained for review.',severity='needs_review',
                sources=[{'method':'vision','value':item['raw_text'],'page':item['page'],'region':item['region']},*nearby[:8]]).model_dump())
        result.append(LayoutBlock.model_validate(item).model_dump())
    # Relations can refer only to real blocks from this result.
    ids={b['id'] for b in result}
    for item in result:
        item['related_block_ids']=[i for i in item['related_block_ids'] if i in ids and i!=item['id']]
    return result,warnings


def enrich_evidence(evidence,blocks,layout,warnings=()):
    by_id={b['block_id']:b for b in blocks}
    for path,proof in evidence.items():
        source=proof.get('source') or {};block=by_id.get(source.get('block_id'),{})
        methods=['ocr' if block.get('extraction_method')=='ocr' else 'text'] if block else []
        if proof.get('method')=='groq': methods.append('text_reasoning')
        supporting=[b for b in layout if b['method']=='vision' and b['status']=='supported' and source.get('block_id') in b['source_block_ids']]
        if supporting: methods.append('vision')
        proof['extraction_methods']=methods
        proof['layout_block_ids']=[b['id'] for b in supporting]
        if 'ocr' in methods:
            proof['ocr_confidence']=block.get('ocr_confidence')
            proof['classification']='ambiguous'
            proof['confidence']='low' if block.get('ocr_confidence',0)<.85 else 'medium'
            proof['warning']='OCR-derived evidence. Check spelling, dates and punctuation against the page preview.'
        conflicts=[w for w in warnings if w.get('sources') and (w.get('field')==path or any(s.get('block_id')==source.get('block_id') for s in w['sources']))]
        if conflicts:
            proof['classification']='ambiguous'
            proof['confidence']='low'
            proof['warning']='Source readings disagree. Compare the alternatives in the processing audit, then edit or explicitly confirm this value.'
    return evidence


def source_conflicts(blocks):
    warnings=[]
    for field,pattern in [('email',r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')]:
        candidates={}
        for b in blocks:
            for m in re.finditer(pattern,b['text']):
                candidates.setdefault(m.group().casefold(),{'value':m.group(),'method':b.get('extraction_method','text'),'block_id':b['block_id'],'page':b.get('page')})
        if len(candidates)>1:
            warnings.append(ValidationWarning(code='conflicting_contact',field=field,severity='needs_review',message='Multiple contact values appear in the source. Compare each candidate and explicitly edit/confirm the correct value.',sources=list(candidates.values())).model_dump())
    return warnings


def draft_audit(values,evidence,other_sections=(),warnings=()):
    result=[]
    for path,value in leaves(values).items():
        proof=evidence.get(path,{})
        refs=proof.get('references') or ([proof['source']] if proof.get('source') else [])
        conflicts=[s for w in warnings if w.get('field')==path or any(item.get('block_id') in {r['block_id'] for r in refs} for item in w.get('sources',[])) for s in w.get('sources',[])]
        problematic=any(w.get('field')==path and w.get('severity') in {'needs_review','failure'} for w in warnings)
        result.append(EvidenceItem(field=path,value=str(value),source_text='\n'.join(ref['quote'] for ref in refs),page=(proof.get('source') or {}).get('page'),
            source_block_ids=list(dict.fromkeys(ref['block_id'] for ref in refs)),extraction_methods=proof.get('extraction_methods',[]),
            confidence=ConfidenceRecord(label=proof.get('confidence','unknown'),score=proof.get('ocr_confidence'),basis='OCR engine score; not a factual accuracy probability.' if proof.get('ocr_confidence') is not None else 'Heuristic; not a calibrated probability.'),
            status='candidate_edited' if proof.get('method')=='user' else 'needs_review' if problematic or conflicts or not refs or proof.get('classification')=='ambiguous' else 'supported',alternatives=conflicts).model_dump())
    return ResumeDraft(values=values,evidence=result,unresolved_fields=[key for key in ('name','email','career_objective','education','skills','projects') if not values.get(key)],other_sections=list(other_sections)).model_dump()


def other_sections(blocks):
    return [dict(heading=s['heading'],text='\n'.join(r['raw'] for r in s['records'])[:6000],source_block_ids=list(dict.fromkeys(r['block']['block_id'] for r in s['records'])))
        for s in segment(blocks) if s['field'] in {'unclassified','additional_sections'} and s['heading']][:30]
