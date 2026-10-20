"""Genuine CrewAI Flow: deterministic evidence tasks plus an optional real Crew."""
import time
from copy import deepcopy
from datetime import date
from typing import ClassVar
from app.agents.matching import CONTRACTS,MatchingTaskTrace,ORCHESTRATOR
from app.agents import runtime  # Configure telemetry before loading CrewAI.
from pydantic import BaseModel,Field,PrivateAttr
from crewai.flow.flow import Flow,start,listen

from app.db.models import new_id
from app.schemas.jobs import Job,JobFilters
from app.schemas.matching import RankedMatch,MatchReason
from app.services.match_normalization import normalize_candidate,normalize_job,deduplicate
from app.services.match_baseline import TfidfBaseline
from app.services.match_scoring import score_job,validate_ranking
from app.providers.embeddings import semantic_scores
from app.services.match_retrieval import retrieve
from app.services.match_calibration import calibrate_match
from app.services.jobs import filter_context
from app.services.match_normalization import fingerprint


class MatchingState(BaseModel):
    id:str=Field(default_factory=new_id)
    candidate:dict=Field(default_factory=dict)
    jobs:list[dict]=Field(default_factory=list)
    results:list[RankedMatch]=Field(default_factory=list)
    warnings:list[str]=Field(default_factory=list)
    tasks:list[dict]=Field(default_factory=list)
    providers:dict=Field(default_factory=dict)
    validation:dict=Field(default_factory=dict)
    duplicate_ids:list[str]=Field(default_factory=list)
    partial:bool=False


