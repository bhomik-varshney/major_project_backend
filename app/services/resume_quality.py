"""Deterministic quality checks always run, independently of model assertions."""
from collections import Counter
import re

from app.schemas.agents import ResumeQualityReport, QualityIssue
from app.services.resume_structure import leaves, normalize


def source_check(values,evidence,blocks):
    """Check every draft leaf against real block/quote spans, not model labels."""
    by_id={b['block_id']:b for b in blocks};supported=0;problems=[];edited=0
    for path,value in leaves(values).items():
        proof=evidence.get(path,{})
        if proof.get('method')=='user':
            edited+=1
            continue
        refs=proof.get('references') or ([proof['source']] if proof.get('source') else [])
        valid=bool(refs) and all(ref.get('block_id') in by_id and ref.get('quote','').strip() and normalize(ref['quote']) in normalize(by_id[ref['block_id']]['text']) for ref in refs)
        quotes=normalize('\n'.join(ref.get('quote','') for ref in refs))
        if not valid:problems.append({'path':path,'code':'invalid_source_reference'})
        elif not all(normalize(line).removeprefix('https://').removeprefix('http://') in quotes for line in str(value).splitlines()):
            problems.append({'path':path,'code':'source_value_mismatch'})
        else:supported+=1
    return {'supported_fields':supported,'unsupported_fields':len(problems),'candidate_edited_fields':edited,'problems':problems,
        'scope':'Literal source support only; this does not establish factual truth or candidate confirmation.'}


def check_resume(values, evidence, blocks=None):
    issues,warnings=[],[]
    for field in ('skills','projects'):
        labels=[normalize(v['name'] if isinstance(v,dict) else v) for v in values.get(field,[])]
        if any(count>1 for count in Counter(labels).values()):
            issues.append(QualityIssue(path=field,code='duplicate_entries'))
            warnings.append(f'Duplicate {field} entries require review; no entries were silently removed by validation.')
    for field in ('education','experience','projects'):
        for i,entry in enumerate(values.get(field,[])):
            dates=re.findall(r'\b(?:19|20)\d{2}\b',entry.get('period',''))
            if len(dates)>=2 and int(dates[0])>int(dates[1]):
                issues.append(QualityIssue(path=f'{field}.{i}.period',code='contradictory_dates'))
                warnings.append(f'{field.capitalize()} entry {i+1}: written dates appear reversed. Confirm or correct them; no dates were changed.')
    for path,value in leaves(values).items():
        proof=evidence.get(path,{})
        if proof.get('confidence') in {'low','unknown'} or proof.get('classification')=='ambiguous':
            issues.append(QualityIssue(path=path,code='low_confidence'))
        if not proof.get('source') and proof.get('method')!='user':
            issues.append(QualityIssue(path=path,code='missing_source'))
    if any(issue.code=='low_confidence' for issue in issues):
        warnings.append('Low-confidence fields require manual review; confidence is heuristic, not a correctness guarantee.')
    if blocks is not None:
        checks=source_check(values,evidence,blocks)
        issues.extend(QualityIssue(**problem) for problem in checks['problems'])
        if checks['problems']:warnings.append('Some draft values do not match their cited source spans. Correct or deselect them before confirmation.')
    return ResumeQualityReport(issues=issues[:200],warnings=list(dict.fromkeys(warnings))[:30])
