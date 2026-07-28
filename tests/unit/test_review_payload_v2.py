import copy
import hashlib
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import SourceDocument
from app.models.entities import Base
from app.services.parsed_artifacts import ParsedArtifactService
from app.services.parsing.base import ParsedDocument, ParsedPage
from app.services.review.service import payload_hash
from app.services.review_payload import portable_record_key


def review_row() -> dict:
    evidence = [
        {
            "document_id": 101,
            "chunk_id": 201,
            "quote": "第一条",
            "page_number": 1,
            "start_offset": 10,
            "end_offset": 13,
            "mode": "verbatim",
        },
        {
            "document_id": 101,
            "quote": "真实内容",
            "page_number": 1,
            "start_offset": 14,
            "end_offset": 18,
            "mode": "verbatim",
        },
    ]
    records = [
        {
            "structured_record_id": 301,
            "title": "正式规则",
            "article_number": "第一条",
            "article_text": "真实内容",
            "source_quote": "真实内容",
            "field_evidence": {"article_text": list(reversed(evidence))},
            "draft_provenance": {
                "structured_record_id": 301,
                "pilot_id": "REG-001",
                "draft_generation_method": "manual_rules",
                "draft_generation_version": "v1",
            },
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "structured_record_id": 302,
            "title": "正式规则",
            "article_number": "第二条",
            "article_text": "第二条内容",
            "source_quote": "第二条内容",
            "field_evidence": {"article_text": evidence},
            "draft_provenance": {
                "structured_record_id": 302,
                "pilot_id": "REG-001",
                "draft_generation_method": "manual_rules",
                "draft_generation_version": "v1",
            },
        },
    ]
    return {
        "review_payload_schema_version": 2,
        "batch_id": 10,
        "batch_item_id": 20,
        "document_id": 101,
        "record_id": 101,
        "record_type": "regulation",
        "source_url": "https://official.example/rule",
        "final_url": "https://official.example/rule.json",
        "source_title": "正式规则",
        "publisher": "监管机构",
        "published_at": "2026-01-01",
        "sha256": "a" * 64,
        "parsed_artifact_sha256": "b" * 64,
        "parsed_text_sha256": "c" * 64,
        "parsed_from_raw_sha256": "a" * 64,
        "raw_file_path": "/tmp/database-a/raw.json",
        "parsed_artifact_path": "/tmp/database-a/parsed.json",
        "pilot_ids": ["REG-002", "REG-001"],
        "source_occurrences": [
            {
                "occurrence_id": 901,
                "source_id": 801,
                "source_url": "https://official.example/rule",
                "final_url": "https://official.example/rule.json",
                "publisher": "监管机构",
                "http_status": 200,
                "collected_at": "2026-01-01T00:00:00Z",
                "response_metadata": {"local_path": "/tmp/database-a/raw.json"},
            },
            {
                "occurrence_id": 902,
                "source_id": 802,
                "source_url": "https://mirror.example/rule",
                "final_url": "https://mirror.example/rule.json",
                "publisher": "监管机构",
                "http_status": 200,
                "collected_at": "2026-01-02T00:00:00Z",
            },
        ],
        "parsed_fields": {"records": records},
        "authenticity_type": "pending_verification",
        "current_status": "pending_review",
        "knowledge_index_status": "not_indexed",
        "can_index": False,
        "index_rejection_reasons": [
            "review_status_not_approved",
            "authenticity_not_verified_public",
        ],
    }


def test_payload_v2_ignores_database_ids_timestamps_paths_and_load_order() -> None:
    first = review_row()
    second = copy.deepcopy(first)
    second.update(
        {
            "batch_id": 110,
            "batch_item_id": 120,
            "document_id": 111,
            "record_id": 111,
            "raw_file_path": "/different/root/raw.json",
            "parsed_artifact_path": "/different/root/parsed.json",
        }
    )
    second["pilot_ids"].reverse()
    second["source_occurrences"].reverse()
    second["parsed_fields"]["records"].reverse()
    for occurrence in second["source_occurrences"]:
        occurrence["occurrence_id"] += 1000
        occurrence["source_id"] += 1000
        occurrence["collected_at"] = "2030-01-01T00:00:00Z"
    for index, record in enumerate(second["parsed_fields"]["records"], 1):
        record["structured_record_id"] += 1000
        record["created_at"] = "2030-01-01T00:00:00Z"
        record["draft_provenance"]["structured_record_id"] += 1000
        for items in record["field_evidence"].values():
            items.reverse()
            for item in items:
                item["document_id"] = 111
                item["chunk_id"] = 999 + index

    assert payload_hash(first) == payload_hash(second)


