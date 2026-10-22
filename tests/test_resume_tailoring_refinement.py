"""Focused regressions for prose, metric preservation, visible URLs and ATS layout."""
from copy import deepcopy
import re
import pytest
from pypdf import PdfReader
from app.core.config import Settings
from app.services.resume_tailoring_content import catalog,assemble,validate,report
from app.services.resume_tailoring_evidence import display_project_links,skill_rows
from app.services.resume_tailoring_render import compile_resume,reduce_content,populate
from test_resume_tailoring_preservation import regression


def test_natural_summary_uses_job_domain_and_confirmed_project_without_education_dump():
    facts,job=regression();job['title']='LLM Application Developer Intern';job['required_skills']=['Python','LLMs']
    source=catalog(facts,job,{});draft=assemble(facts,job,source)
    assert 'undergraduate' in draft.summary and 'agentic AI and LLM applications' in draft.summary
    assert not any(v in draft.summary for v in ('bringing ','Skills include','Targeting ','Education:','Example University','expert'))
    assert 2<=len(re.findall(r'[.!?](?:\s|$)',draft.summary))<=3
    assert any(v.startswith('projects-') for v in draft.summary_evidence)
    assert validate(draft,source,facts,job)['valid']
    draft.summary+=' Expert with 10 years of production LLM leadership.'
    assert not validate(draft,source,facts,job)['valid']


def test_percentage_result_and_implementation_survive_selection_and_fit():
    facts,job=regression();source=catalog(facts,job,{})
    # An agent omits all bullet IDs. Host still restores proven measurable results.
    draft=assemble(facts,job,source,{'entry_ids':source['selected_ids'],'bullet_ids':[]})
    ml=next(e for e in draft.entries if e.id=='experience-1')
    assert any('96.6%' in b['text'] for b in ml.bullets)
    draft=assemble(facts,job,source);final,_=reduce_content(draft,1)
    ml=next(e for e in final.entries if e.id=='experience-1')
    assert '96.6%' in ' '.join(b['text'] for b in ml.bullets)
    ml.bullets=[]
    assert any(i['field']=='experience-1.result' for i in validate(final,source,facts,job)['issues'])


def test_same_entry_implementation_and_metric_combined_without_cross_employer_claim():
    facts,job=regression()
    facts['experience'][1]['description']='Built an SMS spam-classification system with an NLP preprocessing pipeline. Compared feature representations with a Random Forest classifier, reaching 96.6% test accuracy with Bag-of-Words.'
    source=catalog(facts,job,{});draft=assemble(facts,job,source);final,_=reduce_content(draft,1)
    text=' '.join(b['text'] for e in final.entries if e.id=='experience-1' for b in e.bullets)
    assert 'Built an SMS spam-classification system; achieved 96.6%' in text
    assert not any('96.6%' in b['text'] for e in final.entries if e.id=='experience-0' for b in e.bullets)
    assert report(final,source,facts)['bullet_changes']


def test_skill_aliases_are_consolidated_but_complete_ledger_kept():
    facts,job=regression();source=catalog(facts,job,{});draft=assemble(facts,job,source)
    rows=skill_rows(draft.entries);flat=[v for values in rows.values() for v in values]
    assert 'React 19' in flat and 'React' not in flat
    assert 'Model Context Protocol (MCP)' in flat and 'MCP' not in flat
    assert 'Langfuse' in rows['Tools'] and 'Langfuse' not in rows['AI/ML and NLP']
    assert len([e for e in draft.entries if e.section=='skills'])==36
    assert report(draft,source,facts)['skill_presentation']['visible_labels']==34


