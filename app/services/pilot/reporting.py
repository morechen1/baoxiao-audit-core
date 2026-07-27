from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentOccurrence, Penalty, ProductDocument, Regulation, SourceDocument
from app.models.enums import KnowledgeIndexStatus, ReviewStatus
from app.services.field_evidence import EVIDENCE_FIELDS
from app.services.pilot.models import PilotManifestEntry, PilotSourceType
from app.services.pilot.service import PilotService


class PilotReportService:
    def __init__(self, pilot_service: PilotService | None = None) -> None:
        self.pilot_service = pilot_service or PilotService()

    def status(
        self,
        session: Session,
        manifests_dir: Path,
        *,
        registry_dir: Path | None = None,
    ) -> dict[str, dict[str, int]]:
        entries = self._manifest_entries(manifests_dir)
        occurrences = self._pilot_occurrences(session)
        documents = {occurrence.document_id: occurrence.document for occurrence in occurrences}
        pilot_documents = {
            str(occurrence.response_metadata["pilot_id"]): occurrence.document
            for occurrence in occurrences
        }
        result: dict[str, dict[str, int]] = {}
        for source_type in PilotSourceType:
            planned = [entry for entry in entries if entry.source_type == source_type]
            docs = [
                pilot_documents[entry.pilot_id]
                for entry in planned
                if entry.pilot_id in pilot_documents
            ]
            counts = self._status_counts(planned, docs)
            counts["structured"] = sum(
                bool(self._structured_records(session, document)) for document in docs
            )
            result[source_type.value] = counts
        unlisted = [
            document
            for document in documents.values()
            if document.data_type not in {value.value for value in PilotSourceType}
        ]
        if unlisted:
            result["unclassified"] = self._status_counts([], unlisted)
        return result

    def quality_report(
        self,
        session: Session,
        manifests_dir: Path,
        *,
        registry_dir: Path | None = None,
    ) -> dict[str, Any]:
        registry_path = registry_dir or self.pilot_service.registry_dir_for(manifests_dir)
        sources, registry_issues = self.pilot_service.load_registry(registry_path)
        _, manifest_issues = self.pilot_service.validate_manifests(
            manifests_dir, registry_dir=registry_path
        )
        entries = self._manifest_entries(manifests_dir)
        occurrences = self._pilot_occurrences(session)
        pilot_occurrences = {str(item.response_metadata["pilot_id"]): item for item in occurrences}
        documents = list(
            {item.document_id: item.document for item in pilot_occurrences.values()}.values()
        )
        structured_records = [
            (document, record)
            for document in documents
            for record in self._structured_records(session, document)
        ]
        nonempty_fields = evidence_backed_fields = total_evidence_fields = 0
        for document, record in structured_records:
            fields = EVIDENCE_FIELDS.get(document.data_type, frozenset())
            evidence = record.field_evidence_json
            for field_name in fields:
                value = getattr(record, field_name)
                if value is not None and (not isinstance(value, str) or value.strip()):
                    nonempty_fields += 1
                    evidence_backed_fields += int(bool(evidence.get(field_name)))
                total_evidence_fields += 1
        source_errors: dict[str, Counter[str]] = defaultdict(Counter)
        for issue in [*registry_issues, *manifest_issues]:
            source_errors["manifest_or_registry"][issue.code] += 1
        results_path = registry_path.parent / "reports" / "pilot-collection-results.jsonl"
        for result in self._collection_results(results_path):
            if result.get("status") == "failed" and result.get("error_code"):
                source_key = str(result.get("source_key") or "manifest_or_registry")
                source_errors[source_key][str(result["error_code"])] += 1
        downloaded = len(pilot_occurrences)
        unique_documents = len(documents)
        parsed = sum(document.parse_status == "parsed" for document in documents)
        scanned = sum(document.parse_status == "requires_ocr" for document in documents)
        auto_checked = [
            document.metadata_json.get("automatic_validation", {}).get("valid")
            for document in documents
            if "automatic_validation" in document.metadata_json
        ]
        report = {
            "source_count": len(sources),
            "file_count": len(entries),
            "download_success_rate": self._ratio(downloaded, len(entries)),
            "duplicate_rate": self._ratio(downloaded - unique_documents, downloaded),
            "parse_success_rate": self._ratio(parsed, downloaded),
            "scanned_pdf_rate": self._ratio(scanned, downloaded),
            "field_completeness_rate": self._ratio(nonempty_fields, total_evidence_fields),
            "field_evidence_coverage_rate": self._ratio(evidence_backed_fields, nonempty_fields),
            "automatic_validation_pass_rate": self._ratio(
                sum(value is True for value in auto_checked), len(auto_checked)
            ),
            "pending_human_review_count": sum(
                document.final_review_status == ReviewStatus.PENDING_REVIEW.value
                for document in documents
            ),
            "errors_by_source": {
                key: dict(sorted(counts.items())) for key, counts in sorted(source_errors.items())
            },
            "status_by_type": self.status(session, manifests_dir, registry_dir=registry_path),
        }
        self._assert_portable(report)
        return report

    def write_quality_report(
        self,
        session: Session,
        manifests_dir: Path,
        output: Path,
        *,
        registry_dir: Path | None = None,
    ) -> tuple[Path, Path]:
        report = self.quality_report(session, manifests_dir, registry_dir=registry_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown_path = output.with_suffix(".md")
        markdown_path.write_text(self._markdown(report), encoding="utf-8")
        return output, markdown_path

    @staticmethod
    def _pilot_occurrences(session: Session) -> list[DocumentOccurrence]:
        return [
            occurrence
            for occurrence in session.scalars(select(DocumentOccurrence))
            if occurrence.response_metadata.get("pilot_id")
        ]

    def _manifest_entries(self, path: Path) -> list[PilotManifestEntry]:
        manifest_paths = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
        return [
            entry
            for manifest_path in manifest_paths
            for _, entry in self.pilot_service.load_manifest(manifest_path)[0]
        ]

    @staticmethod
    def _collection_results(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        results: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                results.append(value)
        return results

    @staticmethod
    def _structured_records(session: Session, document: SourceDocument) -> list[Any]:
        model: Any = {
            PilotSourceType.REGULATION.value: Regulation,
            PilotSourceType.PENALTY.value: Penalty,
            PilotSourceType.PRODUCT_DOCUMENT.value: ProductDocument,
        }.get(document.data_type)
        return (
            list(session.scalars(select(model).where(model.document_id == document.id)))
            if model
            else []
        )

    @staticmethod
    def _status_counts(
        entries: list[PilotManifestEntry], documents: list[SourceDocument]
    ) -> dict[str, int]:
        reviewed = {
            ReviewStatus.APPROVED.value,
            ReviewStatus.APPROVED_WITH_REVISION.value,
            ReviewStatus.REJECTED.value,
            ReviewStatus.PENDING_SOURCE_VERIFICATION.value,
            ReviewStatus.REJECTED_HALLUCINATION.value,
            ReviewStatus.REJECTED_DUPLICATE.value,
            ReviewStatus.REJECTED_OUTDATED.value,
            ReviewStatus.REQUIRES_EXPERT_REVIEW.value,
        }
        return {
            "planned": len(entries),
            "collected": len(documents),
            "parsed": sum(document.parse_status == "parsed" for document in documents),
            "requires_ocr": sum(document.parse_status == "requires_ocr" for document in documents),
            "structured": 0,
            "auto_validation_failed": sum(
                document.final_review_status == ReviewStatus.AUTO_VALIDATION_FAILED.value
                for document in documents
            ),
            "pending_review": sum(
                document.final_review_status == ReviewStatus.PENDING_REVIEW.value
                for document in documents
            ),
            "reviewed": sum(document.final_review_status in reviewed for document in documents),
            "indexed": sum(
                document.knowledge_index_status == KnowledgeIndexStatus.INDEXED.value
                for document in documents
            ),
        }

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    @staticmethod
    def _assert_portable(value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        if any(token in encoded for token in ("/Users/", "/home/", "\\Users\\")):
            raise ValueError("quality_report_contains_absolute_path")

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        labels = [
            ("来源数量", "source_count"),
            ("文件数量", "file_count"),
            ("下载成功率", "download_success_rate"),
            ("重复率", "duplicate_rate"),
            ("解析成功率", "parse_success_rate"),
            ("扫描 PDF 比例", "scanned_pdf_rate"),
            ("字段完整率", "field_completeness_rate"),
            ("字段证据覆盖率", "field_evidence_coverage_rate"),
            ("自动校验通过率", "automatic_validation_pass_rate"),
            ("待人工审核数量", "pending_human_review_count"),
        ]
        rows = "\n".join(f"| {label} | {report[key]} |" for label, key in labels)
        return (
            "# Pilot 真实数据质量报告\n\n"
            "| 指标 | 值 |\n"
            "| --- | ---: |\n"
            f"{rows}\n\n"
            "报告仅包含汇总指标和安全错误码，不包含本地绝对路径。\n"
        )