def test_payload_v2_changes_when_business_field_changes() -> None:
    first = review_row()
    second = copy.deepcopy(first)
    second["parsed_fields"]["records"][0]["article_text"] = "被篡改的内容"

    assert payload_hash(first) != payload_hash(second)

    evaluation = review_row()
    evaluation["record_type"] = "evaluation_sample"
    evaluation["raw_text"] = "原始评测话术"
    evaluation["parsed_fields"] = {
        "sample_category": "risky",
        "risk_labels": ["夸大"],
    }
    changed_evaluation = copy.deepcopy(evaluation)
    changed_evaluation["raw_text"] = "被篡改的评测话术"
    assert payload_hash(evaluation) != payload_hash(changed_evaluation)


def test_payload_v2_changes_for_every_material_evidence_component() -> None:
    baseline = review_row()
    for field, value in (
        ("quote", "被篡改"),
        ("start_offset", 11),
        ("page_number", 2),
        ("mode", "normalized"),
    ):
        changed = copy.deepcopy(baseline)
        changed["parsed_fields"]["records"][0]["field_evidence"]["article_text"][0][field] = value
        assert payload_hash(baseline) != payload_hash(changed)


def test_payload_v2_binds_artifacts_case_usage_disclosure_and_index_eligibility() -> None:
    baseline = review_row()
    changes = (
        ("sha256", "d" * 64),
        ("parsed_artifact_sha256", "e" * 64),
        ("can_index", True),
        ("index_rejection_reasons", []),
    )
    for field, value in changes:
        changed = copy.deepcopy(baseline)
        changed[field] = value
        assert payload_hash(baseline) != payload_hash(changed)
    for field, value in (
        ("case_usage", "external_test_candidate"),
        ("marketing_wording_disclosed", True),
    ):
        changed = copy.deepcopy(baseline)
        changed["parsed_fields"]["records"][0][field] = value
        assert payload_hash(baseline) != payload_hash(changed)


def test_portable_record_key_ignores_database_only_identifiers() -> None:
    row = review_row()
    record = row["parsed_fields"]["records"][0]
    changed = copy.deepcopy(record)
    changed["structured_record_id"] = 9999
    changed["draft_provenance"]["structured_record_id"] = 9999
    for item in changed["field_evidence"]["article_text"]:
        item["document_id"] = 7777
        item["chunk_id"] = 8888

    assert portable_record_key(row["sha256"], row["record_type"], record) == portable_record_key(
        row["sha256"], row["record_type"], changed
    )


def test_parsed_artifact_v2_is_stable_across_database_ids_and_parse_times(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "official.json"
    raw.write_text("不可变公开原件", encoding="utf-8")
    raw_sha256 = hashlib.sha256(raw.read_bytes()).hexdigest()
    parsed = ParsedDocument(
        title="正式标题",
        plain_text="不可变公开原件",
        pages=[ParsedPage(page_number=1, text="不可变公开原件")],
        metadata={"nfra": {"caption": "监管机构令2026年第1号"}},
    )

    def persist(database_name: str, *, add_dummy: bool) -> tuple[int, str]:
        engine = create_engine(f"sqlite:///{tmp_path / database_name}")
        Base.metadata.create_all(engine)
        data_dir = tmp_path / f"{database_name}-data"
        managed_raw = data_dir / "raw" / "official.json"
        managed_raw.parent.mkdir(parents=True)
        managed_raw.write_bytes(raw.read_bytes())
        with Session(engine, expire_on_commit=False) as session:
            if add_dummy:
                session.add(
                    SourceDocument(
                        data_type="regulation",
                        source_url="https://official.example/dummy",
                        raw_file_path=str(managed_raw),
                        sha256="f" * 64,
                    )
                )
                session.flush()
            document = SourceDocument(
                data_type="regulation",
                source_url="https://official.example/rule",
                raw_file_path=str(managed_raw),
                sha256=raw_sha256,
            )
            session.add(document)
            session.flush()
            ParsedArtifactService(Settings(data_dir=data_dir)).persist(
                session,
                document,
                parsed,
                parser_name="NfraJsonParser",
            )
            return document.id, str(document.parsed_artifact_sha256)

    first_id, first_hash = persist("database-a.sqlite", add_dummy=False)
    second_id, second_hash = persist("database-b.sqlite", add_dummy=True)

    assert first_id != second_id
    assert first_hash == second_hash
