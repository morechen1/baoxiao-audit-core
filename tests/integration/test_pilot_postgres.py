from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models import (
    DataSource,
    PilotCollectionItem,
    PilotSourceRegistration,
    SourceDocument,
)
from app.models.entities import Base
from app.services.collection.base import BaseCollector, CollectionResult
from app.services.pilot.service import PilotService


class ConcurrentCollector(BaseCollector):
    def __init__(
        self,
        settings: Settings,
        calls: list[str],
        lock: threading.Lock,
    ) -> None:
        super().__init__(settings)
        self.calls = calls
        self.lock = lock

    def collect(self, target: str | Path) -> CollectionResult:
        url = str(target)
        with self.lock:
            self.calls.append(url)
        return CollectionResult(
            content=f"postgres concurrent content {url}".encode(),
            source_url=url,
            final_url=url,
            content_type="text/plain",
            http_status=200,
        )


@pytest.fixture
def postgres_sessions():
    database_url = os.environ.get("DATABASE_URL", "")
    database_name = urlparse(database_url).path.lower()
    if not database_url.startswith("postgresql") or "test" not in database_name:
        pytest.skip("dedicated PostgreSQL test database is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _source(*, max_documents: int, rate_limit_seconds: float) -> dict[str, Any]:
    return {
        "source_key": "postgres_source",
        "name": "PostgreSQL并发测试来源",
        "publisher": "PostgreSQL并发测试来源",
        "base_url": "https://source.test",
        "source_type": "regulation",
        "allowed_domains": ["source.test"],
        "enabled": True,
        "confirmed_by": "project_planner",
        "robots_review": {
            "status": "allowed",
            "checked_at": "2026-07-27",
            "checked_by": "project_planner",
            "reference_url": "https://source.test/robots.txt",
            "notes": "并发测试占位审批",
        },
        "terms_review": {
            "status": "public_access_allowed",
            "checked_at": "2026-07-27",
            "checked_by": "project_planner",
            "reference_url": "https://source.test/terms",
            "notes": "并发测试占位审批",
        },
        "approval": {
            "approved_by": "project_planner",
            "approved_at": "2026-07-27T12:00:00+08:00",
            "approval_reference": "postgres-source-review",
        },
        "crawl_policy": {
            "rate_limit_seconds": rate_limit_seconds,
            "max_documents": max_documents,
            "allow_subdomains": False,
        },
        "notes": "仅用于PostgreSQL并发约束测试",
    }


def _entry(pilot_id: str, url: str) -> dict[str, Any]:
    return {
        "pilot_id": pilot_id,
        "source_key": "postgres_source",
        "source_type": "regulation",
        "source_url": url,
        "expected_title": "并发测试占位标题",
        "collection_method": "url",
        "evaluation_usage": ["test_only"],
        "confirmed_by": "project_planner",
        "approved_at": "2026-07-27T12:30:00+08:00",
        "approval_reference": f"review-{pilot_id}",
        "status": "approved_for_collection",
    }


def _layout(
    tmp_path: Path,
    *,
    max_documents: int,
    rate_limit_seconds: float,
) -> tuple[Path, list[Path]]:
    registry_dir = tmp_path / "pilot" / "source_registry"
    manifests_dir = tmp_path / "pilot" / "manifests"
    registry_dir.mkdir(parents=True)
    manifests_dir.mkdir(parents=True)
    (registry_dir / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": [
                    _source(
                        max_documents=max_documents,
                        rate_limit_seconds=rate_limit_seconds,
                    )
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    paths = [manifests_dir / "first.jsonl", manifests_dir / "second.jsonl"]
    paths[0].write_text(
        json.dumps(_entry("PG-001", "https://source.test/one.txt")) + "\n",
        encoding="utf-8",
    )
    paths[1].write_text(
        json.dumps(_entry("PG-002", "https://source.test/two.txt")) + "\n",
        encoding="utf-8",
    )
    return registry_dir, paths


def _run_concurrently(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    registry_dir: Path,
    manifests: list[Path],
    *,
    sleeps: list[float],
) -> tuple[list[str], list[Any]]:
    calls: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")

    def collect(manifest: Path):
        with session_factory() as session:
            service = PilotService(
                settings,
                collector_factory=lambda allowed, subdomains: ConcurrentCollector(
                    settings, calls, lock
                ),
                sleep=lambda seconds: sleeps.append(seconds),
                clock=lambda: datetime(2026, 7, 27, 12, tzinfo=UTC),
            )
            barrier.wait()
            return service.collect_manifest(
                session,
                manifest,
                registry_dir=registry_dir,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(collect, manifest) for manifest in manifests]
        results = [future.result(timeout=20) for future in futures]
    return calls, results


def test_postgres_concurrent_max_documents_is_not_exceeded(
    postgres_sessions, tmp_path: Path
) -> None:
    registry_dir, manifests = _layout(
        tmp_path,
        max_documents=1,
        rate_limit_seconds=0,
    )

    calls, results = _run_concurrently(
        postgres_sessions,
        tmp_path,
        registry_dir,
        manifests,
        sleeps=[],
    )

    with postgres_sessions() as session:
        assert len(calls) == 1
        assert session.query(SourceDocument).count() == 1
        assert session.query(PilotCollectionItem).count() == 2
        assert {item.last_error_code for item in session.query(PilotCollectionItem)} == {
            None,
            "source_document_limit_exceeded",
        }
        assert session.query(PilotSourceRegistration).count() == 1
    assert {result.outcomes[0].status for result in results} == {"collected", "failed"}


def test_postgres_concurrent_requests_share_rate_limit_state(
    postgres_sessions, tmp_path: Path
) -> None:
    registry_dir, manifests = _layout(
        tmp_path,
        max_documents=2,
        rate_limit_seconds=1,
    )
    sleeps: list[float] = []

    calls, _ = _run_concurrently(
        postgres_sessions,
        tmp_path,
        registry_dir,
        manifests,
        sleeps=sleeps,
    )

    with postgres_sessions() as session:
        registration = session.query(PilotSourceRegistration).one()
        assert registration.last_request_at is not None
        assert session.query(PilotCollectionItem).filter_by(status="collected").count() == 2
    assert len(calls) == 2
    assert len(sleeps) == 1
    assert sleeps[0] > 0.8


def test_postgres_concurrent_pilot_id_is_globally_unique(postgres_sessions, tmp_path: Path) -> None:
    registry_dir, _ = _layout(
        tmp_path,
        max_documents=2,
        rate_limit_seconds=0,
    )
    manifests: list[Path] = []
    for directory, url in (("left", "one.txt"), ("right", "two.txt")):
        manifest_dir = tmp_path / "isolated" / directory
        manifest_dir.mkdir(parents=True)
        manifest = manifest_dir / "manifest.jsonl"
        manifest.write_text(
            json.dumps(_entry("PG-SHARED", f"https://source.test/{url}")) + "\n",
            encoding="utf-8",
        )
        manifests.append(manifest)

    calls, results = _run_concurrently(
        postgres_sessions,
        tmp_path,
        registry_dir,
        manifests,
        sleeps=[],
    )

    with postgres_sessions() as session:
        items = session.query(PilotCollectionItem).all()
        assert len(items) == 1
        assert items[0].pilot_id == "PG-SHARED"
        assert session.query(PilotSourceRegistration).count() == 1
    assert len(calls) == 1
    assert {result.outcomes[0].status for result in results} == {"collected", "skipped"}


def test_postgres_source_versions_preserve_historical_identity(
    postgres_sessions, tmp_path: Path
) -> None:
    registry_dir, manifests = _layout(
        tmp_path,
        max_documents=3,
        rate_limit_seconds=0,
    )
    calls: list[str] = []
    lock = threading.Lock()
    settings = Settings(database_url="sqlite://", data_dir=tmp_path / "data")
    service = PilotService(
        settings,
        collector_factory=lambda allowed, subdomains: ConcurrentCollector(settings, calls, lock),
    )

    with postgres_sessions() as session:
        service.collect_manifest(session, manifests[0], registry_dir=registry_dir)

    registry_path = registry_dir / "sources.yaml"
    registry_data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    registry_data["sources"][0]["name"] = "PostgreSQL并发测试来源（新版本）"
    registry_data["sources"][0]["publisher"] = "新版本发布机构"
    registry_data["sources"][0]["approval"]["approval_reference"] = "postgres-source-review-v2"
    registry_path.write_text(
        yaml.safe_dump(registry_data, allow_unicode=True),
        encoding="utf-8",
    )

    with postgres_sessions() as session:
        service.collect_manifest(session, manifests[1], registry_dir=registry_dir)

    with postgres_sessions() as session:
        registrations = (
            session.query(PilotSourceRegistration).order_by(PilotSourceRegistration.version).all()
        )
        data_sources = session.query(DataSource).order_by(DataSource.id).all()
        items = session.query(PilotCollectionItem).order_by(PilotCollectionItem.id).all()
        assert [registration.version for registration in registrations] == [1, 2]
        assert registrations[0].name == "PostgreSQL并发测试来源"
        assert registrations[0].publisher == "PostgreSQL并发测试来源"
        assert registrations[0].superseded_at is not None
        assert registrations[1].name == "PostgreSQL并发测试来源（新版本）"
        assert registrations[1].publisher == "新版本发布机构"
        assert [source.name for source in data_sources] == [
            "PostgreSQL并发测试来源",
            "PostgreSQL并发测试来源（新版本）",
        ]
        assert items[0].source_registration_id == registrations[0].id
        assert items[1].source_registration_id == registrations[1].id
