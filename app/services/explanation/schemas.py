from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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


class AllowedEvidenceSegment(StrictModel):
    field_name: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=600)
    evidence_snapshot: dict[str, Any]


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
    evidence_field_name: str = Field(min_length=1, max_length=80)
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

    @model_validator(mode="after")
    def validate_evidence_status(self) -> ControlledFinding:
        if self.evidence_status in {"supported", "partially_supported"} and not self.evidence:
            raise ValueError("supported finding requires evidence")
        if self.evidence_status == "evidence_insufficient" and self.evidence:
            raise ValueError("evidence insufficient finding cannot contain supporting evidence")
        if self.evidence_status not in {
            "supported",
            "partially_supported",
            "evidence_insufficient",
        }:
            raise ValueError("unknown evidence status")
        return self


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


class GroundedClaimV1(StrictModel):
    claim_type: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)
    finding_keys: list[FindingKey] = Field(max_length=100)
    citations: list[OutputCitation] = Field(max_length=16)


class InstitutionFindingExplanation(StrictModel):
    finding_key: FindingKey
    explanation: GroundedClaimV1
    why_it_matters: GroundedClaimV1
    evidence_assessment: GroundedClaimV1
    review_actions: list[GroundedClaimV1] = Field(min_length=1, max_length=10)


class InstitutionExplanationV1(StrictModel):
    schema_version: Literal["institution_explanation_v1"]
    executive_summary: GroundedClaimV1
    finding_explanations: list[InstitutionFindingExplanation]
    cross_finding_observations: list[GroundedClaimV1] = Field(max_length=20)
    manual_review_priorities: list[GroundedClaimV1] = Field(max_length=20)
    disclaimer: str = Field(min_length=1, max_length=1000)


class ConsumerRiskExplanation(StrictModel):
    finding_key: FindingKey
    plain_language_explanation: GroundedClaimV1
    what_to_check: list[GroundedClaimV1] = Field(min_length=1, max_length=10)


class ConsumerExplanationV1(StrictModel):
    schema_version: Literal["consumer_explanation_v1"]
    overall_notice: GroundedClaimV1
    risk_explanations: list[ConsumerRiskExplanation]
    questions_to_ask: list[GroundedClaimV1] = Field(max_length=20)
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
    support_type: str
    source_title: str
    source_url: str
    pilot_id: str | None
    record_type: str
    chunk_kind: str
    source_locator: dict[str, Any]
    context_scope: str
    evidence_field_name: str


class ResolvedCitationV1(StrictModel):
    citation_key: CitationKey
    finding_key: FindingKey
    support_type: str
    source_title: str
    source_url: str
    pilot_id: str | None
    record_type: Literal["regulation", "penalty", "product_document"]
    chunk_kind: str
    source_locator: dict[str, Any]
    context_scope: str
    evidence_field_name: str
    cited_quote: str
    chunk_identity_sha256: str
    chunk_content_sha256: str


class ValidatedExplanation(StrictModel):
    output: dict[str, Any]
    citations: list[ValidatedCitation]
    resolved_citations: list[ResolvedCitationV1]
