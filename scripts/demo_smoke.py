"""Run the judge-demo API smoke against the isolated PostgreSQL runtime."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class DemoCase:
    name: str
    title: str
    material_type: str
    raw_text: str
    minimum_findings: int


CASES = (
    DemoCase(
        name="high-risk",
        title="高收益承诺宣传",
        material_type="advertisement",
        raw_text="监管推荐本产品，保证收益8%，本金绝对安全，今天投保即可领取限量礼品。",
        minimum_findings=3,
    ),
    DemoCase(
        name="low-risk",
        title="合同要点说明",
        material_type="product_introduction",
        raw_text="本材料仅作产品信息说明，保险责任、等待期、责任免除及退保安排以正式保险合同为准。投保前请阅读条款并按需咨询持证人员。",
        minimum_findings=0,
    ),
    DemoCase(
        name="boundary-risk",
        title="退保价值边界表述",
        material_type="sales_script",
        raw_text="资金使用灵活，如有需要可随时退保没有损失。具体权益和现金价值请以合同约定为准。",
        minimum_findings=1,
    ),
)


def _require_isolated_runtime() -> None:
    if os.environ.get("BAOXIAO_DEMO_RUNTIME") != "1":
        raise SystemExit("Refusing demo smoke: set BAOXIAO_DEMO_RUNTIME=1.")
    url = make_url(os.environ.get("DATABASE_URL", ""))
    if not url.drivername.startswith("postgresql") or url.database != "baoxiao_demo":
        raise SystemExit("Refusing demo smoke: DATABASE_URL must target baoxiao_demo.")


def _json(response: object) -> dict[str, object]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _run_case(client: TestClient, case: DemoCase) -> dict[str, int]:
    created = _json(
        client.post(
            "/api/v1/screenings",
            json={
                "title": case.title,
                "material_type": case.material_type,
                "raw_text": case.raw_text,
                "source_label": "contest_demo_smoke",
            },
        )
    )
    run_id = int(created["screening_run_id"])
    assert created["status"] == "completed"
    assert int(created["finding_count"]) >= case.minimum_findings
    endpoints = created["report_endpoints"]
    assert isinstance(endpoints, dict)
    screening = _json(client.get(str(endpoints["screening"])))
    institution_report = _json(client.get(str(endpoints["institution_report"])))
    consumer_report = _json(client.get(str(endpoints["consumer_notice"])))
    findings = screening["findings"]
    assert isinstance(findings, list)
    assert len(findings) >= case.minimum_findings
    assert "findings" in institution_report
    assert "risk_prompts" in consumer_report
    assert "evidence_links" in consumer_report

    if case.minimum_findings == 0:
        # The controlled explanation layer deliberately has no claim to explain
        # when deterministic screening returns no findings.
        return {"findings": len(findings), "citations": 0}

    citation_count = 0
    for audience in ("institution", "consumer"):
        explanation = _json(
            client.post(
                f"/api/v1/screenings/{run_id}/explanations",
                json={"audience": audience, "provider": "deterministic_fixture"},
            )
        )
        assert explanation["status"] == "completed"
        assert explanation["validation_status"] == "passed"
        artifact = _json(client.get(str(explanation["artifact_endpoint"])))
        assert artifact["validated_output"]
        citations_response = client.get(
            f"/api/v1/explanations/{int(explanation['explanation_run_id'])}/citations"
        )
        assert citations_response.status_code == 200, citations_response.text
        citations = citations_response.json()
        assert isinstance(citations, list)
        citation_count += len(citations)
    if case.minimum_findings:
        assert citation_count > 0, f"{case.name} must surface at least one Citation"
    return {"findings": len(findings), "citations": citation_count}


def main() -> int:
    _require_isolated_runtime()
    # Import only after the guarded environment check so the module-level engine
    # uses the isolated runtime URL.
    from app.main import app

    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200 and "保销智审" in home.text
        assert client.get("/static/app.js").status_code == 200
        assert _json(client.get("/health"))["status"] == "ok"
        results = {case.name: _run_case(client, case) for case in CASES}
    print({"status": "passed", "cases": results})
    return 0


if __name__ == "__main__":
    sys.exit(main())
