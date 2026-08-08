from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ExplanationError
from app.models import ExplanationArtifact, ExplanationCitation, ExplanationRun
from app.models.enums import ExplanationRunStatus, ExplanationValidationStatus
from app.services.explanation.context import ControlledRAGContextBuilder
from app.services.explanation.prompts import canonical_sha256, load_prompt, prompt_sha256
from app.services.explanation.providers import (
    ExplanationProvider,
    ProviderGenerationError,
    provider_configuration_sha256,
    provider_from_name,
)
from app.services.explanation.schemas import ExplanationProviderRequest, PromptDefinition
from app.services.explanation.validators import ControlledExplanationValidator

CITATION_ERROR_CODES = {
    "explanation_unknown_citation_key",
    "explanation_citation_wrong_finding",
    "explanation_citation_snapshot_mismatch",
    "explanation_duplicate_citation",
    "explanation_citation_too_short",
    "explanation_citation_not_substantive",
}
UNSUPPORTED_ERROR_CODES = {
    "explanation_unsupported_claim",
    "explanation_missing_uncertainty",
}
POLICY_ERROR_CODES = {
    "explanation_legal_conclusion_detected",
    "explanation_financial_advice_detected",
    "explanation_missing_product_context_disclaimer",
    "explanation_missing_disclaimer",
}


