"""Deterministic quality checks always run, independently of model assertions."""
from collections import Counter
import re

from app.schemas.agents import ResumeQualityReport, QualityIssue
from app.services.resume_structure import leaves, normalize


def check_resume(values, evidence):
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
    return ResumeQualityReport(issues=issues[:200],warnings=list(dict.fromkeys(warnings))[:30])
