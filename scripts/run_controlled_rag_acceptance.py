from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ExplanationError
from app.models import (
    ExplanationArtifact,
    ExplanationRun,
    KnowledgeChunk,
    MarketingMaterial,
    ScreeningRun,
)
from app.services.explanation import ControlledExplanationService, ControlledRAGContextBuilder
from app.services.explanation.context import BuiltContext
from app.services.explanation.prompts import canonical_sha256, load_prompt, prompt_sha256
from app.services.explanation.providers import DeterministicFixtureProvider


def _runs(session: Session) -> list[ScreeningRun]:
    rows = list(
        session.scalars(
            select(ScreeningRun)
            .join(MarketingMaterial)
            .where(ScreeningRun.status == "completed")
            .order_by(MarketingMaterial.input_sha256, ScreeningRun.run_payload_sha256)
        )
    )
    by_material: dict[str, ScreeningRun] = {}
    for run in rows:
        by_material.setdefault(run.material.input_sha256, run)
    return [by_material[key] for key in sorted(by_material)]


def _material_key(run: ScreeningRun) -> str:
    return run.material.input_sha256


def _two_finding_context(contexts: list[tuple[ScreeningRun, BuiltContext]]) -> BuiltContext | None:
    selected = [item for item in contexts if any(row.evidence for row in item[1].payload.findings)]
    if len(selected) < 2:
        return None
    rows = []
    bindings = {}
    citation_ordinal = 1
    for finding_ordinal, (_run, built) in enumerate(selected[:2], start=1):
        source = next(row for row in built.payload.findings if row.evidence)
        finding_key = f"F{finding_ordinal:03d}"
        evidence_rows = []
        for evidence in source.evidence:
            citation_key = f"E{citation_ordinal:03d}"
            evidence_rows.append(evidence.model_copy(update={"citation_key": citation_key}))
            bindings[citation_key] = replace(
                built.bindings[evidence.citation_key],
                finding_key=finding_key,
                citation_key=citation_key,
            )
            citation_ordinal += 1
        rows.append(
            source.model_copy(update={"finding_key": finding_key, "evidence": evidence_rows})
        )
    payload = selected[0][1].payload.model_copy(update={"findings": rows})
    return BuiltContext(payload, canonical_sha256(payload.model_dump(mode="json")), bindings)


def _execute_database(database_url: str) -> dict[str, Any]:
    engine = create_engine(database_url)
    contexts: dict[str, str] = {}
    artifacts: dict[str, dict[str, str]] = {}
    with Session(engine, expire_on_commit=False) as session:
        runs = _runs(session)
        if len(runs) != 60:
            raise SystemExit(f"controlled_rag_screening_sample_count_invalid:{len(runs)}")
        builder = ControlledRAGContextBuilder()
        service = ControlledExplanationService()
        for run in runs:
            key = _material_key(run)
            built = builder.build(session, run.id)
            contexts[key] = built.payload_sha256
            artifacts[key] = {}
            for audience in ("institution", "consumer"):
                explanation = service.create(
                    session,
                    screening_run_id=run.id,
                    audience=audience,
                    provider_name="deterministic_fixture",
                )
                if explanation.artifact is None:
                    raise SystemExit("controlled_rag_artifact_missing")
                artifacts[key][audience] = explanation.artifact.artifact_sha256
        first = runs[0]
        rerun = service.create(
            session,
            screening_run_id=first.id,
            audience="institution",
            provider_name="deterministic_fixture",
        )
        deterministic_rerun = (
            rerun.artifact is not None
            and rerun.artifact.artifact_sha256 == artifacts[_material_key(first)]["institution"]
        )
        prompt_snapshot_stable = all(
            canonical_sha256(run.prompt_snapshot_json) == run.prompt_sha256
            for run in session.scalars(select(ExplanationRun))
        )
        context_snapshot_stable = all(
            canonical_sha256(run.context_payload_json) == run.context_payload_sha256
            for run in session.scalars(select(ExplanationRun))
        )
        regulatory_case_citations = sum(
            str(evidence.record_type) == "regulatory_case"
            for run in runs
            for finding in builder.build(session, run.id).payload.findings
            for evidence in finding.evidence
        )
        active_knowledge_chunks = session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.is_active.is_(True))
        )
        finding_count = sum(len(run.findings) for run in runs)
        evidence_link_count = sum(
            len(finding.evidence_links) for run in runs for finding in run.findings
        )
    engine.dispose()
    return {
        "sample_count": len(contexts),
        "contexts": contexts,
        "artifacts": artifacts,
        "deterministic_rerun": deterministic_rerun,
        "historical_prompt_snapshot_stability": prompt_snapshot_stable,
        "historical_context_snapshot_stability": context_snapshot_stable,
        "regulatory_case_citation_count": regulatory_case_citations,
        "trusted_knowledge_chunk_count": int(active_knowledge_chunks or 0),
        "screening_finding_count": finding_count,
        "reviewed_evidence_link_count": evidence_link_count,
    }


