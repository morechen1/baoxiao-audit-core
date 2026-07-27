from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.core.config import Settings
from app.models import DocumentOccurrence, Penalty, SourceDocument
from app.models.enums import AuthenticityType
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.collection.security import SafeUrlPolicy
from app.services.collection.web import WebPageCollector
from app.services.pilot.models import PilotSourceType
from app.services.pilot.reporting import PilotReportService
from app.services.pilot.service import PilotService


class StubCollector(BaseCollector):
    def __init__(
        self,
        settings: Settings,
        failures: set[str] | None = None,
    ) -> None:
        super().__init__(settings)
        self.failures = failures or set()

    def collect(self, target: str | Path) -> CollectionResult:
        url = str(target)
        if url in self.failures:
            raise RuntimeError("controlled_download_failure")
        return CollectionResult(
            content=f"official content for {url}".encode(),
            source_url=url,
            final_url=url,
            content_type="text/plain",
            http_status=200,
        )


class PublicUrlPolicy(SafeUrlPolicy):
    def resolve_and_validate(self, url: str) -> frozenset[Any]:
        return frozenset()


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    registry_dir = tmp_path / "pilot" / "source_registry"
    manifests_dir = tmp_path / "pilot" / "manifests"
    registry_dir.mkdir(parents=True)
    manifests_dir.mkdir(parents=True)
    return registry_dir, manifests_dir


def _source(
    source_type: str = "regulation",
    *,
    enabled: bool = True,
    source_key: str = "official_source",
    base_url: str = "https://source.test",
    allow_subdomains: bool = False,
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "name": "正式发布机构",
        "publisher": "正式发布机构",
        "base_url": base_url,
        "source_type": source_type,
        "allowed_domains": [base_url.split("://", 1)[1]],
        "enabled": enabled,
        "confirmed_by": "project_planner" if enabled else None,
        "crawl_policy": {
            "rate_limit_seconds": 0,
            "max_documents": 10,
            "allow_subdomains": allow_subdomains,
        },
        "notes": "已由项目规划者确认",
    }


def _entry(
    pilot_id: str = "REG-001",
    *,
    source_key: str = "official_source",
    source_type: str = "regulation",
    source_url: str = "https://source.test/document.txt",
    status: str = "approved_for_collection",
    confirmed_by: str | None = "project_planner",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "pilot_id": pilot_id,
        "source_key": source_key,
        "source_type": source_type,
        "source_url": source_url,
        "expected_title": "待核验标题",
        "collection_method": "url",
        "evaluation_usage": ["knowledge_base"],
        "confirmed_by": confirmed_by,
        "status": status,
    }
    if source_type == "regulatory_case":
        value["case_usage"] = "external_test_candidate"
    return value


def _write_registry(registry_dir: Path, *sources: dict[str, Any]) -> None:
    (registry_dir / "sources.yaml").write_text(
        yaml.safe_dump({"sources": list(sources)}, allow_unicode=True),
        encoding="utf-8",
    )


def _write_manifest(path: Path, *entries: dict[str, Any]) -> None:
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _service(tmp_path: Path, failures: set[str] | None = None) -> PilotService:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    return PilotService(
        settings,
        collector_factory=lambda allowed, subdomains: StubCollector(settings, failures),
        sleep=lambda seconds: None,
    )


def test_unconfirmed_manifest_is_not_collected(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(
        manifest,
        _entry(status="draft", confirmed_by=None),
    )

    outcomes = _service(tmp_path).collect_manifest(session, manifest, registry_dir=registry_dir)

    assert outcomes[0].status == "skipped"
    assert outcomes[0].error_code == "not_approved_for_collection"
    assert session.query(SourceDocument).count() == 0


def test_unregistered_source_is_rejected(tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())

    _, issues = _service(tmp_path).validate_manifests(manifest, registry_dir=registry_dir)

    assert "source_not_registered" in {issue.code for issue in issues}


def test_disabled_source_is_rejected(tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source(enabled=False))
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())

    _, issues = _service(tmp_path).validate_manifests(manifest, registry_dir=registry_dir)

    assert "source_disabled" in {issue.code for issue in issues}


def test_source_type_mismatch_is_rejected(tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source("penalty"))
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())

    _, issues = _service(tmp_path).validate_manifests(manifest, registry_dir=registry_dir)

    assert "source_type_mismatch" in {issue.code for issue in issues}


