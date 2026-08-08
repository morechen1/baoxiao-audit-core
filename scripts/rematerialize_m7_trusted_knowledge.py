#!/usr/bin/env python3
"""Re-materialize audited M7 knowledge from the five local immutable archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import (
    AuthenticityDecisionLog,
    DataSource,
    DocumentOccurrence,
    Penalty,
    PilotCollectionItem,
    PilotCollectionRun,
    PilotSourceRegistration,
    ProductDocument,
    Regulation,
    ReviewBatch,
    ReviewBatchItem,
    ReviewDecision,
    SourceDocument,
)
from app.services.knowledge.service import KnowledgeIndexService
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.penalty_entries import (
    PENALTY_IDENTITY_FIELDS,
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
)
from app.services.review_payload import canonical_json_bytes

ARCHIVES = {
    "pilot": (
        Path(
            "/Users/morechen/项目/金科创 2/一队/自命题/"
            "baoxiao-pilot-first-batch-v1-success-final-30365518099/"
            "baoxiao-pilot-first-batch-v1-success.zip"
        ),
        "7f0ede6b67e2c8d571f8898f436aaee5311fd665493e8433592787482cc8eb10",
        "baoxiao-pilot-first-batch-v1-success",
    ),
    "structured": (
        Path(
            "/Users/morechen/项目/金科创 2/一队/自命题/"
            "baoxiao-first-batch-structured-review-work-v2/"
            "baoxiao-first-batch-structured-review-v2.zip"
        ),
        "f50eefc2f30fcd1d7fa533f98fa19157ef355cbb076caafb663e3050e8c58456",
        "baoxiao-first-batch-structured-review-v2",
    ),
    "penalty_review": (
        Path(
            "/Users/morechen/项目/金科创 2/一队/自命题/"
            "penalty-v3.3-review-input/baoxiao-first-batch-penalty-review-v3.3.zip"
        ),
        "4feffbed7ffae52b93b90dc3fba2ef58213d12483cc1ba00173fb18b8d5c88aa",
        "baoxiao-first-batch-penalty-review-v3.3",
    ),
    "post": (
        Path(
            "/Users/morechen/项目/金科创 2/一队/自命题/"
            "baoxiao-first-batch-post-review-work-v2.1/"
            "baoxiao-first-batch-post-review-v2.1.zip"
        ),
        "09b7c1c1fa35aeda8eabe87405f897d9f838681135ef98c1d4e979fc6ad51e48",
        "baoxiao-first-batch-post-review-v2.1",
    ),
    "penalty_post": (
        Path(
            "/Users/morechen/项目/金科创 2/一队/自命题/"
            "penalty-v3.3-post-review-execution-20260801/"
            "baoxiao-first-batch-penalty-post-review-v3.3.zip"
        ),
        "4b9dd8e5938d1114d66478e90ef25e9beaff6ab00e60245b926a6234ca76389f",
        "baoxiao-first-batch-penalty-post-review-v3.3",
    ),
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def dt(value: str | None) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(UTC)


def day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def safe_extract(bundle: Path, destination: Path) -> None:
    with zipfile.ZipFile(bundle) as source:
        for member in source.infolist():
            path = Path(member.filename)
            kind = (member.external_attr >> 16) & 0o170000
            if path.is_absolute() or ".." in path.parts or kind not in {0, 0o040000, 0o100000}:
                raise ValueError(f"unsafe archive member: {member.filename}")
        source.extractall(destination)


def extract_archives(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for label, (bundle, expected, top) in ARCHIVES.items():
        if not bundle.is_file() or sha(bundle) != expected:
            raise ValueError(f"archive integrity failure: {bundle}")
        target = root / label
        target.mkdir()
        safe_extract(bundle, target)
        result[label] = target / top
        if not result[label].is_dir():
            raise ValueError(f"archive layout failure: {bundle}")
    return result


def evidence_current_id(payload: dict[str, Any], document_id: int) -> dict[str, Any]:
    copied = json.loads(json.dumps(payload, ensure_ascii=False))
    for values in copied.values():
        for value in values:
            if "document_id" in value:
                value["document_id"] = document_id
    return copied


def current_records(structured: Path, post: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in (structured / "structured-drafts").glob("*.jsonl"):
        for row in rows(path):
            result[row["pilot_id"]].append(row)
    edits = {
        (x["pilot_id"], x["portable_record_key"]): x for x in rows(post / "revised-records.jsonl")
    }
    for pilot_id, records in result.items():
        for record in records:
            edit = edits.get((pilot_id, record.get("portable_record_key")))
            if edit:
                record["fields"].update(edit["fields"])
                record["field_evidence"].update(edit["field_evidence"])
    return result


def migrate(settings: Settings) -> dict[str, Any]:
    parsed_url = make_url(settings.database_url)
    if (
        parsed_url.get_backend_name() != "postgresql"
        or parsed_url.database != "baoxiao_semantic_dev"
    ):
        raise ValueError("requires isolated baoxiao_semantic_dev database")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with tempfile.TemporaryDirectory(prefix="m7-rematerialization-") as temp:
        archive = extract_archives(Path(temp))
        pilot = archive["pilot"]
        mapping = {
            x["pilot_id"]: x
            for x in json.loads((pilot / "mapping/pilot-document-mapping.json").read_text())
        }
        manifests = {
            x["pilot_id"]: x for p in (pilot / "manifests").glob("*.jsonl") for x in rows(p)
        }
        registry = {
            entry["source_key"]: entry
            for p in (pilot / "source-registry").glob("*.yaml")
            for entry in yaml.safe_load(p.read_text())["sources"]
        }
        final_records = current_records(archive["structured"], archive["post"])
        review = {
            x["pilot_id"]: x
            for p in (archive["post"] / "review-decisions-v2").glob("*.jsonl")
            for x in rows(p)
        }
        review.update(
            {
                x["pilot_id"]: x
                for x in rows(archive["penalty_post"] / "final-state/review-decisions.jsonl")
            }
        )
        eligibility = {x["pilot_id"]: x for x in rows(archive["post"] / "index-eligibility.jsonl")}
        eligibility.update(
            {
                x["pilot_id"]: x
                for x in rows(archive["penalty_post"] / "final-state/index-eligibility.jsonl")
            }
        )
        penalty_rows = defaultdict(list)
        for value in rows(archive["penalty_post"] / "final-state/revised-penalty-records.jsonl"):
            penalty_rows[value["pilot_id"]].append(value)
        penalty_evidence: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for value in rows(archive["penalty_post"] / "final-state/field-evidence.jsonl"):
            penalty_evidence[(value["pilot_id"], value["portable_record_key"])][
                value["field_name"]
            ].append(value["evidence"])
        provenance_by_index: dict[tuple[str, int], dict[str, Any]] = {}
        for value in json.loads(
            (archive["penalty_review"] / "source-entry-mapping/all-entries.json").read_text()
        ):
            provenance_by_index[(value["pilot_id"], value["source_entry_index"])] = value

        eligible = sorted(x for x, state in eligibility.items() if state["can_index"])
        expected = [f"PEN-{i:03d}" for i in range(1, 11)] + [
            "PROD-001",
            "PROD-002",
            "REG-001",
            "REG-002",
            "REG-003",
        ]
        if eligible != expected:
            raise ValueError("final archive eligibility mismatch")

        with factory() as session:
            if session.scalar(select(func.count()).select_from(SourceDocument)):
                raise ValueError("recovery database is not empty")
            source_ids, registration_ids = {}, {}
            for key, entry in registry.items():
                source = DataSource(
                    name=entry["name"],
                    base_url=entry["base_url"],
                    publisher=entry["publisher"],
                    source_type=entry["source_type"],
                    enabled=True,
                    crawl_policy=entry["crawl_policy"],
                    rate_limit_seconds=float(entry["crawl_policy"]["rate_limit_seconds"]),
                )
                session.add(source)
                session.flush()
                source_ids[key] = source.id
                approval, robots, terms = (
                    entry["approval"],
                    entry["robots_review"],
                    entry["terms_review"],
                )
                registration = PilotSourceRegistration(
                    data_source_id=source.id,
                    source_key=key,
                    version=1,
                    entry_sha256=hashlib.sha256(canonical_json_bytes(entry)).hexdigest(),
                    name=entry["name"],
                    publisher=entry["publisher"],
                    base_url=entry["base_url"],
                    source_type=entry["source_type"],
                    allowed_domains_json=entry["allowed_domains"],
                    allow_subdomains=entry["crawl_policy"]["allow_subdomains"],
                    rate_limit_seconds=float(entry["crawl_policy"]["rate_limit_seconds"]),
                    max_documents=int(entry["crawl_policy"]["max_documents"]),
                    confirmed_by=entry["confirmed_by"],
                    approved_at=dt(approval["approved_at"]),
                    approval_reference=approval["approval_reference"],
                    robots_review_status=robots["status"],
                    robots_checked_at=day(robots["checked_at"]),
                    robots_checked_by=robots["checked_by"],
                    robots_reference_url=robots.get("reference_url"),
                    robots_notes=robots["notes"],
                    terms_review_status=terms["status"],
                    terms_checked_at=day(terms["checked_at"]),
                    terms_checked_by=terms["checked_by"],
                    terms_reference_url=terms.get("reference_url"),
                    terms_notes=terms["notes"],
                    notes=entry["notes"],
                )
                session.add(registration)
                session.flush()
                registration_ids[key] = registration.id
            run = PilotCollectionRun(
                run_uuid="m7-rematerialization-20260808",
                selected_manifest="first-batch-v1",
                manifest_set_sha256=hashlib.sha256(b"m7-rematerialization-manifests").hexdigest(),
                source_registry_set_sha256=hashlib.sha256(
                    b"m7-rematerialization-registry"
                ).hexdigest(),
                status="completed",
                requested_by="m7_knowledge_rematerialization",
                planned_count=15,
                success_count=15,
                failure_count=0,
                skipped_count=0,
                completed_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            docs: dict[str, SourceDocument] = {}
            occurrences: dict[str, DocumentOccurrence] = {}
            sidecar: list[dict[str, Any]] = []
            for pilot_id in eligible:
                manifest, legacy = manifests[pilot_id], mapping[pilot_id]
                raw = pilot / legacy["raw_relative_path"]
                old_artifact = json.loads((pilot / legacy["parsed_relative_path"]).read_text())
                if (
                    sha(raw) != legacy["raw_sha256"]
                    or sha(pilot / legacy["parsed_relative_path"])
                    != legacy["parsed_artifact_sha256"]
                ):
                    raise ValueError(f"artifact provenance mismatch: {pilot_id}")
                raw_dir = settings.data_dir / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_target = raw_dir / f"{legacy['raw_sha256']}{raw.suffix}"
                shutil.copyfile(raw, raw_target)
                status = eligibility[pilot_id].get(
                    "final_review_status",
                    review[pilot_id].get("final_status", review[pilot_id].get("decision")),
                )
                metadata = old_artifact.get("metadata", {})
                document = SourceDocument(
                    source_id=source_ids[manifest["source_key"]],
                    data_type=manifest["source_type"],
                    source_url=manifest["source_url"],
                    final_url=legacy["final_url"],
                    source_title=old_artifact.get("title") or manifest["expected_title"],
                    publisher=metadata.get("publisher"),
                    published_at=day(metadata.get("published_at")),
                    content_type=legacy["content_type"],
                    raw_file_path=str(raw_target),
                    raw_text=old_artifact["plain_text"],
                    sha256=legacy["raw_sha256"],
                    authenticity_type="verified_public",
                    collection_status="collected",
                    parse_status="parsed",
                    final_review_status=status,
                    knowledge_index_status="indexed",
                    metadata_json={
                        "automatic_validation": {"valid": True},
                        "legacy_logical_id": pilot_id,
                        "legacy_document_id": legacy["document_id"],
                        "legacy_parsed_sha256": legacy["parsed_artifact_sha256"],
                        "legacy_parsed_text_sha256": legacy["parsed_text_sha256"],
                        "provenance": "rematerialized_from_verified_archive",
                    },
                )
                session.add(document)
                session.flush()
                current_metadata = {
                    **metadata,
                    "provenance": "rematerialized_from_verified_archive",
                }
                if metadata.get("source_format") == "nfra_public_json" and metadata.get("doc_id"):
                    current_metadata["nfra"] = {"doc_id": str(metadata["doc_id"])}
                parsed = ParsedDocument(
                    title=old_artifact["title"],
                    plain_text=old_artifact["plain_text"],
                    pages=[
                        ParsedPage(page_number=x["page_number"], text=x["text"])
                        for x in old_artifact["pages"]
                    ],
                    metadata=current_metadata,
                    warnings=list(old_artifact.get("warnings", [])),
                )
                ParsedArtifactService(settings).persist(
                    session,
                    document,
                    parsed,
                    parser_name=old_artifact["parser_name"],
                    parser_version=old_artifact["parser_version"],
                )
                occurrence = DocumentOccurrence(
                    document_id=document.id,
                    source_id=document.source_id,
                    source_url=document.source_url,
                    final_url=document.final_url,
                    publisher=document.publisher,
                    http_status=legacy["http_status"],
                    response_metadata={"provenance": "rematerialized_from_verified_archive"},
                )
                session.add(occurrence)
                session.flush()
                session.add(
                    PilotCollectionItem(
                        run_id=run.id,
                        source_registration_id=registration_ids[manifest["source_key"]],
                        pilot_id=pilot_id,
                        source_key=manifest["source_key"],
                        source_type=manifest["source_type"],
                        source_url=manifest["source_url"],
                        expected_title=manifest.get("expected_title"),
                        evaluation_usage_json=manifest.get("evaluation_usage", []),
                        confirmed_by=manifest["confirmed_by"],
                        approval_reference=manifest["approval_reference"],
                        approved_at=dt(manifest["approved_at"]),
                        manifest_entry_sha256=hashlib.sha256(
                            canonical_json_bytes(manifest)
                        ).hexdigest(),
                        source_registry_entry_sha256=hashlib.sha256(
                            canonical_json_bytes(registry[manifest["source_key"]])
                        ).hexdigest(),
                        status="collected",
                        document_id=document.id,
                        occurrence_id=occurrence.id,
                        document_created=True,
                        attempt_count=1,
                        collected_at=datetime.now(UTC),
                    )
                )
                docs[pilot_id], occurrences[pilot_id] = document, occurrence
                sidecar.append(
                    {
                        "legacy_logical_id": pilot_id,
                        "legacy_document_id": legacy["document_id"],
                        "current_document_id": document.id,
                        "raw_sha256": document.sha256,
                        "legacy_parsed_sha256": legacy["parsed_artifact_sha256"],
                        "current_parsed_sha256": document.parsed_artifact_sha256,
                    }
                )
            record_count = 0
            for pilot_id, document in docs.items():
                if document.data_type == "penalty":
                    provenance = []
                    for row in penalty_rows[pilot_id]:
                        fields = {
                            k: v
                            for k, v in row.items()
                            if k
                            not in {
                                "pilot_id",
                                "document_id",
                                "portable_record_key",
                                "field_evidence_count",
                                "final_review_status",
                            }
                        }
                        fields["source_quote"] = final_records[pilot_id][0]["fields"][
                            "source_quote"
                        ]
                        record = Penalty(
                            document_id=document.id,
                            **fields,
                            field_evidence_json=evidence_current_id(
                                penalty_evidence[(pilot_id, row["portable_record_key"])],
                                document.id,
                            ),
                            final_review_status=row["final_review_status"],
                        )
                        session.add(record)
                        session.flush()
                        identity_fields = {
                            field: getattr(record, field) for field in PENALTY_IDENTITY_FIELDS
                        }
                        current_content_sha = source_entry_content_sha256(
                            build_penalty_identity_material(
                                identity_fields, record.field_evidence_json
                            )
                        )
                        record.source_entry_fingerprint = penalty_source_entry_fingerprint(
                            raw_artifact_sha256=document.sha256,
                            source_entry_content_sha256=current_content_sha,
                        )
                        record_count += 1
                        item = dict(provenance_by_index[(pilot_id, row["source_entry_index"])])
                        item.pop("identity_material", None)
                        item["legacy_source_entry_fingerprint"] = item["source_entry_fingerprint"]
                        item["source_entry_fragments"] = build_penalty_source_entry_fragments(
                            identity_fields, record.field_evidence_json
                        )
                        item["source_entry_content_sha256"] = current_content_sha
                        item["source_entry_fingerprint"] = record.source_entry_fingerprint
                        item["record_type"] = "penalty"
                        item["structured_record_id"] = record.id
                        item["source_identity_status"] = "verified"
                        provenance.append(item)
                    document.metadata_json = {
                        **document.metadata_json,
                        "structured_draft_provenance": provenance,
                    }
                else:
                    model = Regulation if document.data_type == "regulation" else ProductDocument
                    for row in final_records[pilot_id]:
                        fields = dict(row["fields"])
                        for field in ("effective_date", "expiry_date"):
                            if isinstance(fields.get(field), str):
                                fields[field] = day(fields[field])
                        session.add(
                            model(
                                document_id=document.id,
                                **fields,
                                field_evidence_json=evidence_current_id(
                                    row["field_evidence"], document.id
                                ),
                                final_review_status=document.final_review_status,
                            )
                        )
                        record_count += 1
            session.flush()
            for pilot_id, document in docs.items():
                historic = review[pilot_id]
                batch = ReviewBatch(
                    batch_name=f"migrated-{pilot_id}-review",
                    data_type=document.data_type,
                    record_count=1,
                    export_path=f"archive://{pilot_id}",
                    export_sha256=historic["reviewed_payload_hash"],
                    schema_version=historic["schema_version"],
                    status="completed",
                    completed_at=dt(historic.get("reviewed_at")),
                )
                session.add(batch)
                session.flush()
                decision = ReviewDecision(
                    batch_id=batch.id,
                    record_type=document.data_type,
                    record_id=document.id,
                    decision=document.final_review_status,
                    field_reviews_json=historic.get("field_reviews", {}),
                    corrections_json=historic.get("corrections", {}),
                    evidence_quality=historic["evidence_quality"],
                    review_comment=historic.get("review_comment"),
                    reviewer=historic["reviewer"],
                    reviewed_payload_hash=historic["reviewed_payload_hash"],
                    schema_version=historic["schema_version"],
                    reviewed_at=dt(historic.get("reviewed_at")),
                )
                session.add(decision)
                session.flush()
                session.add(
                    ReviewBatchItem(
                        batch_id=batch.id,
                        record_type=document.data_type,
                        record_id=document.id,
                        document_id=document.id,
                        exported_status="parsed",
                        payload_hash=historic["reviewed_payload_hash"],
                        decision_id=decision.id,
                    )
                )
                session.add(
                    AuthenticityDecisionLog(
                        document_id=document.id,
                        previous_authenticity_type="pending_verification",
                        new_authenticity_type="verified_public",
                        reviewer=historic["reviewer"],
                        review_decision_id=decision.id,
                        source_id=document.source_id,
                        verified_occurrence_id=occurrences[pilot_id].id,
                        reason=(historic.get("authenticity_decision") or {}).get("reason")
                        or "migrated_from_verified_archive",
                        changed_at=dt(historic.get("reviewed_at")),
                    )
                )
            session.commit()
            sidecar_path = settings.data_dir / "m7_legacy_current_identity_map.json"
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n")
            index = KnowledgeIndexService(settings)
            summary = index.rebuild_all_trusted_chunks(session)
            session.expire_all()
            report = index.verify_chunks(session)
            if summary.documents != 15 or summary.errors or not report.valid:
                raise ValueError(f"trust gate failed: {report.errors}")
            return {
                "documents": len(docs),
                "records": record_count,
                "chunks": report.active_chunks,
                "types": report.chunks_by_record_type,
                "per_document": report.chunks_by_document,
                "sidecar": str(sidecar_path),
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(Settings(database_url=args.database_url, data_dir=args.data_dir)),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