def _execute_fixture_corpus(database_url: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    engine = create_engine(database_url)
    results: list[dict[str, Any]] = []
    with Session(engine, expire_on_commit=False) as session:
        runs = _runs(session)
        builder = ControlledRAGContextBuilder()
        contexts = [(run, builder.build(session, run.id)) for run in runs]
        fallback = next((item for item in contexts if item[1].payload.findings), None)
        multi_finding = next(
            (
                item
                for item in contexts
                if len([finding for finding in item[1].payload.findings if finding.evidence]) >= 2
            ),
            None,
        )
        if multi_finding is None:
            combined = _two_finding_context(contexts)
            if combined is not None:
                multi_finding = (fallback[0], combined) if fallback is not None else None
        partial = next(
            (
                item
                for item in contexts
                if any(
                    finding.evidence_status in {"partially_supported", "evidence_insufficient"}
                    for finding in item[1].payload.findings
                )
            ),
            None,
        )
        illustrative = next(
            (
                item
                for item in contexts
                if any(
                    evidence.context_scope == "illustrative_not_material_specific"
                    for finding in item[1].payload.findings
                    for evidence in finding.evidence
                )
            ),
            None,
        )
        if fallback is None or multi_finding is None or partial is None or illustrative is None:
            raise SystemExit("controlled_rag_rejection_fixture_missing")
        for sample in samples:
            scenario = str(sample["scenario"])
            expected = str(sample["expected"])
            selected = (
                multi_finding
                if scenario == "wrong_finding"
                else partial
                if scenario in {"missing_uncertainty", "insufficient_hidden"}
                else illustrative
                if scenario in {"missing_product_disclaimer", "missing_illustrative"}
                else fallback
            )
            run, built = selected
            service = ControlledExplanationService()
            service.context_builder.build = lambda *_args, value=built: value  # type: ignore[method-assign]
            actual = "passed"
            created_run: ExplanationRun | None
            try:
                created_run = service.create(
                    session,
                    screening_run_id=run.id,
                    audience=str(sample["audience"]),
                    provider_name="deterministic_fixture",
                    provider=DeterministicFixtureProvider(scenario),
                )
            except ExplanationError as exc:
                actual = str(exc)
                created_run = session.scalar(
                    select(ExplanationRun)
                    .where(ExplanationRun.screening_run_id == run.id)
                    .order_by(ExplanationRun.id.desc())
                    .limit(1)
                )
                if created_run is None:
                    raise SystemExit("controlled_rag_eval_run_missing") from exc
            assert created_run is not None
            artifact_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(ExplanationArtifact)
                    .where(ExplanationArtifact.explanation_run_id == created_run.id)
                )
                or 0
            )
            passed = actual == expected
            results.append(
                {
                    "sample_id": sample["id"],
                    "audience": sample["audience"],
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                    "explanation_run_status": created_run.status,
                    "validation_status": created_run.validation_status,
                    "error_code": created_run.error_code,
                    "artifact_count": artifact_count,
                    "scenario": scenario,
                }
            )
            if not passed or (expected != "passed" and artifact_count != 0):
                raise SystemExit(
                    f"controlled_rag_eval_mismatch:{sample['id']}:{expected}:{actual}:{artifact_count}"
                )
    engine.dispose()
    valid = [item for item in results if item["expected"] == "passed"]
    invalid = [item for item in results if item["expected"] != "passed"]
    return {
        "results": results,
        "valid_executed": len(valid),
        "valid_passed": sum(item["passed"] for item in valid),
        "invalid_executed": len(invalid),
        "invalid_rejected": sum(
            item["explanation_run_status"] in {"rejected", "failed"} for item in invalid
        ),
        "invalid_error_code_match_count": sum(item["passed"] for item in invalid),
        "rejected_artifact_count": sum(item["artifact_count"] for item in invalid),
        "uncited_claim_rejection_count": sum(
            item["scenario"] in {"no_citations", "uncited_claim", "executive_uncited"}
            and item["passed"]
            for item in invalid
        ),
        "trivial_quote_rejection_count": sum(
            item["scenario"] == "trivial_quote" and item["passed"] for item in invalid
        ),
        "metadata_quote_rejection_count": sum(
            item["scenario"] in {"metadata_quote", "metadata_pilot_id", "metadata_url"}
            and item["passed"]
            for item in invalid
        ),
    }


