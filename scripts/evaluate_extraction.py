"""Deterministic, synthetic-only evaluation. Run: python -m scripts.evaluate_extraction."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import AppError
from app.services.resume_text import extract_text
from app.services.resume_structure import deterministic_extract, leaves, normalize

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/resumes'

def at(value, path):
    try:
        for key in path.split('.'):
            value = value[int(key)] if isinstance(value,list) else value[key]
        return value
    except (KeyError,IndexError,TypeError):
        return ''


def evaluate():
    totals = dict(resumes=0, fields_evaluated=0, correct=0, missing=0, incorrect=0,
        extracted_leaves_checked=0, unsupported_leaves=0, unsupported_projects=0, unsupported_technology_associations=0, unexpected_skills=0)
    rows = []
    with TemporaryDirectory(prefix='careeros-evaluation-') as directory:
        settings = Settings(app_env='test', document_storage_path=Path(directory), groq_api_key='')
        for case in json.loads((FIXTURES/'cases.json').read_text()):
            totals['resumes'] += 1
            row = {'resume':case['file'], 'correct':0,'missing':0,'incorrect':0,'issues':[]}
            content = (FIXTURES/case['file']).read_bytes()
            try:
                blocks, _, text_warnings = extract_text(content,case['file'],str(uuid4()),settings)
                values, evidence, warnings = deterministic_extract(blocks)
            except AppError as exc:
                totals['fields_evaluated'] += 1
                key = 'correct' if exc.code == case.get('error') else 'incorrect'
                totals[key] += 1; row[key] += 1
                row['issues'].append(exc.code)
                rows.append(row); continue
            if case.get('error'):
                totals['incorrect'] += 1; row['incorrect'] += 1; row['issues'].append('Expected extraction failure')
            expected = dict(case['expected'])
            for index, name in enumerate(case['project_names']):
                expected[f'projects.{index}.name'] = name
                expected[f'projects.{index}.technologies'] = case['project_technologies'][index]
            expected['projects_count'] = len(case['project_names'])
            if case.get('skills') is not None:
                expected['skills'] = case['skills']
            for path, wanted in expected.items():
                actual = len(values['projects']) if path == 'projects_count' else at(values,path)
                equal = {normalize(v) for v in actual} == {normalize(v) for v in wanted} if isinstance(wanted,list) and isinstance(actual,list) else actual == wanted
                key = 'correct' if equal else 'missing' if not actual and wanted else 'incorrect'
                totals['fields_evaluated'] += 1; totals[key] += 1; row[key] += 1
                if not equal:
                    row['issues'].append(f'{path}: expected {wanted!r}; found {actual!r}')
            if case.get('warning') and not any(case['warning'].lower() in w.lower() for w in warnings + text_warnings):
                totals['incorrect'] += 1; row['incorrect'] += 1; row['issues'].append('Expected uncertainty/missing-field warning')
            actual_names = [p['name'] for p in values['projects']]
            totals['unsupported_projects'] += len(set(actual_names) - set(case['project_names']))
            expected_tech = dict(zip(case['project_names'],case['project_technologies']))
            for project in values['projects']:
                totals['unsupported_technology_associations'] += len(set(project['technologies']) - set(expected_tech.get(project['name'],[])))
            if case.get('skills') is not None:
                totals['unexpected_skills'] += len({normalize(v) for v in values['skills']} - {normalize(v) for v in case['skills']})
            for path,value in leaves(values).items():
                totals['extracted_leaves_checked'] += 1
                proof = evidence.get(path,{})
                refs = proof.get('references') or ([proof['source']] if proof.get('source') else [])
                quote = '\n'.join(ref['quote'] for ref in refs)
                supported = all(normalize(piece).removeprefix('https://').removeprefix('http://') in normalize(quote) for piece in str(value).splitlines())
                valid_refs = all(any(block['block_id'] == ref['block_id'] and normalize(ref['quote']) in normalize(block['text']) for block in blocks) for ref in refs)
                if not refs or not supported or not valid_refs:
                    totals['unsupported_leaves'] += 1
                    row['issues'].append(f'Unsupported source leaf: {path}')
            rows.append(row)
    return totals,rows


def report(totals, rows):
    text = '# Resume extraction evaluation\n\n15 synthetic fixtures; deterministic local extraction only. This is a regression evaluation, not a representative resume-accuracy benchmark. No personal data or provider calls.\n\n'
    text += '| Metric | Count |\n| --- | --- |\n' + ''.join(f'| {key.replace("_"," ")} | {value} |\n' for key,value in totals.items())
    text += '\nFields evaluated are explicit oracle checks (including grouped project names/technology lists, skill sets, empty states, and scanned-file rejection); every nonempty extracted leaf is separately checked against real source quotes. Project existence and per-project technologies are checked against entry-specific expected sets. Lexical anchoring alone does not prove semantic correctness.\n\n'
    text += '| Fixture | Correct | Missing | Incorrect |\n| --- | --- | --- | --- |\n' + ''.join(f'| {row["resume"]} | {row["correct"]} | {row["missing"]} | {row["incorrect"]} |\n' for row in rows)
    text += '\nCommon limitations: unconventional unlabeled employer/institution layouts can be missed; unknown technologies in prose are deliberately omitted unless explicitly listed; ambiguous columns retain uncertainty; no OCR; arbitrary visual tables and nested project layouts need more evaluation. Title grouping is heuristic. Confidence is not calibrated. No perfect-accuracy claim is made.\n\nImprovements exercised: heading aliases/numbering/punctuation/letter spacing; complete Objective/Profile statements; project bullet grouping and technology isolation; multiple work entries and explicit dates; table education; absence handling; skill deduplication; page/paragraph/position anchors.\n\nReproduce with `python -m scripts.evaluate_extraction`. Regenerate synthetic source fixtures with `python tests/fixtures/resumes/build_fixtures.py`.\n'
    for row in rows:
        if row['issues']:
            text += '\n' + row['resume'] + ': ' + '; '.join(row['issues']) + '\n'
    return text

if __name__ == '__main__':
    totals,rows = evaluate()
    (ROOT/'docs/extraction-evaluation.md').write_text(report(totals,rows))
    print(json.dumps(totals,indent=2))
    for row in rows:
        if row['missing'] or row['incorrect'] or any('Unsupported' in issue for issue in row['issues']):
            print(row)
