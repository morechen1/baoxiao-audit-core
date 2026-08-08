"""Verify the final contest runtime without changing rules, taxonomy, or knowledge."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

FINAL_DATABASE = "baoxiao_contest_final"


@dataclass(frozen=True)
class FinalCase:
    name: str
    title: str
    material_type: str
    raw_text: str
    minimum_findings: int


CASES = (
    FinalCase(
        "high-risk",
        "高收益承诺宣传",
        "advertisement",
        "监管推荐本产品，保证收益8%，本金绝对安全，今天投保即可领取限量礼品。",
        3,
    ),
    FinalCase(
        "boundary-risk",
        "退保价值边界表述",
        "sales_script",
        "资金使用灵活，如有需要可随时退保没有损失。具体权益和现金价值请以合同约定为准。",
        1,
    ),
    FinalCase(
        "low-risk",
        "合同要点说明",
        "product_introduction",
        "本材料仅作产品信息说明，保险责任、等待期、责任免除及退保安排以正式保险合同为准。投保前请阅读条款并按需咨询持证人员。",
        0,
    ),
)


def require_final_runtime() -> None:
    if os.environ.get("BAOXIAO_FINAL_RUNTIME") != "1":
        raise SystemExit("Refusing final smoke: set BAOXIAO_FINAL_RUNTIME=1.")
    if os.environ.get("FINAL_DATABASE_NAME", FINAL_DATABASE) != FINAL_DATABASE:
        raise SystemExit(f"Refusing final smoke: database name must be {FINAL_DATABASE}.")
    url = make_url(os.environ.get("DATABASE_URL", ""))
    if url.get_backend_name() != "postgresql" or url.database != FINAL_DATABASE:
        raise SystemExit(f"Refusing final smoke: DATABASE_URL must target {FINAL_DATABASE}.")
    if os.environ.get("SEMANTIC_SCREENING_ENABLED", "").lower() not in {"false", "0"}:
        raise SystemExit("Refusing final smoke: semantic screening must be explicitly disabled.")


def response_json(response: Any) -> dict[str, Any]:
    if response.status_code != 200:
        raise AssertionError(f"HTTP {response.status_code}: {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError("Expected a JSON object")
    return payload


def knowledge_summary() -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models import KnowledgeChunk, Penalty, ProductDocument, SourceDocument
    from app.models.enums import APPROVABLE_STATUSES
    from app.services.knowledge import (
        KnowledgeIndexService,
        SearchRequest,
        TrustedKnowledgeSearchService,
    )

    with SessionLocal() as session:
        report = KnowledgeIndexService().verify_chunks(session)
        source_documents = int(
            session.scalar(select(func.count()).select_from(SourceDocument)) or 0
        )
        eligible = int(
            session.scalar(
                select(func.count())
                .select_from(SourceDocument)
                .where(
                    SourceDocument.authenticity_type == "verified_public",
                    SourceDocument.final_review_status.in_(APPROVABLE_STATUSES),
                    SourceDocument.knowledge_index_status == "indexed",
                )
            )
            or 0
        )
        document_types = dict(
            session.execute(
                select(SourceDocument.data_type, func.count()).group_by(SourceDocument.data_type)
            ).all()
        )
        chunk_types = dict(
            session.execute(
                select(KnowledgeChunk.record_type, func.count())
                .where(KnowledgeChunk.is_active.is_(True))
                .group_by(KnowledgeChunk.record_type)
            ).all()
        )
        runtime_results = TrustedKnowledgeSearchService().search(
            session,
            SearchRequest(
                record_types=("regulation", "product_document", "penalty"),
                limit=100,
            ),
        )
        summary = {
            "source_documents": source_documents,
            "eligible_documents": eligible,
            "regulations": int(document_types.get("regulation", 0)),
            "product_documents": int(
                session.scalar(select(func.count()).select_from(ProductDocument)) or 0
            ),
            "penalty_documents": int(document_types.get("penalty", 0)),
            "penalty_records": int(session.scalar(select(func.count()).select_from(Penalty)) or 0),
            "trusted_chunks": report.active_chunks,
            "regulation_chunks": int(chunk_types.get("regulation", 0)),
            "product_chunks": int(chunk_types.get("product_document", 0)),
            "penalty_chunks": int(chunk_types.get("penalty", 0)),
            "regulatory_case_chunks": int(chunk_types.get("regulatory_case", 0)),
            "trusted_search_service_chunks": len(runtime_results),
            "trust_gate_valid": report.valid,
        }
    expected = {
        "source_documents": 15,
        "eligible_documents": 15,
        "regulations": 3,
        "product_documents": 2,
        "penalty_documents": 10,
        "penalty_records": 34,
        "trusted_chunks": 73,
        "regulation_chunks": 33,
        "product_chunks": 6,
        "penalty_chunks": 34,
        "regulatory_case_chunks": 0,
        "trusted_search_service_chunks": 73,
        "trust_gate_valid": True,
    }
    if summary != expected:
        raise AssertionError(f"final knowledge mismatch: {summary}")
    return summary


def run_case(
    client: TestClient,
    case: FinalCase,
    *,
    provider: str,
    skip_explanations: bool,
) -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models import ExplanationArtifact, ExplanationRun, KnowledgeChunk, ScreeningRun

    created = response_json(
        client.post(
            "/api/v1/screenings",
            json={
                "title": case.title,
                "material_type": case.material_type,
                "raw_text": case.raw_text,
                "source_label": "contest_final_runtime",
            },
        )
    )
    run_id = int(created["screening_run_id"])
    screening = response_json(client.get(f"/api/v1/screenings/{run_id}"))
    findings = screening["findings"]
    if not isinstance(findings, list) or len(findings) < case.minimum_findings:
        raise AssertionError(f"{case.name} finding count mismatch")
    if case.minimum_findings == 0 and findings:
        raise AssertionError("low-risk case must remain zero-finding")

    evidence_links = sum(len(item.get("evidence", [])) for item in findings)
    if findings and any(not item.get("evidence") for item in findings):
        raise AssertionError(f"{case.name} has a finding without trusted evidence")

    source_rows: list[dict[str, str]] = []
    with SessionLocal() as session:
        run = session.get(ScreeningRun, run_id)
        if run is None or run.material.raw_text != case.raw_text:
            raise AssertionError("fresh run text identity mismatch")
        for finding in findings:
            for evidence in finding.get("evidence", []):
                chunk = session.scalar(
                    select(KnowledgeChunk).where(
                        KnowledgeChunk.chunk_identity_sha256
                        == evidence["chunk_identity_sha256"]
                    )
                )
                if chunk is None or not chunk.is_active or chunk.record_type == "regulatory_case":
                    raise AssertionError("evidence chunk is not active trusted runtime knowledge")
                metadata = chunk.source_document.metadata_json or {}
                if metadata.get("constructed_contest_demo"):
                    raise AssertionError("old constructed demo-only chunk was used")
                source_rows.append(
                    {
                        "logical_id": str(chunk.pilot_id or chunk.source_document_id),
                        "source_type": chunk.record_type,
                        "title": chunk.title,
                    }
                )

    provider_calls = 0
    artifacts: dict[str, bool] = {"institution": False, "consumer": False}
    citation_count = 0
    if findings and not skip_explanations:
        for audience in ("institution", "consumer"):
            provider_calls += 1
            explanation = response_json(
                client.post(
                    f"/api/v1/screenings/{run_id}/explanations",
                    json={"audience": audience, "provider": provider},
                )
            )
            if explanation.get("status") != "completed" or explanation.get(
                "validation_status"
            ) != "passed":
                raise AssertionError(f"{case.name} {audience} explanation did not pass")
            artifact = response_json(client.get(str(explanation["artifact_endpoint"])))
            artifacts[audience] = bool(artifact.get("validated_output"))
            citations = client.get(
                f"/api/v1/explanations/{int(explanation['explanation_run_id'])}/citations"
            )
            if citations.status_code != 200 or not isinstance(citations.json(), list):
                raise AssertionError("validated citations are unavailable")
            citation_count += len(citations.json())

    with SessionLocal() as session:
        explanation_runs = int(
            session.scalar(
                select(func.count())
                .select_from(ExplanationRun)
                .where(ExplanationRun.screening_run_id == run_id)
            )
            or 0
        )
        artifact_count = int(
            session.scalar(
                select(func.count())
                .select_from(ExplanationArtifact)
                .join(ExplanationRun)
                .where(ExplanationRun.screening_run_id == run_id)
            )
            or 0
        )
    if not findings and (explanation_runs or artifact_count or provider_calls):
        raise AssertionError("low-risk case created an explanation side effect")

    representative = list(
        {
            json.dumps(row, ensure_ascii=False, sort_keys=True): row
            for row in source_rows
        }.values()
    )
    return {
        "screening_run_id": run_id,
        "findings": len(findings),
        "rule_ids": [item["rule_id"] for item in findings],
        "evidence_links": evidence_links,
        "citations": citation_count,
        "institution_artifact": artifacts["institution"],
        "consumer_artifact": artifacts["consumer"],
        "provider_calls": provider_calls,
        "artifacts": artifact_count,
        "representative_evidence_sources": representative,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-explanations", action="store_true")
    parser.add_argument("--provider", default="openai_compatible")
    args = parser.parse_args()
    require_final_runtime()

    from app.core.config import get_settings
    from app.main import app
    from app.services.explanation import configured_provider_status

    settings = get_settings()
    if settings.semantic_screening_enabled:
        raise AssertionError("semantic screening must remain disabled")
    provider_status = configured_provider_status()
    if not args.skip_explanations and (
        args.provider != "openai_compatible"
        or provider_status.get("provider") != "openai_compatible"
        or not provider_status.get("ready")
    ):
        raise AssertionError("real controlled-explanation provider is not ready")

    knowledge = knowledge_summary()
    with TestClient(app) as client:
        if response_json(client.get("/health"))["status"] != "ok":
            raise AssertionError("health check failed")
        cases = {
            case.name: run_case(
                client,
                case,
                provider=args.provider,
                skip_explanations=args.skip_explanations,
            )
            for case in CASES
        }
    print(
        json.dumps(
            {
                "status": "passed",
                "database": FINAL_DATABASE,
                "semantic_screening_enabled": False,
                "provider": {
                    "name": provider_status.get("provider"),
                    "model": provider_status.get("model"),
                    "ready": provider_status.get("ready"),
                    "calls_executed": sum(item["provider_calls"] for item in cases.values()),
                },
                "knowledge": knowledge,
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