def _exercise_evidence_insufficient(database_url: str) -> bool:
    engine = create_engine(database_url)
    with Session(engine, expire_on_commit=False) as session:
        run = _runs(session)[0]
        built = ControlledRAGContextBuilder().build(session, run.id)
        finding = built.payload.findings[0].model_copy(
            update={"evidence_status": "evidence_insufficient", "evidence": []}
        )
        payload = built.payload.model_copy(update={"findings": [finding]})
        insufficient = BuiltContext(payload, canonical_sha256(payload.model_dump(mode="json")), {})
        service = ControlledExplanationService()
        service.context_builder.build = lambda *_args: insufficient  # type: ignore[method-assign]
        explanation = service.create(
            session,
            screening_run_id=run.id,
            audience="institution",
            provider_name="deterministic_fixture",
            provider=DeterministicFixtureProvider("evidence_insufficient"),
        )
        result = (
            explanation.status == "completed"
            and explanation.artifact is not None
            and not explanation.artifact.citations
            and "当前证据不足以作出结论"
            in json.dumps(explanation.artifact.validated_output_json, ensure_ascii=False)
        )
    engine.dispose()
    return result


def _execute_fixture_corpus_offline(samples: list[dict[str, Any]]) -> dict[str, Any]:

    from app.services.explanation.prompts import load_prompt
    from app.services.explanation.providers import DeterministicFixtureProvider
    from app.services.explanation.schemas import ExplanationProviderRequest
    from app.services.explanation.validators import ControlledExplanationValidator
    from tests.unit.test_controlled_explanations import _built_context

    results: list[dict[str, Any]] = []
    for sample in samples:
        scenario = str(sample["scenario"])
        expected_status = "passed" if sample["expected"] == "passed" else sample["expected"]
        audience = str(sample["audience"])
        prompt = load_prompt(audience)
        built = _built_context()
        request = ExplanationProviderRequest(
            audience=audience, prompt=prompt, context=built.payload
        )
        actual_status: str
        actual_error_code: str | None = None
        run_status: str
        artifact_count = 0
        if scenario in {"provider_timeout", "provider_exception"}:
            try:
                DeterministicFixtureProvider(scenario).generate(request)
                actual_status = "passed"
                actual_error_code = None
                run_status = "completed"
                artifact_count = 1
            except Exception as exc:
                actual_status = str(exc)
                actual_error_code = str(exc)
                run_status = "failed"
        else:
            try:
                raw = DeterministicFixtureProvider(scenario).generate(request).raw_json
                ControlledExplanationValidator().validate(raw, prompt, built)
                actual_status = "passed"
                actual_error_code = None
                run_status = "completed"
                artifact_count = 1
            except ExplanationError as exc:
                actual_status = str(exc)
                actual_error_code = str(exc)
                run_status = "rejected"
            except Exception:
                actual_status = "provider_exception"
                actual_error_code = "provider_exception"
                run_status = "failed"
        passed = actual_status == expected_status
        results.append(
            {
                "sample_id": sample["id"],
                "audience": audience,
                "expected_status": expected_status,
                "actual_status": actual_status,
                "expected_error_code": (None if expected_status == "passed" else expected_status),
                "actual_error_code": actual_error_code,
                "passed": passed,
                "explanation_run_status": run_status,
                "artifact_count": artifact_count,
                "scenario": scenario,
            }
        )
    valid = [item for item in results if item["expected_status"] == "passed"]
    invalid = [item for item in results if item["expected_status"] != "passed"]
    rejected_runs = [item for item in invalid if item["explanation_run_status"] == "rejected"]
    failed_runs = [item for item in invalid if item["explanation_run_status"] == "failed"]
    return {
        "results": results,
        "constructed_valid_executed": len(valid),
        "constructed_valid_passed": sum(item["passed"] for item in valid),
        "constructed_valid_failed": len(valid) - sum(item["passed"] for item in valid),
        "constructed_valid_artifact_count": sum(
            item["artifact_count"] for item in valid if item["passed"]
        ),
        "constructed_invalid_executed": len(invalid),
        "constructed_invalid_blocked": len(rejected_runs) + len(failed_runs),
        "constructed_invalid_unexpected_pass": sum(item["passed"] for item in invalid),
        "invalid_error_code_match_count": sum(
            item["expected_error_code"] == item["actual_error_code"] for item in invalid
        ),
        "rejected_run_count": len(rejected_runs),
        "failed_run_count": len(failed_runs),
        "invalid_failed_or_rejected_count": len(rejected_runs) + len(failed_runs),
        "rejected_artifact_count": sum(item["artifact_count"] for item in invalid),
        "uncited_claim_rejection_count": sum(
            item["scenario"] in {"no_citations", "uncited_claim", "executive_uncited"}
            and item["passed"]
            for item in invalid
        ),
        "trivial_quote_rejection_count": sum(
            item["scenario"] == "trivial_quote" and item["passed"] for item in invalid
        ),
        "metadata_quote_rejection_count": sum(
            item["scenario"] in {"metadata_quote", "metadata_pilot_id", "metadata_url"}
            and item["passed"]
            for item in invalid
        ),
    }


