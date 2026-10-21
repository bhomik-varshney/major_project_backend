"""Responsibilities executed by the Career Orchestrator's skill-gap Flow.

Evidence transformations are local Flow tasks. Only optional guidance selection
and its independent review use LLM-backed crewai.Agent objects.
"""
CONTRACTS={
    'preparing':('Career Orchestrator','Frozen confirmed profile and job selection','Validated immutable selection','Reject unavailable sources; never use document drafts'),
    'analysing_profile':('Candidate Skill Profile Agent','Confirmed facts, original skill terms and source proofs','InventorySkill[] with path, origin, strength and unknown proficiency','Keep weak narrative mentions distinct from confirmed skill claims'),
    'analysing_requirements':('Job Requirement Analysis Agent','Frozen selected Job[]','Requirement[] and job context','Do not promote preferred skills or infer mandatory requirements'),
    'comparing_skills':('Skill Comparison and Gap Analysis Agents','InventorySkill[] and Requirement[]','SkillComparison[] and aggregated gaps','Only explicit aliases satisfy equivalence; preserve conflicts'),
    'prioritising_gaps':('Skill Priority and Career Guidance Agent','Gap evidence, corpus frequency and scorer configuration','GapRecord[] with transparent priority and impact estimates','Never mutate confirmed facts or promise employment'),
    'generating_guidance':('Recommendation Explanation Agent','Validated gap action catalog','Source-grounded guidance; optional GuidanceChoices','Provider failure retains deterministic guidance and marks partial'),
    'validating':('Skill Gap Validation Agent','Frozen sources and all structured outputs','Grounding, classification, priority and consistency checks','Reject unsupported data; disagreements remain needs_review'),
}
