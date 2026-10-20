"""Explicit contracts for deterministic CrewAI Flow actors and optional LLM review.

Data preparation/retrieval/ranking/calibration run as local Flow stages. Only
explanation selection and its review instantiate LLM-backed crewai.Agent objects.
The orchestrator owns dependencies, cancellation, timeout and persistence.
"""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field

CONTRACTS={
 'preparing':('Candidate Profile Preparation Agent','Confirmed ProfileInput matching fields','Normalized candidate with source skill mappings','Reject invalid profile; never include drafts or contacts'),
 'normalizing':('Job Normalization Agent','Frozen Job[] snapshot','Canonical Job[] plus duplicate IDs','Reject invalid schema; deduplicate by stable ID'),
 'calculating_baseline':('TF-IDF Baseline','Normalized candidate and complete filtered Job[]','Finite cosine scores aligned to job IDs','Fail before publication on invalid corpus'),
 'retrieving':('Multi-Method Retrieval Agent','Complete filtered corpus and confirmed query','RetrievalScores[] plus fused candidate IDs/exclusion reasons','Embedding/vector failure falls back to lexical and skill retrieval'),
 'calculating_hybrid':('Hybrid Ranking Agent','Retrieved IDs, original evidence and method scores','RankedMatch[] with weighted features and transparent penalties','Reject impossible scores and unsupported evidence'),
 'calibrating':('Score Calibration Agent','Raw hybrid scores and frozen training artifact','Monotonic empirical match_percentage and calibration metadata','Keep raw scores; mark percentage unavailable if artifact fails'),
 'generating_explanations':('Recommendation Explanation Agent','Validated score components and reason catalog','Existing grounded reason IDs; optional MatchSelections','Optional Groq failure retains validated deterministic explanations'),
 'validating':('Ranking Validation Agent','RankedMatch[] and frozen evidence','Finite score/evidence/order validation summary; optional MatchReview','Reject invalid output; retain only validated checkpoint'),
}
ORCHESTRATOR={'name':'Career Orchestrator','implementation':'crewai.Flow','responsibility':'Coordinate schema-checked dependencies, immutable snapshots, cancellation, timeout and atomic publication.'}

class MatchingTaskTrace(BaseModel):
    model_config=ConfigDict(extra='allow')
    stage:str
    name:str
    execution_id:str=Field(min_length=1)
    status:Literal['processing','completed','partial','failed','skipped']
    method:str
    inputs:dict
    output_schema:str
    depends_on:list[str]
    responsibility:str
    failure_policy:str
    elapsed_ms:int|None=Field(default=None,ge=0)