def _exercise_budget_pressure() -> dict[str, Any]:
    from tests.unit.test_controlled_explanations import _budget_context

    try:
        ctx_20_4 = _budget_context(20, 4)
        preserves_minimum = all(len(finding.evidence) >= 1 for finding in ctx_20_4.payload.findings)
    except Exception:
        preserves_minimum = False
    try:
        ctx_20_4i = _budget_context(20, 4, illustrative=True)
        preserves_minimum = preserves_minimum and all(
            len(finding.evidence) >= 1 for finding in ctx_20_4i.payload.findings
        )
    except Exception:
        preserves_minimum = False
    fail_closed = False
    try:
        from app.core.exceptions import ExplanationError
        from tests.unit.test_controlled_explanations import _budget_context as _bc

        _bc(33, 1)
    except ExplanationError:
        fail_closed = True
    except Exception:
        pass
    return {
        "executed": 3,
        "preserves_minimum": preserves_minimum,
        "fail_closed": fail_closed,
    }


def _sensitive_scan(value: object) -> bool:
    text = json.dumps(value, ensure_ascii=False)
    return not bool(
        re.search(
            r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|"
            r"Authorization:\s*Bearer|/Users/|/home/[^/]+/|BEGIN .* PRIVATE KEY",
            text,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--comparison-database-url", default=None)
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    args = parser.parse_args()
    os.environ["DATA_DIR"] = str(args.data_dir.resolve())
    get_settings.cache_clear()
    db_available = bool(args.database_url)
    if db_available:
        assert args.database_url is not None
        primary = _execute_database(args.database_url)
        comparison = (
            _execute_database(args.comparison_database_url)
            if args.comparison_database_url
            else primary
        )
        context_stable = primary["contexts"] == comparison["contexts"]
        artifact_stable = primary["artifacts"] == comparison["artifacts"]
        evidence_insufficient_pass = _exercise_evidence_insufficient(args.database_url)
    else:
        primary = {
            "sample_count": 0,
            "contexts": {},
            "artifacts": {},
            "deterministic_rerun": False,
            "historical_prompt_snapshot_stability": False,
            "historical_context_snapshot_stability": False,
            "regulatory_case_citation_count": 0,
            "trusted_knowledge_chunk_count": 0,
            "screening_finding_count": 0,
            "reviewed_evidence_link_count": 0,
        }
        context_stable = None
        artifact_stable = None
        evidence_insufficient_pass = None
    fixture = json.loads(
        Path("tests/fixtures/controlled_rag_eval_v1/responses.json").read_text(encoding="utf-8")
    )
    evaluation = (
        _execute_fixture_corpus(args.database_url, list(fixture["samples"]))
        if db_available
        else _execute_fixture_corpus_offline(list(fixture["samples"]))
    )
    institution_prompt = load_prompt("institution")
    consumer_prompt = load_prompt("consumer")

    valid_artifact_count_db = 0
    if db_available:
        budget_exercise = _exercise_budget_pressure()
        context_budget_pressure_executed = True
        context_budget_preserves_minimum_evidence = budget_exercise.get("preserves_minimum", False)
        context_too_large_fail_closed = budget_exercise.get("fail_closed", False)
        valid_artifact_count_db = primary["sample_count"] * 2 + (
            1 if primary["deterministic_rerun"] else 0
        )
        deterministic_rerun_artifact_count = 1 if primary["deterministic_rerun"] else 0
    else:
        context_budget_pressure_executed = False
        context_budget_preserves_minimum_evidence = False
        context_too_large_fail_closed = False
        deterministic_rerun_artifact_count = 0

    formal_institution_count = primary["sample_count"] if db_available else 0
    formal_consumer_count = primary["sample_count"] if db_available else 0

    constructed_valid_artifacts = evaluation["constructed_valid_artifact_count"]
    evidence_insufficient_artifacts = 1 if evidence_insufficient_pass else 0
    total_valid_artifact_count = (
        valid_artifact_count_db + constructed_valid_artifacts + evidence_insufficient_artifacts
    )

    report = {
        "schema_version": "controlled_rag_acceptance_report_v2",
        "prompt_version": institution_prompt.prompt_version,
        "prompt_sha256": prompt_sha256(institution_prompt),
        "institution_prompt_version": institution_prompt.prompt_version,
        "institution_prompt_sha256": prompt_sha256(institution_prompt),
        "consumer_prompt_version": consumer_prompt.prompt_version,
        "consumer_prompt_sha256": prompt_sha256(consumer_prompt),
        "context_schema_version": "controlled_rag_context_v1",
        "provider_version": "controlled_fixture_provider_v2",
        "database_executed": db_available,
        "postgresql_executed": False,
        "comparison_database_executed": False,
        "docker_executed": False,
        "alembic_check_executed": False,
        "formal_database_acceptance_completed": False,
        "offline_constructed_evaluation_completed": True,
        "screening_sample_count": primary["sample_count"],
        "screening_finding_count": primary["screening_finding_count"],
        "reviewed_evidence_link_count": primary["reviewed_evidence_link_count"],
        "trusted_knowledge_chunk_count": primary["trusted_knowledge_chunk_count"],
        "formal_context_count": len(primary["contexts"]),
        "formal_institution_artifact_count": formal_institution_count,
        "formal_consumer_artifact_count": formal_consumer_count,
        "formal_valid_artifact_count": valid_artifact_count_db,
        "deterministic_rerun_artifact_count": deterministic_rerun_artifact_count,
        "regulatory_case_citation_count": primary["regulatory_case_citation_count"],
        "cross_database_context_sha_stability": context_stable,
        "cross_database_artifact_sha_stability": artifact_stable,
        "historical_prompt_snapshot_stability": primary.get(
            "historical_prompt_snapshot_stability", False
        ),
        "historical_context_snapshot_stability": primary.get(
            "historical_context_snapshot_stability", False
        ),
        "deterministic_rerun": primary["deterministic_rerun"],
        "constructed_response_samples": len(fixture["samples"]),
        "constructed_valid_expected": sum(
            item["expected"] == "passed" for item in fixture["samples"]
        ),
        "constructed_invalid_expected": sum(
            item["expected"] != "passed" for item in fixture["samples"]
        ),
        "constructed_valid_executed": evaluation["constructed_valid_executed"],
        "constructed_valid_passed": evaluation["constructed_valid_passed"],
        "constructed_valid_failed": evaluation.get("constructed_valid_failed", 0),
        "constructed_valid_artifact_count": constructed_valid_artifacts,
        "constructed_invalid_executed": evaluation["constructed_invalid_executed"],
        "constructed_invalid_blocked": evaluation["constructed_invalid_blocked"],
        "constructed_invalid_unexpected_pass": evaluation.get(
            "constructed_invalid_unexpected_pass", 0
        ),
        "invalid_error_code_match_count": evaluation["invalid_error_code_match_count"],
        "rejected_run_count": evaluation["rejected_run_count"],
        "failed_run_count": evaluation["failed_run_count"],
        "invalid_failed_or_rejected_count": evaluation["invalid_failed_or_rejected_count"],
        "rejected_artifact_count": evaluation["rejected_artifact_count"],
        "evidence_insufficient_valid_artifact_count": evidence_insufficient_artifacts,
        "total_valid_artifact_count": total_valid_artifact_count,
        "artifact_count_formula": (
            "formal_valid_artifact_count"
            " + constructed_valid_artifact_count"
            " + evidence_insufficient_valid_artifact_count"
            " = total_valid_artifact_count"
        ),
        "uncited_claim_rejection_count": evaluation["uncited_claim_rejection_count"],
        "trivial_quote_rejection_count": evaluation["trivial_quote_rejection_count"],
        "metadata_quote_rejection_count": evaluation["metadata_quote_rejection_count"],
        "cross_finding_missing_citation_rejection_count": sum(
            item["actual_error_code"] == "explanation_citation_wrong_finding" and not item["passed"]
            for item in evaluation["results"]
        ),
        "partial_certainty_rejection_count": sum(
            item["actual_error_code"] == "explanation_missing_uncertainty" and not item["passed"]
            for item in evaluation["results"]
        ),
        "hidden_segment_rejection_count": 0,
        "evidence_insufficient_context_pass": evidence_insufficient_pass,
        "context_budget_pressure_executed": context_budget_pressure_executed,
        "context_budget_preserves_minimum_evidence": context_budget_preserves_minimum_evidence,
        "context_too_large_fail_closed": context_too_large_fail_closed,
        "constructed_evaluation_results": evaluation["results"],
    }
    report["sensitive_data_scan"] = _sensitive_scan(report)
    if db_available:
        constructed_valid = all(
            [
                report["constructed_valid_executed"] == 12,
                report["constructed_valid_passed"] == 12,
                report["constructed_valid_failed"] == 0,
            ]
        )
        constructed_invalid = all(
            [
                report["constructed_invalid_executed"] == 43,
                report["constructed_invalid_blocked"] == 43,
                report["constructed_invalid_unexpected_pass"] == 0,
                report["invalid_error_code_match_count"] == 43,
                report["rejected_artifact_count"] == 0,
            ]
        )
        if not all(
            [
                report["formal_context_count"] == 60,
                context_stable,
                artifact_stable,
                report["regulatory_case_citation_count"] == 0,
                report["historical_prompt_snapshot_stability"],
                report["historical_context_snapshot_stability"],
                report["deterministic_rerun"],
                report["sensitive_data_scan"],
                constructed_valid,
                constructed_invalid,
                evidence_insufficient_pass,
                report["context_budget_pressure_executed"],
                report["context_too_large_fail_closed"],
            ]
        ):
            raise SystemExit("controlled_rag_acceptance_failed")
    else:
        offline_gate = all(
            [
                report["constructed_valid_executed"] == 12,
                report["constructed_valid_passed"] == 12,
                report["constructed_valid_failed"] == 0,
                report["constructed_invalid_executed"] == 43,
                report["constructed_invalid_blocked"] == 43,
                report["constructed_invalid_unexpected_pass"] == 0,
                report["invalid_error_code_match_count"] == 43,
                report["rejected_artifact_count"] == 0,
                report["sensitive_data_scan"],
            ]
        )
        if not offline_gate:
            raise SystemExit("controlled_rag_acceptance_failed")
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = ["# Controlled RAG acceptance report", ""]
    if not db_available:
        lines.append("该报告仅证明离线构造响应验证，不代表PostgreSQL正式数据验收完成。")
        lines.append("")
        lines.append("formal_database_acceptance_completed: `false`")
        lines.append("")
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(report.items()))
    args.markdown_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
