"""Versioned contracts for the local, single-owner career workspace."""
from __future__ import annotations

from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


def uid() -> str:
    return uuid4().hex


Section = Literal['contacts', 'summary', 'education', 'skills', 'experience', 'projects', 'achievements', 'languages']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Fact(Contract):
    id: str = Field(default_factory=uid, max_length=80)
    section: Section
    text: str = Field(min_length=1, max_length=3000)
    evidence: str = Field(default='', max_length=4000)
    source_refs: list[str] = Field(default_factory=list, max_length=12,
                                 description='IDs of the supplied raw source excerpts, such as T01. Never invent IDs.')
    page: int | None = Field(default=None, ge=1, le=20)
    origin: Literal['document', 'user', 'interview', 'demo'] = 'user'


class ProfileExtraction(Contract):
    """Extract candidate facts with exact evidence from the supplied resume."""
    facts: list[Fact] = Field(max_length=100)


class Requirement(Contract):
    id: str = Field(default_factory=uid, max_length=80)
    text: str = Field(min_length=1, max_length=2000)
    priority: Literal['required', 'preferred', 'unspecified'] = 'unspecified'
    category: Literal['skill', 'experience', 'responsibility', 'condition'] = 'skill'
    evidence: str = Field(min_length=1, max_length=3000)


class Vacancy(Contract):
    """Extract the employer, position and evidenced vacancy requirements."""
    title: str = Field(default='', max_length=200)
    company: str = Field(default='', max_length=200)
    requirements: list[Requirement] = Field(default_factory=list, max_length=80)


class Finding(Contract):
    requirement_id: str = Field(max_length=80)
    status: Literal['supported', 'partial', 'unknown', 'gap']
    fact_ids: list[str] = Field(default_factory=list, max_length=30)
    explanation: str = Field(min_length=1, max_length=2000)
    needs_review: bool = False


class MatchResult(Contract):
    """Explain how every requirement relates to the confirmed candidate facts."""
    findings: list[Finding] = Field(max_length=80)


class Question(Contract):
    id: str = Field(default_factory=uid)
    requirement_id: str
    text: str
    answer: str = ''
    skipped: bool = False


class ResumeChange(Contract):
    id: str = Field(default_factory=uid, max_length=80)
    fact_id: str = Field(max_length=80)
    supporting_fact_ids: list[str] = Field(default_factory=list, max_length=12)
    before: str = Field(min_length=1, max_length=3000)
    after: str = Field(min_length=1, max_length=3000)
    reason: str = Field(min_length=1, max_length=2000)
    decision: Literal['pending', 'accepted', 'rejected'] = 'pending'


class ChangeResult(Contract):
    """Propose factual resume changes and a relevant section order for review."""
    changes: list[ResumeChange] = Field(max_length=50)
    section_order: list[Section] = Field(default_factory=list, max_length=8)
    order_reason: str = Field(default='', max_length=2000)


class LetterResult(Contract):
    """Draft a cover letter grounded in identified confirmed candidate facts."""
    text: str = Field(min_length=1, max_length=12000)
    fact_ids: list[str] = Field(max_length=100)


ResearchCategory = Literal['overview', 'products', 'technology', 'team', 'news', 'interview']


class Claim(Contract):
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal['fact', 'inference']
    source_ids: list[str] = Field(min_length=1, max_length=9)
    category: ResearchCategory = 'overview'
    evidence: str = Field(default='', max_length=3000)


class ResearchCoverage(Contract):
    category: ResearchCategory
    status: Literal['sourced', 'not_found', 'search_failed', 'needs_summary']
    source_count: int = 0


class ResearchPlan(Contract):
    """Plan five focused web queries from public employer discovery snippets."""
    queries: list[str] = Field(default_factory=list, max_length=5)


class ResearchPoint(Contract):
    text: str = Field(min_length=1, max_length=1200)
    category: ResearchCategory
    kind: Literal['fact', 'inference'] = 'fact'
    evidence_ids: list[str] = Field(default_factory=list, max_length=3)


class ResearchDraft(Contract):
    """A concise employer brief grounded only in supplied excerpt IDs."""
    claims: list[ResearchPoint] = Field(default_factory=list, max_length=12)
    questions: list[str] = Field(default_factory=list, max_length=5)
    limitations: str = Field(default='', max_length=2000)


class ResearchResult(Contract):
    """Summarize employer sources, separating sourced claims from inferences."""
    claims: list[Claim] = Field(default_factory=list, max_length=30)
    questions: list[str] = Field(default_factory=list, max_length=8)
    limitations: str = Field(default='', max_length=3000)
    coverage: list[ResearchCoverage] = Field(default_factory=list, max_length=6)
    summary_pending: bool = False
    researched_at: str = ''
    report_status: Literal['complete', 'partial', 'failed', 'no_sources'] = 'complete'
    failure_code: str = ''
    retrieval_stats: dict[str, int] = Field(default_factory=dict)


class Application(Contract):
    id: str = Field(default_factory=uid)
    version: int = 0
    title: str = Field(default='Новый отклик', max_length=200)
    created_at: str = ''
    updated_at: str = ''
    demo: bool = False
    mode: Literal['example', 'live'] = 'live'
    provenance: dict[str, Literal['example', 'ai', 'user', 'assembled']] = Field(default_factory=dict)
    resume_source: str = Field(default='', max_length=60000)
    resume_pages: list[str] = Field(default_factory=list, max_length=20)
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    profile_candidates: list[Fact] = Field(default_factory=list, max_length=100)
    profile_confirmed: bool = False
    vacancy_source: str = Field(default='', max_length=60000)
    vacancy_url: str = ''
    vacancy: Vacancy = Field(default_factory=Vacancy)
    vacancy_confirmed: bool = False
    findings: list[Finding] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    interview_done: bool = False
    changes: list[ResumeChange] = Field(default_factory=list)
    section_order: list[Section] = Field(default_factory=list)
    order_reason: str = ''
    changes_generated: bool = False
    resume_text: str = Field(default='', max_length=60000)
    resume_confirmed: bool = False
    letter_text: str = Field(default='', max_length=12000)
    letter_confirmed: bool = False
    letter_tone: Literal['professional', 'warm', 'concise'] = 'professional'
    letter_length: Literal['short', 'standard'] = 'standard'
    letter_fact_ids: list[str] = Field(default_factory=list)
    research: ResearchResult | None = None
    sources: list[dict] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    stage_notices: dict[str, list[str]] = Field(default_factory=dict)
    company_site: str = Field(default='', max_length=2048)
