from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
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


def _exercise_rejections(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    counts: Counter[str] = Counter()
    scenarios = {
        "unknown_citation": "explanation_unknown_citation_key",
        "citation_mismatch": "explanation_citation_snapshot_mismatch",
        "legal_conclusion": "explanation_legal_conclusion_detected",
        "financial_advice": "explanation_financial_advice_detected",
        "guarantee": "explanation_unsupported_claim",
        "missing_uncertainty": "explanation_missing_uncertainty",
        "missing_product_disclaimer": "explanation_missing_product_context_disclaimer",
    }
    with Session(engine, expire_on_commit=False) as session:
        runs = _runs(session)
        builder = ControlledRAGContextBuilder()
        contexts = [(run, builder.build(session, run.id)) for run in runs]
        fallback = next((item for item in contexts if item[1].payload.findings), None)
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
        if fallback is None or partial is None or illustrative is None:
            raise SystemExit("controlled_rag_rejection_fixture_missing")
        service = ControlledExplanationService()
        for scenario, expected in scenarios.items():
            selected = (
                partial
                if scenario == "missing_uncertainty"
                else illustrative
                if scenario == "missing_product_disclaimer"
                else fallback
            )
            run, built = selected
            service.context_builder.build = lambda *_args, value=built: value  # type: ignore[method-assign]
            try:
                service.create(
                    session,
                    screening_run_id=run.id,
                    audience="institution",
                    provider_name="deterministic_fixture",
                    provider=DeterministicFixtureProvider(scenario),
                )
            except ExplanationError as exc:
                if str(exc) != expected:
                    raise SystemExit(f"controlled_rag_rejection_mismatch:{scenario}:{exc}") from exc
                counts[expected] += 1
            else:
                raise SystemExit(f"controlled_rag_rejection_not_enforced:{scenario}")
        rejected = session.scalar(
            select(ExplanationRun).where(ExplanationRun.status == "rejected").limit(1)
        )
        if rejected is None or session.scalar(
            select(ExplanationArtifact).where(ExplanationArtifact.explanation_run_id == rejected.id)
        ):
            raise SystemExit("controlled_rag_rejected_artifact_exposed")
    engine.dispose()
    return dict(sorted(counts.items()))


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
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--comparison-database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    args = parser.parse_args()
    os.environ["DATA_DIR"] = str(args.data_dir.resolve())
    get_settings.cache_clear()
    primary = _execute_database(args.database_url)
    comparison = _execute_database(args.comparison_database_url)
    context_stable = primary["contexts"] == comparison["contexts"]
    artifact_stable = primary["artifacts"] == comparison["artifacts"]
    rejections = _exercise_rejections(args.database_url)
    fixture = json.loads(
        Path("tests/fixtures/controlled_rag_eval_v1/responses.json").read_text(encoding="utf-8")
    )
    prompt = load_prompt("institution")
    report = {
        "schema_version": "controlled_rag_acceptance_report_v1",
        "prompt_version": prompt.prompt_version,
        "prompt_sha256": prompt_sha256(prompt),
        "context_schema_version": "controlled_rag_context_v1",
        "provider_version": "controlled_fixture_provider_v1",
        "screening_sample_count": primary["sample_count"],
        "screening_finding_count": primary["screening_finding_count"],
        "reviewed_evidence_link_count": primary["reviewed_evidence_link_count"],
        "trusted_knowledge_chunk_count": primary["trusted_knowledge_chunk_count"],
        "context_build_success_count": len(primary["contexts"]),
        "explanation_run_count": primary["sample_count"] * 2 + 1 + len(rejections),
        "valid_artifact_count": primary["sample_count"] * 2 + 1,
        "rejected_run_count": sum(rejections.values()),
        "unknown_citation_rejection_count": rejections.get("explanation_unknown_citation_key", 0),
        "citation_snapshot_mismatch_count": rejections.get(
            "explanation_citation_snapshot_mismatch", 0
        ),
        "unsupported_claim_rejection_count": rejections.get("explanation_unsupported_claim", 0),
        "legal_conclusion_rejection_count": rejections.get(
            "explanation_legal_conclusion_detected", 0
        ),
        "advice_rejection_count": rejections.get("explanation_financial_advice_detected", 0),
        "missing_uncertainty_rejection_count": rejections.get("explanation_missing_uncertainty", 0),
        "missing_illustrative_disclaimer_rejection_count": rejections.get(
            "explanation_missing_product_context_disclaimer", 0
        ),
        "regulatory_case_citation_count": primary["regulatory_case_citation_count"],
        "cross_database_context_sha_stability": context_stable,
        "cross_database_artifact_sha_stability": artifact_stable,
        "historical_prompt_snapshot_stability": primary["historical_prompt_snapshot_stability"],
        "historical_context_snapshot_stability": primary["historical_context_snapshot_stability"],
        "deterministic_rerun": primary["deterministic_rerun"],
        "constructed_response_samples": len(fixture["samples"]),
        "constructed_valid_samples": sum(
            item["expected"] == "passed" for item in fixture["samples"]
        ),
        "constructed_invalid_samples": sum(
            item["expected"] != "passed" for item in fixture["samples"]
        ),
    }
    report["sensitive_data_scan"] = _sensitive_scan(report)
    if not all(
        [
            report["context_build_success_count"] == 60,
            context_stable,
            artifact_stable,
            report["regulatory_case_citation_count"] == 0,
            report["historical_prompt_snapshot_stability"],
            report["historical_context_snapshot_stability"],
            report["deterministic_rerun"],
            report["sensitive_data_scan"],
            all(value == 1 for value in rejections.values()),
        ]
    ):
        raise SystemExit("controlled_rag_acceptance_failed")
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = ["# Controlled RAG acceptance report", ""]
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(report.items()))
    args.markdown_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
