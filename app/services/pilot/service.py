from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import DataSource, DocumentOccurrence
from app.models.enums import AuthenticityType
from app.services.collection import SafeUrlPolicy, WebPageCollector
from app.services.pilot.models import (
    PilotManifestEntry,
    PilotManifestStatus,
    PilotSourceType,
    SourceRegistryEntry,
    SourceRegistryFile,
)

SUPPORTED_DOCUMENT_TYPES = frozenset(
    {
        PilotSourceType.REGULATION,
        PilotSourceType.PENALTY,
        PilotSourceType.PRODUCT_DOCUMENT,
    }
)


@dataclass(frozen=True)
class PilotValidationIssue:
    location: str
    code: str
    message: str


@dataclass(frozen=True)
class PilotCollectionOutcome:
    pilot_id: str
    status: str
    source_key: str | None = None
    document_id: int | None = None
    created: bool | None = None
    authenticity_type: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


CollectorFactory = Callable[[frozenset[str], bool], WebPageCollector]
SleepFunction = Callable[[float], None]


class PilotService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        collector_factory: CollectorFactory | None = None,
        sleep: SleepFunction = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self.collector_factory = collector_factory or self._collector
        self.sleep = sleep

    @staticmethod
    def registry_dir_for(manifest_path: Path) -> Path:
        path = manifest_path.resolve()
        manifest_dir = path if path.is_dir() else path.parent
        return manifest_dir.parent / "source_registry"

    def load_registry(
        self, registry_dir: Path
    ) -> tuple[dict[str, SourceRegistryEntry], list[PilotValidationIssue]]:
        sources: dict[str, SourceRegistryEntry] = {}
        issues: list[PilotValidationIssue] = []
        for path in sorted(registry_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {"sources": []}
                registry = SourceRegistryFile.model_validate(raw)
            except (OSError, yaml.YAMLError, PydanticValidationError) as exc:
                issues.append(PilotValidationIssue(str(path), "invalid_source_registry", str(exc)))
                continue
            for source in registry.sources:
                if source.source_key in sources:
                    issues.append(
                        PilotValidationIssue(
                            str(path),
                            "duplicate_source_key",
                            f"duplicate source_key: {source.source_key}",
                        )
                    )
                    continue
                sources[source.source_key] = source
        if not registry_dir.is_dir():
            issues.append(
                PilotValidationIssue(
                    str(registry_dir),
                    "source_registry_not_found",
                    "source registry directory does not exist",
                )
            )
        return sources, issues

    @staticmethod
    def load_manifest(
        path: Path,
    ) -> tuple[list[tuple[int, PilotManifestEntry]], list[PilotValidationIssue]]:
        entries: list[tuple[int, PilotManifestEntry]] = []
        issues: list[PilotValidationIssue] = []
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            return [], [
                PilotValidationIssue(
                    str(path), "invalid_manifest_path", "manifest must be an existing JSONL file"
                )
            ]
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entries.append((line_number, PilotManifestEntry.model_validate_json(line)))
            except PydanticValidationError as exc:
                issues.append(
                    PilotValidationIssue(
                        f"{path}:{line_number}", "invalid_manifest_entry", str(exc)
                    )
                )
        return entries, issues

    def validate_manifests(
        self,
        path: Path,
        *,
        registry_dir: Path | None = None,
    ) -> tuple[list[PilotManifestEntry], list[PilotValidationIssue]]:
        registry_path = registry_dir or self.registry_dir_for(path)
        sources, issues = self.load_registry(registry_path)
        manifest_paths = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        if path.is_dir() and not manifest_paths:
            issues.append(
                PilotValidationIssue(str(path), "manifest_not_found", "no JSONL manifests found")
            )
        valid: list[PilotManifestEntry] = []
        seen_ids: set[str] = set()
        for manifest_path in manifest_paths:
            entries, load_issues = self.load_manifest(manifest_path)
            issues.extend(load_issues)
            for line_number, entry in entries:
                location = f"{manifest_path}:{line_number}"
                entry_issues = self._validate_entry(entry, sources, location)
                if entry.pilot_id in seen_ids:
                    entry_issues.append(
                        PilotValidationIssue(
                            location,
                            "duplicate_pilot_id",
                            f"duplicate pilot_id: {entry.pilot_id}",
                        )
                    )
                seen_ids.add(entry.pilot_id)
                if entry_issues:
                    issues.extend(entry_issues)
                else:
                    valid.append(entry)
        return valid, issues

    def collect_manifest(
        self,
        session: Session,
        manifest_path: Path,
        *,
        registry_dir: Path | None = None,
    ) -> list[PilotCollectionOutcome]:
        registry_path = registry_dir or self.registry_dir_for(manifest_path)
        sources, registry_issues = self.load_registry(registry_path)
        entries, load_issues = self.load_manifest(manifest_path)
        outcomes = [
            PilotCollectionOutcome(
                pilot_id="<registry>",
                status="failed",
                error_code=issue.code,
            )
            for issue in [*registry_issues, *load_issues]
        ]
        seen_ids: set[str] = set()
        collected_per_source: dict[str, int] = {}
        for line_number, entry in entries:
            location = f"{manifest_path}:{line_number}"
            entry_issues = self._validate_entry(entry, sources, location)
            if entry.pilot_id in seen_ids:
                entry_issues.append(
                    PilotValidationIssue(
                        location,
                        "duplicate_pilot_id",
                        f"duplicate pilot_id: {entry.pilot_id}",
                    )
                )
            seen_ids.add(entry.pilot_id)
            if entry_issues:
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="failed",
                        source_key=entry.source_key,
                        error_code=entry_issues[0].code,
                    )
                )
                continue
            if entry.status != PilotManifestStatus.APPROVED_FOR_COLLECTION:
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="skipped",
                        source_key=entry.source_key,
                        error_code="not_approved_for_collection",
                    )
                )
                continue
            source = sources[entry.source_key]
            if entry.source_type not in SUPPORTED_DOCUMENT_TYPES:
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="failed",
                        source_key=entry.source_key,
                        error_code="regulatory_case_model_not_implemented",
                    )
                )
                continue
            count = collected_per_source.get(entry.source_key, 0)
            if count >= source.crawl_policy.max_documents:
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="failed",
                        source_key=entry.source_key,
                        error_code="source_document_limit_exceeded",
                    )
                )
                continue
            if count > 0 and source.crawl_policy.rate_limit_seconds:
                self.sleep(source.crawl_policy.rate_limit_seconds)
            try:
                outcome = self._collect_entry(session, entry, source)
            except Exception as exc:
                session.rollback()
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="failed",
                        source_key=entry.source_key,
                        error_code=self._safe_error_code(exc),
                    )
                )
                continue
            outcomes.append(outcome)
            collected_per_source[entry.source_key] = count + 1
        return outcomes

    def _collect_entry(
        self,
        session: Session,
        entry: PilotManifestEntry,
        source: SourceRegistryEntry,
    ) -> PilotCollectionOutcome:
        data_source = self._ensure_data_source(session, source)
        allowed_hosts = SafeUrlPolicy.allowed_hosts(str(source.base_url), source.allowed_domains)
        collector = self.collector_factory(allowed_hosts, source.crawl_policy.allow_subdomains)
        result = collector.collect(str(entry.source_url))
        result.metadata = {
            **result.metadata,
            "pilot_id": entry.pilot_id,
            "pilot_source_key": entry.source_key,
            "evaluation_usage": list(entry.evaluation_usage),
            "confirmed_by": entry.confirmed_by,
        }
        document, created = collector.persist(
            session,
            result,
            entry.source_type.value,
            source_id=data_source.id,
            authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        )
        occurrence = session.scalar(
            select(DocumentOccurrence).where(
                DocumentOccurrence.document_id == document.id,
                DocumentOccurrence.source_url == result.source_url,
                DocumentOccurrence.final_url == result.final_url,
            )
        )
        if occurrence:
            occurrence.response_metadata = {
                **occurrence.response_metadata,
                "pilot_collection_created": created,
            }
            session.commit()
        return PilotCollectionOutcome(
            pilot_id=entry.pilot_id,
            status="collected",
            source_key=entry.source_key,
            document_id=document.id,
            created=created,
            authenticity_type=document.authenticity_type,
        )

    def _ensure_data_source(self, session: Session, source: SourceRegistryEntry) -> DataSource:
        for candidate in session.scalars(select(DataSource)):
            if candidate.crawl_policy.get("pilot_source_key") == source.source_key:
                candidate.name = source.name
                candidate.publisher = source.publisher
                candidate.base_url = str(source.base_url)
                candidate.source_type = source.source_type.value
                candidate.enabled = source.enabled
                candidate.rate_limit_seconds = source.crawl_policy.rate_limit_seconds
                candidate.crawl_policy = self._database_crawl_policy(source)
                session.commit()
                return candidate
        data_source = DataSource(
            name=source.name,
            publisher=source.publisher,
            base_url=str(source.base_url),
            source_type=source.source_type.value,
            enabled=source.enabled,
            rate_limit_seconds=source.crawl_policy.rate_limit_seconds,
            crawl_policy=self._database_crawl_policy(source),
        )
        session.add(data_source)
        session.commit()
        session.refresh(data_source)
        return data_source

    @staticmethod
    def _database_crawl_policy(source: SourceRegistryEntry) -> dict[str, Any]:
        return {
            "pilot_source_key": source.source_key,
            "allowed_domains": list(source.allowed_domains),
            "allow_subdomains": source.crawl_policy.allow_subdomains,
            "max_documents": source.crawl_policy.max_documents,
        }

    def _validate_entry(
        self,
        entry: PilotManifestEntry,
        sources: dict[str, SourceRegistryEntry],
        location: str,
    ) -> list[PilotValidationIssue]:
        source = sources.get(entry.source_key)
        if not source:
            return [
                PilotValidationIssue(
                    location, "source_not_registered", "source_key is not registered"
                )
            ]
        issues: list[PilotValidationIssue] = []
        if not source.enabled:
            issues.append(
                PilotValidationIssue(location, "source_disabled", "registered source is disabled")
            )
        if source.source_type != entry.source_type:
            issues.append(
                PilotValidationIssue(
                    location,
                    "source_type_mismatch",
                    "manifest source_type does not match the registered source",
                )
            )
        url = str(entry.source_url)
        static_issue = self._static_url_issue(url)
        if static_issue:
            issues.append(PilotValidationIssue(location, static_issue, "unsafe source_url"))
        elif not self._url_allowed(source, url):
            issues.append(
                PilotValidationIssue(
                    location,
                    "source_domain_not_allowed",
                    "source_url is outside the registered allowlist",
                )
            )
        return issues

    @staticmethod
    def _static_url_issue(url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return "unsupported_url_scheme"
        if parsed.username or parsed.password:
            return "url_credentials_forbidden"
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            return "url_hostname_required"
        if hostname == "localhost" or hostname.endswith(".localhost"):
            return "private_network_url"
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return None
        return None if address.is_global else "private_network_url"

    @staticmethod
    def _url_allowed(source: SourceRegistryEntry, url: str) -> bool:
        target = (urlparse(url).hostname or "").rstrip(".").lower()
        allowed = SafeUrlPolicy.allowed_hosts(str(source.base_url), list(source.allowed_domains))
        if source.crawl_policy.allow_subdomains:
            return SafeUrlPolicy.host_allowed_for_hosts(url, allowed)
        return target in allowed

    @staticmethod
    def _safe_error_code(exc: Exception) -> str:
        text = str(exc).strip().lower()
        safe = "".join(character if character.isalnum() else "_" for character in text)
        safe = "_".join(part for part in safe.split("_") if part)
        return (safe or exc.__class__.__name__.lower())[:120]

    def _collector(self, allowed_hosts: frozenset[str], allow_subdomains: bool) -> WebPageCollector:
        return WebPageCollector(
            self.settings,
            allowed_hosts=allowed_hosts,
            allow_subdomains=allow_subdomains,
        )


def collection_outcomes_jsonl(outcomes: Iterable[PilotCollectionOutcome]) -> str:
    return "".join(json.dumps(outcome.as_dict(), ensure_ascii=False) + "\n" for outcome in outcomes)


def append_collection_outcomes(path: Path, outcomes: Iterable[PilotCollectionOutcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as target:
        target.write(collection_outcomes_jsonl(outcomes))
