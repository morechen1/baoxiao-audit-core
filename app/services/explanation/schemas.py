from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CitationKey = Annotated[str, StringConstraints(pattern=r"^E[0-9]{3}$")]
FindingKey = Annotated[str, StringConstraints(pattern=r"^F[0-9]{3}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptDefinition(StrictModel):
    prompt_version: str = Field(min_length=1, max_length=80)
    audience: Literal["institution", "consumer"]
    system_instruction: str = Field(min_length=20, max_length=4000)
    allowed_claim_types: tuple[str, ...] = Field(min_length=1)
    forbidden_claim_patterns: tuple[str, ...] = Field(min_length=1)
    required_disclaimer: str = Field(min_length=10, max_length=1000)
    citation_format: str = Field(min_length=1, max_length=200)
    output_schema_version: Literal["institution_explanation_v1", "consumer_explanation_v1"]
    uncertainty_instructions: str = Field(min_length=10, max_length=1000)
    insufficient_evidence_instructions: str = Field(min_length=10, max_length=1000)
    illustrative_product_disclaimer: str = Field(min_length=10, max_length=1000)


class PromptRegistry(StrictModel):
    prompts: tuple[PromptDefinition, ...] = Field(min_length=2)


class ControlledEvidence(StrictModel):
    citation_key: CitationKey
    support_type: str
    source_title: str
    source_url: str
    pilot_id: str | None
    record_type: Literal["regulation", "penalty", "product_document"]
    chunk_kind: str
    quote: str = Field(min_length=1, max_length=600)
    source_locator: dict[str, Any]
    evidence_references: list[dict[str, Any]]
    context_scope: str
    chunk_identity_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    chunk_content_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    truncated: bool = False


class ControlledFinding(StrictModel):
    finding_key: FindingKey
    rule_id: str
    category: str
    severity: str
    signal_strength: str
    matched_text: str
    raw_start_offset: int = Field(ge=0)
    raw_end_offset: int = Field(gt=0)
    deterministic_explanation: str
    review_question: str
    evidence_status: str
    evidence: list[ControlledEvidence] = Field(max_length=4)


class ControlledRAGContext(StrictModel):
    context_schema_version: Literal["controlled_rag_context_v1"]
    screening_run_payload_sha256: str
    trusted_index_payload_hash: str
    material: dict[str, Any]
    findings: list[ControlledFinding]
    truncated: bool = False


class OutputCitation(StrictModel):
    citation_key: CitationKey
    cited_quote: str = Field(min_length=1, max_length=600)


class InstitutionFindingExplanation(StrictModel):
    finding_key: FindingKey
    explanation: str = Field(min_length=1, max_length=3000)
    why_it_matters: str = Field(min_length=1, max_length=2000)
    evidence_assessment: str = Field(min_length=1, max_length=2000)
    review_actions: list[str] = Field(min_length=1, max_length=10)
    citations: list[OutputCitation] = Field(min_length=1, max_length=4)


class InstitutionExplanationV1(StrictModel):
    schema_version: Literal["institution_explanation_v1"]
    executive_summary: str = Field(min_length=1, max_length=4000)
    finding_explanations: list[InstitutionFindingExplanation]
    cross_finding_observations: list[str] = Field(max_length=20)
    manual_review_priorities: list[str] = Field(max_length=20)
    disclaimer: str = Field(min_length=1, max_length=1000)


class ConsumerRiskExplanation(StrictModel):
    finding_key: FindingKey
    plain_language_explanation: str = Field(min_length=1, max_length=3000)
    what_to_check: list[str] = Field(min_length=1, max_length=10)
    citations: list[OutputCitation] = Field(min_length=1, max_length=4)


class ConsumerExplanationV1(StrictModel):
    schema_version: Literal["consumer_explanation_v1"]
    overall_notice: str = Field(min_length=1, max_length=4000)
    risk_explanations: list[ConsumerRiskExplanation]
    questions_to_ask: list[str] = Field(max_length=20)
    evidence_links: list[OutputCitation] = Field(max_length=100)
    disclaimer: str = Field(min_length=1, max_length=1000)


ExplanationOutput = InstitutionExplanationV1 | ConsumerExplanationV1


class ExplanationProviderRequest(StrictModel):
    audience: Literal["institution", "consumer"]
    prompt: PromptDefinition
    context: ControlledRAGContext


class ExplanationProviderResponse(StrictModel):
    raw_json: str = Field(max_length=200_000)


class ValidatedCitation(StrictModel):
    citation_key: CitationKey
    finding_key: FindingKey
    finding_id: int
    finding_evidence_link_id: int
    chunk_identity_sha256: str
    chunk_content_sha256: str
    cited_quote: str
    quote_start_offset: int
    quote_end_offset: int


class ValidatedExplanation(StrictModel):
    output: dict[str, Any]
    citations: list[ValidatedCitation]
