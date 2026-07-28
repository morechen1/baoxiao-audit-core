from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import yaml
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import CollectionError, UnsafeUrlError
from app.models import (
    DataSource,
    DocumentOccurrence,
    PilotCollectionItem,
    PilotCollectionRun,
    PilotSourceRegistration,
)
from app.models.enums import AuthenticityType
from app.services.collection import (
    BaseCollector,
    NfraPublicDocumentCollector,
    SafeUrlPolicy,
    WebPageCollector,
)
from app.services.pilot.models import (
    PilotManifestEntry,
    PilotManifestStatus,
    PilotSourceType,
    RobotsReviewStatus,
    SourceRegistryEntry,
    SourceRegistryFile,
    TermsReviewStatus,
)

SUPPORTED_DOCUMENT_TYPES = frozenset(
    {
        PilotSourceType.REGULATION,
        PilotSourceType.PENALTY,
        PilotSourceType.PRODUCT_DOCUMENT,
        PilotSourceType.REGULATORY_CASE,
    }
)
ALLOWED_ROBOTS_STATUSES = frozenset(
    {
        RobotsReviewStatus.ALLOWED,
        RobotsReviewStatus.NOT_PUBLISHED_MANUAL_REVIEW,
    }
)
ALLOWED_TERMS_STATUSES = frozenset(
    {
        TermsReviewStatus.PUBLIC_ACCESS_ALLOWED,
        TermsReviewStatus.NOT_PUBLISHED_MANUAL_REVIEW,
    }
)
SAFE_URL_ERROR_CODES = frozenset(
    {
        "redirect_host_not_allowed",
        "source_host_not_allowed",
        "private_network_url",
        "url_credentials_forbidden",
        "unsupported_url_scheme",
        "url_hostname_required",
    }
)
SAFE_COLLECTION_ERROR_CODES = frozenset(
    {
        "nfra_administrative_license",
        "nfra_api_status_error",
        "nfra_angular_template_shell",
        "nfra_appointment_qualification",
        "nfra_captcha_page",
        "nfra_doc_id_mismatch",
        "nfra_empty_document_body",
        "nfra_error_page",
        "nfra_error_payload",
        "nfra_invalid_json",
        "nfra_invalid_landing_url",
        "nfra_non_insurance_penalty",
        "nfra_retrieval_url_changed",
        "nfra_title_mismatch",
        "nfra_title_missing",
        "nfra_unexpected_response_type",
        "unsupported_document_data_type",
        "duplicate_content_type_conflict",
        "stored_artifact_corrupt",
    }
)


@dataclass(frozen=True)
class PilotValidationIssue:
    location: str
    code: str
    message: str


class PilotConfigurationError(Exception):
    def __init__(self, issues: list[PilotValidationIssue]) -> None:
        super().__init__("pilot_configuration_invalid")
        self.issues = issues


@dataclass(frozen=True)
class PilotCollectionOutcome:
    pilot_id: str
    status: str
    run_id: int | None = None
    collection_item_id: int | None = None
    source_key: str | None = None
    document_id: int | None = None
    occurrence_id: int | None = None
    created: bool | None = None
    authenticity_type: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PilotCollectionResult:
    run_id: int
    outcomes: list[PilotCollectionOutcome]


CollectorFactory = Callable[[frozenset[str], bool], BaseCollector]
SleepFunction = Callable[[float], None]
ClockFunction = Callable[[], datetime]