def test_combined_claim_keeps_both_sources_when_outcome_is_in_a_separate_field():
    facts,job=regression()
    facts['experience'][1].update(description='Built an SMS spam-classification system with an NLP pipeline.',
        achievements=['Compared feature representations with a classifier, reaching 96.6% test accuracy.'])
    source=catalog(facts,job,{});draft=assemble(facts,job,source)
    result=next(b for e in draft.entries if e.id=='experience-1' for b in e.bullets if '96.6%' in b['text'])
    assert result['combination_sources']==['confirmed_profile.experience.1.description','confirmed_profile.experience.1.achievements.0']
    assert 'Built an SMS' in result['original'] and '96.6%' in result['original']
    assert validate(draft,source,facts,job)['valid']


def test_classified_readable_links_exact_targets_and_explicit_optional_link_omission():
    facts,job=regression();p=facts['projects'][0]
    url='https://github.com/student/'+('long_repository_name_'*5)
    p.update(repository=url,url=url,links=[url,'https://youtu.be/synthetic-demo'])
    source=catalog(facts,job,{});draft=assemble(facts,job,source)
    entry=next(e for e in draft.entries if e.id=='projects-0');shown=display_project_links(entry)
    assert len(shown)==2 and any(v['url']==url for v in shown)
    assert any('(github.com)' in v['label'] for v in shown)
    assert any(v['label'].startswith('Demo: ') for v in shown)
    assert len({v['url'] for v in entry.links})==3
    assert report(draft,source,facts)['omitted_links'][0]['url']=='https://youtu.be/synthetic-demo'
    assert all(v['label'] not in {'Repository','Deployment','Additional link','Project link'} for v in entry.links)
    assert all('github.com/' in v['label'] or 'linkedin.com/' in v['label'] or 'example.com' in v['label'] for v in draft.links)


def test_real_pdf_readable_urls_normal_traffic_text_right_aligned_dates_and_complete_history(tmp_path):
    facts,job=regression();facts['projects'][3]['description']='Designed a traffic-signal simulation using Python and React Three Fiber.'
    source=catalog(facts,job,{});draft=assemble(facts,job,source)
    final,audit=compile_resume(draft,source,facts,job,tmp_path,Settings())
    pdf=PdfReader(tmp_path/'document.pdf');text=pdf.pages[0].extract_text()
    assert len(pdf.pages)==1 and audit['quality']['valid'] and audit['quality']['body_font_pt']==11
    assert 'traffic' in text and not re.search(r'[\ufb00-\ufb06]',text)
    assert 'Adobe Systems India' in text and '96.6%' in text
    assert 'GitHub: github.com/student/' in text and 'linkedin.com/in/' in text
    assert audit['quality']['right_aligned_dates_checked']==3
    assert audit['quality']['name_centered']
    assert abs(audit['quality']['name_center_offset_pt'])<=2
    assert audit['quality']['ligature_extraction_checked'] and audit['quality']['readable_link_labels_checked']
    assert not re.search(r'confirmed_profile\.|/Users/|Project link|Additional link',text)
    assert len([e for e in final.entries if e.section=='experience'])==2
    assert len([e for e in final.entries if e.section=='projects'])==4
    assert 'Grade / CGPA: CGPI' not in text
    for row in source['entries']:
        if row['section']=='skills':assert row['source'] in {v['source_evidence'] for v in source['ledger']}


def test_new_template_neutralizes_parfill_and_keeps_supplied_class_unchanged():
    facts,job=regression();source=catalog(facts,job,{});draft=assemble(facts,job,source)
    tex=populate(draft)
    assert r'\setlength{\parfillskip}{0pt plus 1fil}' in tex
    assert 'Ligatures=NoCommon,Kerning=Off' in tex
    assert r'\begin{tabularx}' in tex and r'\raggedleft' in tex
    assert 'React Native Developer Intern | Remote' not in tex


@pytest.mark.parametrize('value',['R&D 50% $Lab #1_ {AI} ~ ^ \\', 'traffic classification and source-backed data'])
def test_special_characters_and_normal_text_are_source_checked(value):
    facts,job=regression();facts['projects'][0]['name']=value
    source=catalog(facts,job,{});draft=assemble(facts,job,source)
    assert validate(draft,source,facts,job)['valid']
    assert r'\write18' not in populate(draft)
