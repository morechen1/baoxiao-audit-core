from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

import app.cli.main as cli_module
from app.cli.main import app as cli_app
from app.core.config import Settings
from app.models import (
    DataSource,
    DocumentOccurrence,
    Penalty,
    PilotCollectionItem,
    PilotCollectionRun,
    PilotSourceRegistration,
    SourceDocument,
)
from app.models.enums import AuthenticityType
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.pilot.reporting import PilotReportService
from app.services.pilot.service import (
    PilotConfigurationError,
    PilotService,
    collection_outcomes_jsonl,
)


class StubCollector(BaseCollector):
    def __init__(
        self,
        settings: Settings,
        calls: list[str],
        failures: dict[str, Exception],
    ) -> None:
        super().__init__(settings)
        self.calls = calls
        self.failures = failures

    def collect(self, target: str | Path) -> CollectionResult:
        url = str(target)
        self.calls.append(url)
        failure = self.failures.get(url)
        if failure:
            raise failure
        return CollectionResult(
            content=f"official content for {url}".encode(),
            source_url=url,
            final_url=url,
            content_type="text/plain",
            http_status=200,
        )


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
    name: str = "正式发布机构",
    publisher: str = "正式发布机构",
    max_documents: int = 10,
    rate_limit_seconds: float = 0,
    approval_reference: str = "source-review-001",
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "name": name,
        "publisher": publisher,
        "base_url": base_url,
        "source_type": source_type,
        "allowed_domains": [base_url.split("://", 1)[1]],
        "enabled": enabled,
        "confirmed_by": "project_planner" if enabled else None,
        "robots_review": {
            "status": "allowed",
            "checked_at": "2026-07-27",
            "checked_by": "project_planner",
            "reference_url": f"{base_url}/robots.txt",
            "notes": "允许访问明确公开文件",
        },
        "terms_review": {
            "status": "public_access_allowed",
            "checked_at": "2026-07-27",
            "checked_by": "project_planner",
            "reference_url": f"{base_url}/terms",
            "notes": "仅下载公开原件",
        },
        "approval": {
            "approved_by": "project_planner",
            "approved_at": "2026-07-27T12:00:00+08:00",
            "approval_reference": approval_reference,
        },
        "crawl_policy": {
            "rate_limit_seconds": rate_limit_seconds,
            "max_documents": max_documents,
            "allow_subdomains": False,
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
    approval_reference: str = "manifest-review-001",
    expected_title: str = "待核验标题",
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "pilot_id": pilot_id,
        "source_key": source_key,
        "source_type": source_type,
        "source_url": source_url,
        "expected_title": expected_title,
        "collection_method": "url",
        "evaluation_usage": ["knowledge_base"],
        "confirmed_by": "project_planner",
        "approved_at": "2026-07-27T12:30:00+08:00",
        "approval_reference": approval_reference,
        "status": status,
    }
    if source_type == "regulatory_case":
        value["case_usage"] = "external_test_candidate"
    return value


def _write_registry(
    registry_dir: Path,
    *sources: dict[str, Any],
    filename: str = "sources.yaml",
) -> None:
    (registry_dir / filename).write_text(
        yaml.safe_dump({"sources": list(sources)}, allow_unicode=True),
        encoding="utf-8",
    )


def _write_manifest(path: Path, *entries: dict[str, Any]) -> None:
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


def _service(
    tmp_path: Path,
    *,
    failures: dict[str, Exception] | None = None,
    sleeps: list[float] | None = None,
    clock: datetime | None = None,
) -> tuple[PilotService, list[str]]:
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    calls: list[str] = []
    failures = failures or {}
    sleep_values = sleeps if sleeps is not None else []
    service = PilotService(
        settings,
        collector_factory=lambda allowed, subdomains: StubCollector(settings, calls, failures),
        sleep=sleep_values.append,
        clock=(lambda: clock) if clock else None,
    )
    return service, calls