class PilotService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        collector_factory: CollectorFactory | None = None,
        sleep: SleepFunction = time.sleep,
        clock: ClockFunction | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._custom_collector_factory = collector_factory is not None
        self.collector_factory = collector_factory or self._collector
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(UTC))

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
        if not registry_dir.is_dir():
            return {}, [
                PilotValidationIssue(
                    self._portable_location(registry_dir),
                    "source_registry_not_found",
                    "source registry directory does not exist",
                )
            ]
        for path in sorted(registry_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {"sources": []}
                registry = SourceRegistryFile.model_validate(raw)
            except (OSError, yaml.YAMLError, PydanticValidationError):
                issues.append(
                    PilotValidationIssue(
                        path.name,
                        "invalid_source_registry",
                        "source registry entry is invalid",
                    )
                )
                continue
            for source in registry.sources:
                if source.source_key in sources:
                    issues.append(
                        PilotValidationIssue(
                            path.name,
                            "duplicate_source_key",
                            f"duplicate source_key: {source.source_key}",
                        )
                    )
                    continue
                sources[source.source_key] = source
                if source.enabled and not self._source_approval_valid(source):
                    issues.append(
                        PilotValidationIssue(
                            path.name,
                            "invalid_approval_metadata",
                            f"enabled source approval is incomplete: {source.source_key}",
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
                    path.name,
                    "invalid_manifest_path",
                    "manifest must be an existing JSONL file",
                )
            ]
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entries.append((line_number, PilotManifestEntry.model_validate_json(line)))
            except PydanticValidationError:
                issues.append(
                    PilotValidationIssue(
                        f"{path.name}:{line_number}",
                        "invalid_manifest_entry",
                        "manifest entry is invalid",
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
                PilotValidationIssue(
                    path.name,
                    "manifest_not_found",
                    "no JSONL manifests found",
                )
            )
        valid: list[PilotManifestEntry] = []
        seen_ids: dict[str, str] = {}
        for manifest_path in manifest_paths:
            entries, load_issues = self.load_manifest(manifest_path)
            issues.extend(load_issues)
            for line_number, entry in entries:
                location = f"{manifest_path.name}:{line_number}"
                entry_issues = self._validate_entry(entry, sources, location)
                previous = seen_ids.get(entry.pilot_id)
                if previous is not None:
                    issues.append(
                        PilotValidationIssue(
                            previous,
                            "duplicate_pilot_id",
                            f"duplicate pilot_id: {entry.pilot_id}",
                        )
                    )
                    entry_issues.append(
                        PilotValidationIssue(
                            location,
                            "duplicate_pilot_id",
                            f"duplicate pilot_id: {entry.pilot_id}",
                        )
                    )
                else:
                    seen_ids[entry.pilot_id] = location
                if entry_issues:
                    issues.extend(entry_issues)
                else:
                    valid.append(entry)
        return valid, self._deduplicate_issues(issues)

    def collect_manifest(
        self,
        session: Session,
        manifest_path: Path,
        *,
        registry_dir: Path | None = None,
        requested_by: str = "cli_operator",
    ) -> PilotCollectionResult:
        manifests_dir = manifest_path.resolve().parent
        registry_path = registry_dir or self.registry_dir_for(manifest_path)

        _, issues = self.validate_manifests(manifests_dir, registry_dir=registry_path)
        if issues:
            raise PilotConfigurationError(issues)

        sources, _ = self.load_registry(registry_path)
        selected_entries = [entry for _, entry in self.load_manifest(manifest_path)[0]]
        run = PilotCollectionRun(
            run_uuid=str(uuid4()),
            selected_manifest=manifest_path.name,
            manifest_set_sha256=self._file_set_hash(sorted(manifests_dir.glob("*.jsonl"))),
            source_registry_set_sha256=self._file_set_hash(sorted(registry_path.glob("*.yaml"))),
            status="running",
            requested_by=requested_by,
            planned_count=len(selected_entries),
            success_count=0,
            failure_count=0,
            skipped_count=0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        outcomes: list[PilotCollectionOutcome] = []
        for entry in selected_entries:
            if entry.status != PilotManifestStatus.APPROVED_FOR_COLLECTION:
                run.skipped_count += 1
                outcomes.append(
                    PilotCollectionOutcome(
                        pilot_id=entry.pilot_id,
                        status="skipped",
                        run_id=run.id,
                        source_key=entry.source_key,
                        error_code="not_approved_for_collection",
                    )
                )
                session.commit()
                continue
            outcome = self._process_entry(session, run, entry, sources[entry.source_key])
            outcomes.append(outcome)
            if outcome.status == "collected":
                run.success_count += 1
            elif outcome.status == "skipped":
                run.skipped_count += 1
            else:
                run.failure_count += 1
            session.commit()

        run.completed_at = self._now()
        run.status = "completed_with_errors" if run.failure_count else "completed"
        session.commit()
        return PilotCollectionResult(run.id, outcomes)

    def _process_entry(
        self,
        session: Session,
        run: PilotCollectionRun,
        entry: PilotManifestEntry,
        source: SourceRegistryEntry,
    ) -> PilotCollectionOutcome:
        source_registration = self._ensure_source_registration(session, source)
        existing = session.scalar(
            select(PilotCollectionItem).where(PilotCollectionItem.pilot_id == entry.pilot_id)
        )
        if existing:
            return PilotCollectionOutcome(
                pilot_id=entry.pilot_id,
                status="skipped",
                run_id=run.id,
                collection_item_id=existing.id,
                source_key=entry.source_key,
                document_id=existing.document_id,
                occurrence_id=existing.occurrence_id,
                error_code=(
                    "already_collected"
                    if existing.status == "collected"
                    else "pilot_id_already_registered"
                ),
            )

        item = self._new_item(run, entry, source_registration)
        session.add(item)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(PilotCollectionItem).where(PilotCollectionItem.pilot_id == entry.pilot_id)
            )
            return PilotCollectionOutcome(
                pilot_id=entry.pilot_id,
                status="skipped",
                run_id=run.id,
                collection_item_id=existing.id if existing else None,
                source_key=entry.source_key,
                error_code="already_collected",
            )
        session.refresh(item)

        limit_error, wait_seconds = self._reserve_request_slot(session, item, source_registration)
        if limit_error:
            return self._outcome(item, run.id)
        if wait_seconds > 0:
            self.sleep(wait_seconds)

        try:
            document, occurrence, created = self._collect_entry(session, entry, source_registration)
        except Exception as exc:
            refreshed_item = session.get(PilotCollectionItem, item.id)
            if refreshed_item is None:
                raise RuntimeError("pilot collection item disappeared") from None
            refreshed_item.status = "failed"
            refreshed_item.last_error_code = self._public_error_code(exc)
            session.commit()
            return self._outcome(refreshed_item, run.id)

        refreshed_item = session.get(PilotCollectionItem, item.id)
        if refreshed_item is None:
            raise RuntimeError("pilot collection item disappeared")
        refreshed_item.status = "collected"
        refreshed_item.document_id = document.id
        refreshed_item.occurrence_id = occurrence.id
        refreshed_item.document_created = created
        refreshed_item.last_error_code = None
        refreshed_item.collected_at = self._now()
        session.commit()
        return self._outcome(
            refreshed_item,
            run.id,
            authenticity_type=document.authenticity_type,
        )

    def _reserve_request_slot(
        self,
        session: Session,
        item: PilotCollectionItem,
        registration: PilotSourceRegistration,
    ) -> tuple[str | None, float]:
        locked = session.scalar(
            select(PilotSourceRegistration)
            .where(PilotSourceRegistration.id == registration.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if locked is None:
            raise RuntimeError("pilot source registration disappeared")
        reserved_count = session.scalar(
            select(func.count(PilotCollectionItem.id)).where(
                PilotCollectionItem.source_registration_id == locked.id,
                PilotCollectionItem.status.in_(("attempting", "collected")),
            )
        )
        if int(reserved_count or 0) >= locked.max_documents:
            item.status = "failed"
            item.last_error_code = "source_document_limit_exceeded"
            session.commit()
            return item.last_error_code, 0

        now = self._now()
        last_request_at = self._aware(locked.last_request_at)
        next_request_at = now
        if last_request_at is not None:
            next_request_at = max(
                now,
                last_request_at + timedelta(seconds=locked.rate_limit_seconds),
            )
        wait_seconds = max(0.0, (next_request_at - now).total_seconds())
        locked.last_request_at = next_request_at
        item.status = "attempting"
        item.attempt_count += 1
        session.commit()
        return None, wait_seconds

    def _collect_entry(
        self,
        session: Session,
        entry: PilotManifestEntry,
        registration: PilotSourceRegistration,
    ) -> tuple[Any, DocumentOccurrence, bool]:
        if registration.data_source_id is None:
            raise CollectionError("unsupported_document_data_type")
        allowed_hosts = SafeUrlPolicy.allowed_hosts(
            registration.base_url, registration.allowed_domains_json
        )
        source_url = str(entry.source_url)
        if NfraPublicDocumentCollector.is_dynamic_landing_candidate(source_url):
            if not NfraPublicDocumentCollector.supports(source_url, allowed_hosts):
                raise CollectionError("nfra_invalid_landing_url")
            if not self._custom_collector_factory:
                collector: BaseCollector = NfraPublicDocumentCollector(
                    self.settings,
                    allowed_hosts=allowed_hosts,
                    allow_subdomains=registration.allow_subdomains,
                    expected_title=entry.expected_title,
                    source_type=entry.source_type.value,
                )
            else:
                collector = self.collector_factory(
                    allowed_hosts,
                    registration.allow_subdomains,
                )
        else:
            collector = self.collector_factory(
                allowed_hosts,
                registration.allow_subdomains,
            )
        result = collector.collect(source_url)
        document, created = collector.persist(
            session,
            result,
            entry.source_type.value,
            source_id=registration.data_source_id,
            authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        )
        occurrence = session.scalar(
            select(DocumentOccurrence).where(
                DocumentOccurrence.document_id == document.id,
                DocumentOccurrence.source_url == result.source_url,
                DocumentOccurrence.final_url == result.final_url,
            )
        )
        if occurrence is None:
            raise CollectionError("occurrence_not_recorded")
        return document, occurrence, created

    def _ensure_source_registration(
        self,
        session: Session,
        source: SourceRegistryEntry,
    ) -> PilotSourceRegistration:
        entry_sha256 = self.source_entry_hash(source)
        approval = source.approval
        robots = source.robots_review
        terms = source.terms_review
        if not approval or not robots or not terms or not source.confirmed_by:
            raise PilotConfigurationError(
                [
                    PilotValidationIssue(
                        source.source_key,
                        "invalid_approval_metadata",
                        "enabled source approval is incomplete",
                    )
                ]
            )

        # PostgreSQL row locking serializes normal updates. The bounded retry also
        # handles the case where two transactions selected the same latest version
        # before either inserted its successor.
        for attempt in range(3):
            existing = session.scalar(
                select(PilotSourceRegistration).where(
                    PilotSourceRegistration.source_key == source.source_key,
                    PilotSourceRegistration.entry_sha256 == entry_sha256,
                )
            )
            if existing:
                return existing

            previous = session.scalar(
                select(PilotSourceRegistration)
                .where(PilotSourceRegistration.source_key == source.source_key)
                .order_by(PilotSourceRegistration.version.desc())
                .with_for_update()
            )
            version = (previous.version + 1) if previous else 1
            now = self._now()
            if previous and previous.superseded_at is None:
                previous.superseded_at = now

            data_source: DataSource | None = None
            if source.source_type in SUPPORTED_DOCUMENT_TYPES:
                data_source = DataSource(
                    name=source.name,
                    publisher=source.publisher,
                    base_url=str(source.base_url),
                    source_type=source.source_type.value,
                    enabled=True,
                    rate_limit_seconds=source.crawl_policy.rate_limit_seconds,
                    crawl_policy={
                        "pilot_source_key": source.source_key,
                        "pilot_source_version": version,
                        "source_registry_entry_sha256": entry_sha256,
                        "allowed_domains": list(source.allowed_domains),
                        "allow_subdomains": source.crawl_policy.allow_subdomains,
                        "max_documents": source.crawl_policy.max_documents,
                    },
                )
                session.add(data_source)
                session.flush()

            registration = PilotSourceRegistration(
                data_source_id=data_source.id if data_source else None,
                source_key=source.source_key,
                version=version,
                entry_sha256=entry_sha256,
                name=source.name,
                publisher=source.publisher,
                base_url=str(source.base_url),
                source_type=source.source_type.value,
                allowed_domains_json=list(source.allowed_domains),
                allow_subdomains=source.crawl_policy.allow_subdomains,
                rate_limit_seconds=source.crawl_policy.rate_limit_seconds,
                max_documents=source.crawl_policy.max_documents,
                confirmed_by=source.confirmed_by,
                approved_at=approval.approved_at,
                approval_reference=approval.approval_reference,
                robots_review_status=robots.status.value,
                robots_checked_at=robots.checked_at,
                robots_checked_by=robots.checked_by,
                robots_reference_url=(str(robots.reference_url) if robots.reference_url else None),
                robots_notes=robots.notes,
                terms_review_status=terms.status.value,
                terms_checked_at=terms.checked_at,
                terms_checked_by=terms.checked_by,
                terms_reference_url=str(terms.reference_url) if terms.reference_url else None,
                terms_notes=terms.notes,
                notes=source.notes,
                created_at=now,
            )
            session.add(registration)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                concurrent = session.scalar(
                    select(PilotSourceRegistration).where(
                        PilotSourceRegistration.source_key == source.source_key,
                        PilotSourceRegistration.entry_sha256 == entry_sha256,
                    )
                )
                if concurrent:
                    return concurrent
                if attempt == 2:
                    raise
                continue
            session.refresh(registration)
            return registration
        raise RuntimeError("pilot source registration retry exhausted")

    def _new_item(
        self,
        run: PilotCollectionRun,
        entry: PilotManifestEntry,
        source: PilotSourceRegistration,
    ) -> PilotCollectionItem:
        if not entry.confirmed_by or not entry.approval_reference or not entry.approved_at:
            raise PilotConfigurationError(
                [
                    PilotValidationIssue(
                        entry.pilot_id,
                        "invalid_approval_metadata",
                        "approved manifest entry metadata is incomplete",
                    )
                ]
            )
        return PilotCollectionItem(
            run_id=run.id,
            source_registration_id=source.id,
            pilot_id=entry.pilot_id,
            source_key=entry.source_key,
            source_type=entry.source_type.value,
            source_url=str(entry.source_url),
            expected_title=entry.expected_title,
            evaluation_usage_json=list(entry.evaluation_usage),
            confirmed_by=entry.confirmed_by,
            approval_reference=entry.approval_reference,
            approved_at=entry.approved_at,
            manifest_entry_sha256=self.manifest_entry_hash(entry),
            source_registry_entry_sha256=source.entry_sha256,
            status="pending",
            attempt_count=0,
        )

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
        if (
            entry.status == PilotManifestStatus.APPROVED_FOR_COLLECTION
            and not self._manifest_approval_valid(entry)
        ):
            issues.append(
                PilotValidationIssue(
                    location,
                    "invalid_approval_metadata",
                    "approved manifest metadata is incomplete",
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
        elif NfraPublicDocumentCollector.is_dynamic_landing_candidate(
            url
        ) and not NfraPublicDocumentCollector.supports(
            url,
            SafeUrlPolicy.allowed_hosts(str(source.base_url), list(source.allowed_domains)),
        ):
            issues.append(
                PilotValidationIssue(
                    location,
                    "nfra_invalid_landing_url",
                    "NFRA dynamic landing URL is invalid",
                )
            )
        return issues

    @staticmethod
    def _source_approval_valid(source: SourceRegistryEntry) -> bool:
        return bool(
            source.confirmed_by
            and source.approval
            and source.approval.approved_by
            and source.approval.approval_reference
            and source.robots_review
            and source.robots_review.checked_by
            and source.robots_review.notes
            and source.robots_review.status in ALLOWED_ROBOTS_STATUSES
            and source.terms_review
            and source.terms_review.checked_by
            and source.terms_review.notes
            and source.terms_review.status in ALLOWED_TERMS_STATUSES
        )

    @staticmethod
    def _manifest_approval_valid(entry: PilotManifestEntry) -> bool:
        return bool(entry.confirmed_by and entry.approved_at and entry.approval_reference)

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
    def _public_error_code(exc: Exception) -> str:
        if isinstance(exc, UnsafeUrlError):
            code = str(exc)
            return code if code in SAFE_URL_ERROR_CODES else "unsafe_url"
        if isinstance(exc, httpx.TimeoutException):
            return "network_timeout"
        if isinstance(exc, httpx.TooManyRedirects):
            return "too_many_redirects"
        if isinstance(exc, httpx.HTTPStatusError):
            return "http_status_error"
        if isinstance(exc, CollectionError):
            code = str(exc)
            return code if code in SAFE_COLLECTION_ERROR_CODES else "collection_error"
        if isinstance(exc, OSError):
            return "artifact_io_error"
        return "collection_internal_error"

    @staticmethod
    def source_entry_hash(source: SourceRegistryEntry) -> str:
        return PilotService._payload_hash(source.model_dump(mode="json"))

    @staticmethod
    def manifest_entry_hash(entry: PilotManifestEntry) -> str:
        return PilotService._payload_hash(entry.model_dump(mode="json"))

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _file_set_hash(paths: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    @staticmethod
    def _deduplicate_issues(
        issues: list[PilotValidationIssue],
    ) -> list[PilotValidationIssue]:
        return list(dict.fromkeys(issues))

    @staticmethod
    def _portable_location(path: Path) -> str:
        return path.name or "source_registry"

    def _now(self) -> datetime:
        return self._aware(self.clock()) or datetime.now(UTC)

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _outcome(
        item: PilotCollectionItem,
        run_id: int,
        *,
        authenticity_type: str | None = None,
    ) -> PilotCollectionOutcome:
        return PilotCollectionOutcome(
            pilot_id=item.pilot_id,
            status=(
                "failed" if item.status in {"failed", "blocked_not_implemented"} else item.status
            ),
            run_id=run_id,
            collection_item_id=item.id,
            source_key=item.source_key,
            document_id=item.document_id,
            occurrence_id=item.occurrence_id,
            created=item.document_created,
            authenticity_type=authenticity_type,
            error_code=item.last_error_code,
        )

    def _collector(self, allowed_hosts: frozenset[str], allow_subdomains: bool) -> WebPageCollector:
        return WebPageCollector(
            self.settings,
            allowed_hosts=allowed_hosts,
            allow_subdomains=allow_subdomains,
        )


def collection_outcomes_jsonl(outcomes: Iterable[PilotCollectionOutcome]) -> str:
    return "".join(json.dumps(outcome.as_dict(), ensure_ascii=False) + "\n" for outcome in outcomes)


def write_collection_outcomes(path: Path, outcomes: Iterable[PilotCollectionOutcome]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(collection_outcomes_jsonl(outcomes), encoding="utf-8")