class ControlledExplanationService:
    def __init__(self) -> None:
        self.context_builder = ControlledRAGContextBuilder()
        self.validator = ControlledExplanationValidator()

    def create(
        self,
        session: Session,
        *,
        screening_run_id: int,
        audience: str,
        provider_name: str,
        retry_of_id: int | None = None,
        provider: ExplanationProvider | None = None,
    ) -> ExplanationRun:
        prompt = load_prompt(audience)
        built = self.context_builder.build(session, screening_run_id)
        if not built.payload.findings:
            raise ExplanationError("explanation_not_required")
        selected_provider = provider or self._provider(provider_name)
        if selected_provider.provider_name != provider_name:
            raise ExplanationError("explanation_provider_mismatch")
        self._validate_retry(session, retry_of_id, screening_run_id, audience)
        configuration = selected_provider.safe_configuration
        run = ExplanationRun(
            screening_run_id=screening_run_id,
            audience=audience,
            prompt_version=prompt.prompt_version,
            prompt_sha256=prompt_sha256(prompt),
            prompt_snapshot_json=prompt.model_dump(mode="json"),
            context_schema_version=built.payload.context_schema_version,
            context_payload_sha256=built.payload_sha256,
            context_payload_json=built.payload.model_dump(mode="json"),
            provider_name=selected_provider.provider_name,
            provider_model=selected_provider.provider_model,
            provider_configuration_sha256=provider_configuration_sha256(selected_provider),
            provider_configuration_json=configuration,
            status=ExplanationRunStatus.RUNNING.value,
            validation_status=ExplanationValidationStatus.PENDING.value,
            retry_of_id=retry_of_id,
        )
        session.add(run)
        session.commit()
        try:
            response = selected_provider.generate(
                ExplanationProviderRequest(
                    audience=audience,
                    prompt=prompt,
                    context=built.payload,
                )
            )
        except ProviderGenerationError as exc:
            self._fail(session, run, str(exc))
            raise ExplanationError(str(exc)) from exc
        except Exception as exc:
            self._fail(session, run, "explanation_provider_failed")
            raise ExplanationError("explanation_provider_failed") from exc

        raw_sha = hashlib.sha256(response.raw_json.encode("utf-8")).hexdigest()
        try:
            validation_prompt = PromptDefinition.model_validate(run.prompt_snapshot_json)
            validated = self.validator.validate(response.raw_json, validation_prompt, built)
        except ExplanationError as exc:
            self._reject(session, run, str(exc))
            raise
        try:
            resolved = [item.model_dump(mode="json") for item in validated.resolved_citations]
            stored_output = deepcopy(validated.output)
            artifact_payload = {
                "output_schema_version": prompt.output_schema_version,
                "prompt_sha256": run.prompt_sha256,
                "context_payload_sha256": run.context_payload_sha256,
                "provider_name": run.provider_name,
                "provider_model": run.provider_model,
                "provider_configuration_sha256": run.provider_configuration_sha256,
                "validated_output": stored_output,
                "resolved_citations": resolved,
                "citations": [
                    {
                        "citation_key": item.citation_key,
                        "finding_key": item.finding_key,
                        "chunk_identity_sha256": item.chunk_identity_sha256,
                        "chunk_content_sha256": item.chunk_content_sha256,
                        "cited_quote": item.cited_quote,
                        "quote_start_offset": item.quote_start_offset,
                        "quote_end_offset": item.quote_end_offset,
                    }
                    for item in validated.citations
                ],
            }
            artifact = ExplanationArtifact(
                explanation_run_id=run.id,
                output_schema_version=prompt.output_schema_version,
                raw_provider_response_sha256=raw_sha,
                validated_output_json=stored_output,
                resolved_citations_json=resolved,
                artifact_sha256=canonical_sha256(artifact_payload),
                disclaimer=str(validated.output["disclaimer"]),
            )
            session.add(artifact)
            session.flush()
            session.add_all(
                [
                    ExplanationCitation(
                        explanation_artifact_id=artifact.id,
                        citation_key=item.citation_key,
                        finding_key=item.finding_key,
                        finding_id=item.finding_id,
                        finding_evidence_link_id=item.finding_evidence_link_id,
                        chunk_identity_sha256=item.chunk_identity_sha256,
                        chunk_content_sha256=item.chunk_content_sha256,
                        support_type=item.support_type,
                        source_title=item.source_title,
                        source_url=item.source_url,
                        pilot_id=item.pilot_id,
                        record_type=item.record_type,
                        chunk_kind=item.chunk_kind,
                        source_locator_snapshot_json=item.source_locator,
                        context_scope=item.context_scope,
                        evidence_field_name=item.evidence_field_name,
                        cited_quote=item.cited_quote,
                        quote_start_offset=item.quote_start_offset,
                        quote_end_offset=item.quote_end_offset,
                        validation_status="passed",
                    )
                    for item in validated.citations
                ]
            )
            run.status = ExplanationRunStatus.COMPLETED.value
            run.validation_status = ExplanationValidationStatus.PASSED.value
            run.completed_at = datetime.now(UTC)
            session.commit()
            session.refresh(run)
            return run
        except Exception as exc:
            session.rollback()
            stored = session.get(ExplanationRun, run.id)
            if stored is not None:
                self._fail(session, stored, "explanation_persistence_failed")
            raise ExplanationError("explanation_persistence_failed") from exc

    def show(self, session: Session, run_id: int) -> dict[str, Any]:
        run = self._run(session, run_id)
        return {
            "explanation_run_id": run.id,
            "screening_run_id": run.screening_run_id,
            "audience": run.audience,
            "prompt_version": run.prompt_version,
            "prompt_sha256": run.prompt_sha256,
            "context_schema_version": run.context_schema_version,
            "context_payload_sha256": run.context_payload_sha256,
            "provider_name": run.provider_name,
            "provider_model": run.provider_model,
            "provider_configuration_sha256": run.provider_configuration_sha256,
            "status": run.status,
            "validation_status": run.validation_status,
            "error_code": run.error_code,
            "retry_of_id": run.retry_of_id,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }

    def artifact(self, session: Session, run_id: int) -> dict[str, Any]:
        run = self._run(session, run_id)
        if run.status != ExplanationRunStatus.COMPLETED.value or run.artifact is None:
            raise ExplanationError("explanation_artifact_not_available")
        return {
            "explanation_run_id": run.id,
            "output_schema_version": run.artifact.output_schema_version,
            "artifact_sha256": run.artifact.artifact_sha256,
            "validated_output": run.artifact.validated_output_json,
            "resolved_citations": run.artifact.resolved_citations_json,
            "disclaimer": run.artifact.disclaimer,
        }

    def citations(self, session: Session, run_id: int) -> list[dict[str, Any]]:
        run = self._run(session, run_id)
        if run.status != ExplanationRunStatus.COMPLETED.value or run.artifact is None:
            raise ExplanationError("explanation_artifact_not_available")
        return [
            {
                "citation_key": item.citation_key,
                "finding_key": item.finding_key,
                "finding_id": item.finding_id,
                "support_type": item.support_type,
                "source_title": item.source_title,
                "source_url": item.source_url,
                "pilot_id": item.pilot_id,
                "record_type": item.record_type,
                "chunk_kind": item.chunk_kind,
                "source_locator": item.source_locator_snapshot_json,
                "context_scope": item.context_scope,
                "evidence_field_name": item.evidence_field_name,
                "chunk_identity_sha256": item.chunk_identity_sha256,
                "chunk_content_sha256": item.chunk_content_sha256,
                "cited_quote": item.cited_quote,
                "quote_start_offset": item.quote_start_offset,
                "quote_end_offset": item.quote_end_offset,
                "validation_status": item.validation_status,
            }
            for item in sorted(run.artifact.citations, key=lambda value: value.citation_key)
        ]

    @staticmethod
    def _provider(name: str) -> ExplanationProvider:
        try:
            return provider_from_name(name)
        except ProviderGenerationError as exc:
            raise ExplanationError(str(exc)) from exc

    @staticmethod
    def _validate_retry(
        session: Session,
        retry_of_id: int | None,
        screening_run_id: int,
        audience: str,
    ) -> None:
        if retry_of_id is None:
            return
        previous = session.get(ExplanationRun, retry_of_id)
        if (
            previous is None
            or previous.screening_run_id != screening_run_id
            or previous.audience != audience
        ):
            raise ExplanationError("explanation_retry_invalid")

    @staticmethod
    def _reject(session: Session, run: ExplanationRun, code: str) -> None:
        run.status = ExplanationRunStatus.REJECTED.value
        run.validation_status = _validation_status(code)
        run.error_code = code
        run.completed_at = datetime.now(UTC)
        session.commit()

    @staticmethod
    def _fail(session: Session, run: ExplanationRun, code: str) -> None:
        run.status = ExplanationRunStatus.FAILED.value
        run.validation_status = ExplanationValidationStatus.PENDING.value
        run.error_code = code
        run.completed_at = datetime.now(UTC)
        session.commit()

    @staticmethod
    def _run(session: Session, run_id: int) -> ExplanationRun:
        run = session.scalar(
            select(ExplanationRun)
            .options(
                selectinload(ExplanationRun.artifact).selectinload(ExplanationArtifact.citations)
            )
            .where(ExplanationRun.id == run_id)
        )
        if run is None:
            raise ExplanationError("explanation_run_not_found")
        return run


def _validation_status(code: str) -> str:
    if code in CITATION_ERROR_CODES:
        return ExplanationValidationStatus.REJECTED_INVALID_CITATION.value
    if code in UNSUPPORTED_ERROR_CODES:
        return ExplanationValidationStatus.REJECTED_UNSUPPORTED_CLAIM.value
    if code in POLICY_ERROR_CODES:
        return ExplanationValidationStatus.REJECTED_POLICY_VIOLATION.value
    return ExplanationValidationStatus.REJECTED_INVALID_SCHEMA.value