class CareerMatchingFlow(Flow[MatchingState]):
    # This pipeline persists explicit snapshots in SQL; no implicit vector memory.
    _skip_auto_memory: ClassVar[bool] = True
    _settings=PrivateAttr();_input=PrivateAttr();_factory=PrivateAttr();_manager=PrivateAttr();_owner=PrivateAttr();_deadline=PrivateAttr();_callback=PrivateAttr()
    _baseline=PrivateAttr(default=None);_baseline_scores=PrivateAttr(default_factory=list);_project_scores=PrivateAttr(default_factory=list);_interest_scores=PrivateAttr(default_factory=list)
    _semantic=PrivateAttr(default=None);_semantic_projects=PrivateAttr(default=None);_retrieval_methods=PrivateAttr(default_factory=list);_selected=PrivateAttr(default_factory=list);_retrieval=PrivateAttr(default_factory=dict)
    _started=PrivateAttr(default_factory=dict);_embedding_provider=PrivateAttr(default=None)

    def __init__(self,payload,settings,factory,manager,owner,deadline,callback=lambda kind,data:None,embedding_provider=None):
        super().__init__(initial_state=MatchingState(),name='Career Matching Orchestrator',tracing=False,memory=None,persistence=None,checkpoint=False,suppress_flow_events=True,max_method_calls=12)
        self._input,self._settings,self._factory=payload,settings,factory
        self._manager,self._owner,self._deadline,self._callback=manager,owner,deadline,callback
        self._embedding_provider=embedding_provider

    def begin(self,stage,name,method='deterministic'):
        if time.monotonic()>=self._deadline:raise TimeoutError('matching_timeout')
        self._started[stage]=time.monotonic()
        label,inputs,outputs,failure=CONTRACTS[stage]
        task=MatchingTaskTrace(stage=stage,name=label,status='processing',method=method,execution_id=new_id(),
            depends_on=[self.state.tasks[-1]['stage']] if self.state.tasks else [],
            inputs={'schema':inputs,'profile_source':'confirmed_fields_only','corpus':'frozen_filtered_snapshot'},
            output_schema=outputs,failure_policy=failure,responsibility=label)
        self.state.tasks.append(task.model_dump())
        self._callback('stage',{'stage':stage,'tasks':deepcopy(self.state.tasks)})

    def complete(self,stage,**extra):
        task=next(t for t in self.state.tasks if t['stage']==stage)
        task.update({'status':'completed','elapsed_ms':round((time.monotonic()-self._started[stage])*1000),**extra})
        MatchingTaskTrace.model_validate(task)

    @start()
    def candidate_preparation(self):
        self.begin('preparing','Candidate Profile Preparation')
        self.state.candidate=normalize_candidate(self._input['candidate'])
        self.state.warnings.extend(self.state.candidate['warnings'])
        self.complete('preparing',confirmed_fields=sorted(self._input['candidate']),skill_count=len(self.state.candidate['skill_tokens']))

    @listen(candidate_preparation)
    def job_preparation(self):
        self.begin('normalizing','Job Preparation and Normalization')
        jobs,duplicates=deduplicate([Job.model_validate(j) for j in self._input['jobs']])
        self.state.duplicate_ids=duplicates
        self.state.jobs=[normalize_job(j) for j in jobs]
        if duplicates:self.state.warnings.append(f'{len(duplicates)} duplicate listings were excluded with stable-ID selection.')
        self.complete('normalizing',eligible_jobs=len(jobs),duplicates_excluded=len(duplicates))

    @listen(job_preparation)
    def baseline_matching(self):
        self.begin('calculating_baseline','TF-IDF Baseline Matching')
        candidate=self.state.candidate;jobs=self.state.jobs
        self._baseline=TfidfBaseline([candidate['text']]+[j['text'] for j in jobs])
        vector=self._baseline.vector(candidate['text']);project=self._baseline.vector(candidate['project_experience_text']);interest=self._baseline.vector(candidate['interests_text'])
        self._baseline_scores=[];self._project_scores=[];self._interest_scores=[]
        for job in jobs:
            target=self._baseline.vector(job['text'])
            self._baseline_scores.append(self._baseline.similarity(vector,target))
            self._project_scores.append(self._baseline.similarity(project,self._baseline.vector(job['project_text'])))
            self._interest_scores.append(self._baseline.similarity(interest,target))
        self.complete('calculating_baseline',method_version=self._baseline.version,corpus_documents=self._baseline.documents,vocabulary_size=len(self._baseline.idf))

    @listen(baseline_matching)
    def multi_method_retrieval(self):
        self.begin('retrieving','Multi-Method Retrieval Agent')
        scores,projects,metadata,warning=semantic_scores(self.state.candidate,self.state.jobs,self._settings,self._factory,self._input['user_scope'],min(self._deadline-5,time.monotonic()+self._settings.matching_embedding_timeout_seconds),provider=self._embedding_provider)
        self._semantic,self._semantic_projects=scores,projects
        self.state.providers['embeddings']=metadata
        if warning:self.state.warnings.append(warning)
        if metadata['status']=='unavailable':self.state.partial=True
        self._selected,self._retrieval_methods,self._retrieval=retrieve(self.state.candidate,self.state.jobs,self._baseline_scores,scores,self._settings.matching_word_vector_path,self._input.get('retrieval_pool_size') or self._settings.matching_retrieval_pool_size)
        self.state.providers['retrieval']={'status':'active','provider':'local','model':None,'methods':self._retrieval['methods']}
        self.complete('retrieving',corpus_size=len(self.state.jobs),retrieved_count=len(self._selected),pool_limit=self._retrieval['pool_limit'],methods=list(self._retrieval['methods']))

    @listen(multi_method_retrieval)
    def hybrid_matching(self):
        self.begin('calculating_hybrid','Hybrid Ranking Agent')
        scores,projects=self._semantic,self._semantic_projects
        as_of=date.fromisoformat(self._input['as_of'])
        for i in self._selected:
            job=self.state.jobs[i]
            lexical=next(m.scores[i] for m in self._retrieval_methods if m.name==self._settings.matching_lexical_strategy)
            row=score_job(self.state.candidate,job,self._baseline_scores[i],projects[i] if projects is not None else self._project_scores[i],self._interest_scores[i],scores[i] if scores is not None else None,self._settings,as_of,lexical_score=lexical,lexical_name=self._settings.matching_lexical_strategy)
            row.method_scores={m.name:m.scores[i] if m.scores is not None else None for m in self._retrieval_methods}
            row.method_scores['bm25_raw']=next(m.raw[i] for m in self._retrieval_methods if m.name=='bm25')
            row.method_scores['lexical']=lexical
            row.retrieval=next(c for c in self._retrieval['candidates'] if c['job_id']==row.job.id)
            if self._input.get('filters'):
                row.filter_evidence=filter_context(row.job,JobFilters.model_validate(self._input['filters']))
                for source,detail in row.filter_evidence.items():
                    if detail['relation']!='not_requested':
                        row.reasons.append(MatchReason(id='search_'+source,kind='consideration',text='Search '+source+': '+detail['reason'],evidence=[{'request_path':'filters.'+source,'relation':detail['relation']}]))
                row.calculation['reason_catalog_hash']=fingerprint([r.model_dump() for r in row.reasons])
            if row.retrieval.get('zero_signal'):row.warnings.append('No positive retrieval signal: included for complete review of this small filtered pool, not established suitability.')
            row.calculation['lexical_strategy']=self._settings.matching_lexical_strategy
            row.calculation['baseline_method']={'version':self._baseline.version,'corpus_documents':self._baseline.documents,'vocabulary_size':len(self._baseline.idf),
                'tf':'1 + ln(count)','idf':'ln((1 + corpus_documents) / (1 + document_frequency)) + 1','normalization':'L2','cosine_scale':100}
            self.state.results.append(row)
        self.state.results.sort(key=lambda r:(-r.hybrid_score,-r.baseline_score,r.job.id))
        for rank,row in enumerate(self.state.results,1):row.rank=rank
        self.complete('calculating_hybrid',scored_jobs=len(self.state.results),semantic_status=self.state.providers['embeddings']['status'])

    @listen(hybrid_matching)
    def score_calibration(self):
        self.begin('calibrating','Score Calibration Agent',method='empirical_isotonic')
        for row in self.state.results:
            row.match_percentage,row.calibration=calibrate_match(row)
        unavailable=any(r.calibration.get('status')=='unavailable' for r in self.state.results)
        if unavailable:
            self.state.partial=True
            self.state.warnings.append('Experimental calibration is unavailable; valid raw scores and rankings were retained.')
        state='partial' if unavailable else 'skipped' if self.state.results and all(r.match_percentage is None for r in self.state.results) else 'completed'
        self.complete('calibrating',calibrated_jobs=sum(r.match_percentage is not None for r in self.state.results),status=state,threshold_basis='raw_hybrid_score')

    @listen(score_calibration)
    def recommendation_explanations(self):
        self.begin('generating_explanations','Recommendation Explanation',method='grounded_rules')
        # Preserve validated deterministic results before any optional model request.
        self.state.validation={**validate_ranking(self.state.results,self.state.candidate,0,{j['original']['id']:j for j in self.state.jobs}),'retrieval':self._retrieval,'calibration':{'status':'experimental','method':'monotonic empirical synthetic label frequency','threshold_basis':'raw_hybrid_score'}}
        self._callback('checkpoint',self.export())
        if self._input.get('use_ai'):
            from app.crews.matching import review_recommendations
            try:
                selected=[r for r in self.state.results if r.hybrid_score>=self._input['minimum_score']]
                if not selected:
                    self.state.providers['ai_review']={'status':'skipped','model':None,'reviewed_jobs':0,'reason':'no_recommendations_above_threshold'}
                    self.complete('generating_explanations',method='grounded_rules',reviewed_jobs=0)
                    return
                if not self._manager.configured:
                    from app.providers.groq import ProviderUnavailable
                    raise ProviderUnavailable('groq_not_configured','No Groq keys are configured for optional matching review.')
                self.state.providers['ai_review']=review_recommendations(selected,self._settings,self._manager,self._owner,self._deadline)
                self.complete('generating_explanations',method='crewai.Crew',model=self._settings.text_model,reviewed_jobs=self.state.providers['ai_review']['reviewed_jobs'])
            except Exception as exc:
                code=getattr(exc,'code','matching_ai_failed')
                self.state.partial=True
                self.state.providers['ai_review']={'status':'unavailable','model':self._settings.text_model,'error_code':code,'reviewed_jobs':0}
                self.state.warnings.append('Optional AI explanation review could not complete ('+code+'). Stored source-grounded explanations and numeric scores were retained.')
                self.complete('generating_explanations',status='partial',error_code=code,method='grounded_rules')
        else:
            self.state.providers['ai_review']={'status':'not_selected','model':None,'reviewed_jobs':0}
            self.complete('generating_explanations',method='grounded_rules')

    @listen(recommendation_explanations)
    def ranking_validation(self):
        self.begin('validating','Ranking Validation')
        self.state.validation={**validate_ranking(self.state.results,self.state.candidate,0,{j['original']['id']:j for j in self.state.jobs}),'retrieval':self._retrieval,'calibration':{'status':'experimental','method':'monotonic empirical synthetic label frequency','threshold_basis':'raw_hybrid_score'}}
        self.complete('validating',checked=len(self.state.results),unsupported_reasons=0)
        return self.export()

    def export(self):
        selected_ids={r.job.id for r in self.state.results}
        return {'candidate':self.state.candidate,'normalized_jobs':{j['original']['id']:{k:v for k,v in j.items() if k!='original'} for j in self.state.jobs if j['original']['id'] in selected_ids},
            'results':[r.model_dump(mode='json') for r in self.state.results],'warnings':list(dict.fromkeys(self.state.warnings)),
            'tasks':deepcopy(self.state.tasks),'provider_metadata':deepcopy(self.state.providers),'validation':deepcopy(self.state.validation),'duplicate_ids':self.state.duplicate_ids,
            'status':'partial' if self.state.partial else 'completed','engine':'crewai.Flow','orchestrator':ORCHESTRATOR}