def _collect(
    session,
    tmp_path: Path,
    manifest: Path,
    registry_dir: Path,
    **service_kwargs: Any,
):
    service, calls = _service(tmp_path, **service_kwargs)
    result = service.collect_manifest(session, manifest, registry_dir=registry_dir)
    return service, calls, result


def test_duplicate_id_in_one_manifest_blocks_every_request_and_write(
    session, tmp_path: Path
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry(), _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "duplicate_pilot_id" in {issue.code for issue in caught.value.issues}
    assert calls == []
    assert session.query(SourceDocument).count() == 0
    assert session.query(PilotCollectionRun).count() == 0


def test_duplicate_id_across_manifests_blocks_selected_manifest(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    first = manifests_dir / "a.jsonl"
    second = manifests_dir / "b.jsonl"
    _write_manifest(first, _entry())
    _write_manifest(second, _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError):
        service.collect_manifest(session, first, registry_dir=registry_dir)

    assert calls == []
    assert session.query(PilotCollectionItem).count() == 0


def test_duplicate_source_key_blocks_whole_batch(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source(), _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "duplicate_source_key" in {issue.code for issue in caught.value.issues}
    assert calls == []
    assert session.query(DataSource).count() == 0


def test_invalid_line_prevents_partial_collection(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    manifest.write_text(
        json.dumps(_entry()) + "\n" + "{invalid-json}\n",
        encoding="utf-8",
    )
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "invalid_manifest_entry" in {issue.code for issue in caught.value.issues}
    assert calls == []
    assert session.query(PilotCollectionRun).count() == 0


def test_invalid_nfra_dynamic_landing_fails_without_request_or_database_write(
    session, tmp_path: Path
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(
        registry_dir,
        _source(base_url="https://www.nfra.gov.cn"),
    )
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(
        manifest,
        _entry(source_url=("https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=invalid")),
    )
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "nfra_invalid_landing_url" in {issue.code for issue in caught.value.issues}
    assert calls == []
    assert session.query(PilotCollectionRun).count() == 0
    assert session.query(PilotCollectionItem).count() == 0
    assert session.query(PilotSourceRegistration).count() == 0
    assert session.query(DataSource).count() == 0
    assert session.query(SourceDocument).count() == 0


def test_collect_uses_global_validation_not_only_selected_file(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    selected = manifests_dir / "selected.jsonl"
    invalid = manifests_dir / "invalid.jsonl"
    _write_manifest(selected, _entry())
    invalid.write_text("{broken}\n", encoding="utf-8")
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError, match="pilot_configuration_invalid"):
        service.collect_manifest(session, selected, registry_dir=registry_dir)

    assert calls == []


def test_pilot_collect_cli_returns_configuration_error_without_database_write(
    session, tmp_path: Path, monkeypatch
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    selected = manifests_dir / "selected.jsonl"
    _write_manifest(selected, _entry(), _entry())

    class SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    monkeypatch.setattr(cli_module, "SessionLocal", SessionContext)
    result = CliRunner().invoke(
        cli_app,
        ["pilot-collect", "--manifest", str(selected)],
    )

    assert result.exit_code == 1
    assert "pilot_configuration_invalid" in result.output
    assert session.query(PilotCollectionRun).count() == 0


def test_pilot_id_has_database_unique_constraint(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    _, _, result = _collect(session, tmp_path, manifest, registry_dir)
    original = session.get(PilotCollectionItem, result.outcomes[0].collection_item_id)
    assert original is not None
    duplicate = PilotCollectionItem(
        run_id=original.run_id,
        source_registration_id=original.source_registration_id,
        pilot_id=original.pilot_id,
        source_key=original.source_key,
        source_type=original.source_type,
        source_url=original.source_url,
        expected_title=original.expected_title,
        evaluation_usage_json=original.evaluation_usage_json,
        confirmed_by=original.confirmed_by,
        approval_reference=original.approval_reference,
        approved_at=original.approved_at,
        manifest_entry_sha256=original.manifest_entry_sha256,
        source_registry_entry_sha256=original.source_registry_entry_sha256,
        status="pending",
    )
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_successful_pilot_id_is_idempotent_without_second_request(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    service, calls = _service(tmp_path)

    first = service.collect_manifest(session, manifest, registry_dir=registry_dir)
    second = service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert len(calls) == 1
    assert first.outcomes[0].status == "collected"
    assert second.outcomes[0].error_code == "already_collected"
    assert session.query(PilotCollectionItem).count() == 1


def test_two_pilots_can_bind_same_occurrence_without_metadata_overwrite(
    session, tmp_path: Path
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry("REG-001"), _entry("REG-002"))

    _, calls, result = _collect(session, tmp_path, manifest, registry_dir)

    items = session.query(PilotCollectionItem).order_by(PilotCollectionItem.id).all()
    occurrence = session.query(DocumentOccurrence).one()
    assert len(calls) == 2
    assert len(items) == 2
    assert items[0].occurrence_id == items[1].occurrence_id == occurrence.id
    assert "pilot_id" not in occurrence.response_metadata
    assert result.outcomes[1].created is False


def test_manifest_change_cannot_mutate_historical_item(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry(expected_title="原标题"))
    service, _ = _service(tmp_path)
    service.collect_manifest(session, manifest, registry_dir=registry_dir)
    original = session.query(PilotCollectionItem).one()
    original_hash = original.manifest_entry_sha256

    _write_manifest(manifest, _entry(expected_title="修改后的标题"))
    service.collect_manifest(session, manifest, registry_dir=registry_dir)
    session.refresh(original)

    assert original.expected_title == "原标题"
    assert original.manifest_entry_sha256 == original_hash


def test_deleting_manifest_does_not_remove_status_history(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    _, _, result = _collect(session, tmp_path, manifest, registry_dir)
    manifest.write_text("", encoding="utf-8")

    status = PilotReportService().status(session)
    run_status = PilotReportService().status(session, run_id=result.run_id)

    assert status["totals"]["collected_count"] == 1
    assert run_status["totals"]["collected_count"] == 1


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("name", "新的正式名称"),
        ("publisher", "新的发布机构"),
    ],
)
def test_source_identity_change_creates_version_without_overwrite(
    session,
    tmp_path: Path,
    changed_field: str,
    changed_value: str,
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    first_source = _source()
    _write_registry(registry_dir, first_source)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry("REG-001"))
    service, _ = _service(tmp_path)
    service.collect_manifest(session, manifest, registry_dir=registry_dir)
    first = session.query(PilotSourceRegistration).one()

    second_source = _source(approval_reference="source-review-002")
    second_source[changed_field] = changed_value
    _write_registry(registry_dir, second_source)
    manifest.write_text("", encoding="utf-8")
    second_manifest = manifests_dir / "second.jsonl"
    _write_manifest(second_manifest, _entry("REG-002"))
    service.collect_manifest(session, second_manifest, registry_dir=registry_dir)
    registrations = session.query(PilotSourceRegistration).order_by(PilotSourceRegistration.version)

    assert registrations.count() == 2
    assert getattr(registrations.first(), changed_field) == getattr(first, changed_field)
    assert registrations.all()[1].version == 2


def test_base_url_change_requires_approved_new_source_version(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    first_manifest = manifests_dir / "first.jsonl"
    _write_manifest(first_manifest, _entry("REG-001"))
    service, _ = _service(tmp_path)
    service.collect_manifest(session, first_manifest, registry_dir=registry_dir)

    first_manifest.write_text("", encoding="utf-8")
    _write_registry(
        registry_dir,
        _source(
            base_url="https://new-source.test",
            approval_reference="source-review-002",
        ),
    )
    second_manifest = manifests_dir / "second.jsonl"
    _write_manifest(
        second_manifest,
        _entry(
            "REG-002",
            source_url="https://new-source.test/document.txt",
        ),
    )
    service.collect_manifest(session, second_manifest, registry_dir=registry_dir)
    registrations = (
        session.query(PilotSourceRegistration).order_by(PilotSourceRegistration.version).all()
    )

    assert [item.base_url for item in registrations] == [
        "https://source.test/",
        "https://new-source.test/",
    ]
    assert session.query(DataSource).count() == 2


def test_source_type_change_never_rewrites_old_data_source(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    first_manifest = manifests_dir / "first.jsonl"
    _write_manifest(first_manifest, _entry("REG-001"))
    service, _ = _service(tmp_path)
    service.collect_manifest(session, first_manifest, registry_dir=registry_dir)

    first_manifest.write_text("", encoding="utf-8")
    _write_registry(
        registry_dir,
        _source("product_document", approval_reference="source-review-002"),
    )
    second_manifest = manifests_dir / "second.jsonl"
    _write_manifest(
        second_manifest,
        _entry(
            "PROD-001",
            source_type="product_document",
        ),
    )
    service.collect_manifest(session, second_manifest, registry_dir=registry_dir)

    assert [value.source_type for value in session.query(DataSource).order_by(DataSource.id)] == [
        "regulation",
        "product_document",
    ]


def test_max_documents_is_cumulative_across_manifests_and_runs(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source(max_documents=1))
    first = manifests_dir / "first.jsonl"
    second = manifests_dir / "second.jsonl"
    _write_manifest(first, _entry("REG-001", source_url="https://source.test/one.txt"))
    _write_manifest(second, _entry("REG-002", source_url="https://source.test/two.txt"))
    service, calls = _service(tmp_path)

    first_result = service.collect_manifest(session, first, registry_dir=registry_dir)
    second_result = service.collect_manifest(session, second, registry_dir=registry_dir)

    assert first_result.outcomes[0].status == "collected"
    assert second_result.outcomes[0].error_code == "source_document_limit_exceeded"
    assert len(calls) == 1
    assert session.query(SourceDocument).count() == 1


def test_failed_request_still_reserves_rate_interval(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source(rate_limit_seconds=5))
    first_url = "https://source.test/fail.txt"
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(
        manifest,
        _entry("REG-001", source_url=first_url),
        _entry("REG-002", source_url="https://source.test/pass.txt"),
    )
    sleeps: list[float] = []
    service, calls = _service(
        tmp_path,
        failures={first_url: httpx.ReadTimeout("timeout")},
        sleeps=sleeps,
        clock=datetime(2026, 7, 27, 12, tzinfo=UTC),
    )

    result = service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert calls == [first_url, "https://source.test/pass.txt"]
    assert result.outcomes[0].error_code == "network_timeout"
    assert sleeps == [5.0]


def test_quality_report_uses_run_ledger_and_ignores_old_jsonl(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    _, _, result = _collect(session, tmp_path, manifest, registry_dir)
    reports = registry_dir.parent / "reports"
    reports.mkdir()
    (reports / "pilot-collection-results.jsonl").write_text(
        json.dumps(
            {
                "run_id": 999,
                "status": "failed",
                "error_code": "historical_fake_error",
            }
        ),
        encoding="utf-8",
    )

    cumulative = PilotReportService().quality_report(session)
    per_run = PilotReportService().quality_report(session, run_id=result.run_id)

    assert cumulative["collected_count"] == 1
    assert per_run["collected_count"] == 1
    assert "historical_fake_error" not in json.dumps(cumulative)


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("/Users/alice/secret.pdf"),
        RuntimeError("/home/bob/private.docx"),
        RuntimeError(r"C:\Users\Carol\secret.txt"),
        RuntimeError("https://example.com/private/token?access=secret"),
    ],
)
def test_exception_details_never_escape_public_outputs(
    session, tmp_path: Path, failure: Exception
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    url = "https://source.test/document.txt"
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry(source_url=url))
    _, _, result = _collect(
        session,
        tmp_path,
        manifest,
        registry_dir,
        failures={url: failure},
    )

    output = collection_outcomes_jsonl(result.outcomes)
    report = json.dumps(PilotReportService().quality_report(session))

    assert result.outcomes[0].error_code == "collection_internal_error"
    assert str(failure) not in output
    assert str(failure) not in report


@pytest.mark.parametrize(
    "missing_field",
    ["confirmed_by", "robots_review", "terms_review", "approval"],
)
def test_enabled_source_requires_explicit_policy_and_approval(
    session, tmp_path: Path, missing_field: str
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    source = _source()
    source.pop(missing_field)
    _write_registry(registry_dir, source)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "invalid_approval_metadata" in {issue.code for issue in caught.value.issues}
    assert calls == []


@pytest.mark.parametrize("missing_field", ["approved_at", "approval_reference"])
def test_enabled_source_requires_approval_timestamp_and_reference(
    session, tmp_path: Path, missing_field: str
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    source = _source()
    source["approval"].pop(missing_field)
    _write_registry(registry_dir, source)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert {issue.code for issue in caught.value.issues} & {
        "invalid_source_registry",
        "invalid_approval_metadata",
    }
    assert calls == []


@pytest.mark.parametrize(
    ("review_field", "status"),
    [
        ("robots_review", "prohibited"),
        ("terms_review", "prohibited"),
    ],
)
def test_prohibited_policy_status_blocks_enabled_source(
    session,
    tmp_path: Path,
    review_field: str,
    status: str,
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    source = _source()
    source[review_field]["status"] = status
    _write_registry(registry_dir, source)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "invalid_approval_metadata" in {issue.code for issue in caught.value.issues}
    assert calls == []


@pytest.mark.parametrize("missing_field", ["approved_at", "approval_reference"])
def test_approved_manifest_requires_approval_metadata(
    session, tmp_path: Path, missing_field: str
) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    entry = _entry()
    entry.pop(missing_field)
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, entry)
    service, calls = _service(tmp_path)

    with pytest.raises(PilotConfigurationError) as caught:
        service.collect_manifest(session, manifest, registry_dir=registry_dir)

    assert "invalid_approval_metadata" in {issue.code for issue in caught.value.issues}
    assert calls == []


def test_regulatory_case_pilot_collects_document_without_creating_penalty(
    session, tmp_path: Path
) -> None:
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

    _, calls, result = _collect(session, tmp_path, manifest, registry_dir)
    item = session.query(PilotCollectionItem).one()
    document = session.get(SourceDocument, item.document_id)

    assert calls == ["https://source.test/case.txt"]
    assert result.outcomes[0].status == "collected"
    assert result.outcomes[0].authenticity_type == "pending_verification"
    assert item.status == "collected"
    assert document is not None
    assert document.data_type == "regulatory_case"
    assert session.query(Penalty).count() == 0


def test_collection_starts_pending_and_binds_hashes_and_occurrence(session, tmp_path: Path) -> None:
    registry_dir, manifests_dir = _layout(tmp_path)
    _write_registry(registry_dir, _source())
    manifest = manifests_dir / "regulations.jsonl"
    _write_manifest(manifest, _entry())

    _, _, result = _collect(session, tmp_path, manifest, registry_dir)
    item = session.query(PilotCollectionItem).one()
    document = session.get(SourceDocument, item.document_id)

    assert result.outcomes[0].authenticity_type == "pending_verification"
    assert document is not None
    assert document.authenticity_type == AuthenticityType.PENDING_VERIFICATION.value
    assert item.occurrence_id is not None
    assert len(item.manifest_entry_sha256) == 64
    assert len(item.source_registry_entry_sha256) == 64


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