def test_duplicate_pilot_id_is_rejected(tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry(), _entry())

    _, issues = _service(tmp_path).validate_manifests(manifest, registry_dir=registry_dir)

    assert "duplicate_pilot_id" in {issue.code for issue in issues}


def test_private_network_url_is_rejected(tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(
        registry_dir,
        _source(base_url="https://10.0.0.1"),
    )
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry(source_url="https://10.0.0.1/document"))

    _, issues = _service(tmp_path).validate_manifests(manifest, registry_dir=registry_dir)

    assert "private_network_url" in {issue.code for issue in issues}


def test_cross_domain_redirect_is_rejected_during_pilot_collection(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://attacker.test/document"},
            request=request,
        )

    service = PilotService(
        settings,
        collector_factory=lambda allowed, subdomains: WebPageCollector(
            settings,
            allowed_hosts=allowed,
            allow_subdomains=subdomains,
            transport=httpx.MockTransport(handler),
            url_policy=PublicUrlPolicy(),
        ),
    )

    outcomes = service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert outcomes[0].status == "failed"
    assert outcomes[0].error_code == "redirect_host_not_allowed"
    assert session.query(SourceDocument).count() == 0


def test_one_collection_failure_does_not_stop_other_entries(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    failed_url = "https://source.test/fail.txt"
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(
        manifest,
        _entry("REG-001", source_url=failed_url),
        _entry("REG-002", source_url="https://source.test/pass.txt"),
    )

    outcomes = _service(tmp_path, {failed_url}).collect_manifest(
        session, manifest, registry_dir=registry_dir
    )

    assert [outcome.status for outcome in outcomes] == ["failed", "collected"]
    assert session.query(SourceDocument).count() == 1


def test_pilot_collection_authenticity_is_pending_verification(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())

    outcome = _service(tmp_path).collect_manifest(session, manifest, registry_dir=registry_dir)[0]
    document = session.get(SourceDocument, outcome.document_id)
    occurrence = session.query(DocumentOccurrence).one()

    assert outcome.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
    assert document is not None
    assert document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
    assert occurrence.response_metadata["pilot_id"] == "REG-001"


def test_penalty_template_keeps_undisclosed_wording_empty() -> None:
    template = json.loads(
        Path("pilot/templates/penalty-structured-draft.template.json").read_text(encoding="utf-8")
    )

    assert template["fields"]["original_sales_wording_disclosed"] is False
    assert template["fields"]["original_sales_wording"] is None


def test_regulation_template_defaults_validity_to_unknown() -> None:
    template = json.loads(
        Path("pilot/templates/regulation-structured-draft.template.json").read_text(
            encoding="utf-8"
        )
    )

    assert template["fields"]["validity_status"] == "unknown"


def test_quality_report_does_not_leak_absolute_paths(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    _write_manifest(manifests_dir / "regulations.jsonl", _entry(status="draft"))
    report_service = PilotReportService(_service(tmp_path))
    output = tmp_path / "reports" / "pilot-quality-report.json"
    results = registry_dir.parent / "reports" / "pilot-collection-results.jsonl"
    results.parent.mkdir()
    results.write_text(
        json.dumps(
            {
                "pilot_id": "REG-001",
                "source_key": "official_source",
                "status": "failed",
                "error_code": "controlled_download_failure",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    json_path, markdown_path = report_service.write_quality_report(
        session,
        manifests_dir,
        output,
        registry_dir=registry_dir,
    )
    content = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")

    assert str(tmp_path) not in content
    assert "/Users/" not in content
    assert "\\Users\\" not in content
    assert "controlled_download_failure" in content


def test_regulatory_case_cannot_be_persisted_as_penalty(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source("regulatory_case"))
    manifest = manifests_dir / "regulatory_cases.jsonl"
    _write_manifest(
        manifest,
        _entry(
            "CASE-001",
            source_type="regulatory_case",
            source_url="https://source.test/case.txt",
        ),
    )

    outcome = _service(tmp_path).collect_manifest(session, manifest, registry_dir=registry_dir)[0]

    assert outcome.error_code == "regulatory_case_model_not_implemented"
    assert session.query(Penalty).count() == 0
    assert session.query(SourceDocument).count() == 0


def test_pilot_source_types_cover_four_controlled_categories() -> None:
    assert {source_type.value for source_type in PilotSourceType} == {
        "regulation",
        "penalty",
        "regulatory_case",
        "product_document",
    }
