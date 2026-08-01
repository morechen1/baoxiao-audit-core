from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    FindingEvidenceLink,
    KnowledgeChunk,
    SourceDocument,
)
from app.services.knowledge import KnowledgeIndexService
from app.services.screening import DeterministicScreeningService, MarketingRuleSet, load_ruleset
from app.services.screening.evidence import SUPPORT_EVALUATION_VERSION
from app.services.screening.normalization import normalize_marketing_text
from app.services.screening.reports import (
    ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE,
    canonical_report_sha256,
)

NON_PENALTY_ARCHIVE_SHA256 = "09b7c1c1fa35aeda8eabe87405f897d9f838681135ef98c1d4e979fc6ad51e48"
PENALTY_ARCHIVE_SHA256 = "4b9dd8e5938d1114d66478e90ef25e9beaff6ab00e60245b926a6234ca76389f"
FORBIDDEN_NORMATIVE_CHUNKS = {
    "regulatory_endorsement": {
        "cb306ddab3d697dbc49df233d9dc568c6934f2555abd0df670f3931da4efe960",
        "5597f2f9eecccf988d346961600335b92e14272490620cd946af183c3527e04d",
    },
    "extra_contractual_benefit": {
        "c29eb875a6f8862f2b3fb95c89068277b17bf0d38abdd009a474b01d487fc7ff",
    },
    "product_nature_confusion": {
        "61f03550569d7cfd7231622707aba3582175f8853ac3f7862fb0dd499dd88a6f",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--comparison-database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--json-report", type=Path, required=True)
    parser.add_argument("--markdown-report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    settings = Settings(database_url=args.database_url, data_dir=args.data_dir)
    engine = create_engine(args.database_url)
    ruleset = load_ruleset()
    service = DeterministicScreeningService(ruleset, settings)
    with Session(engine, expire_on_commit=False) as session:
        verification = KnowledgeIndexService(settings).verify_chunks(session)
        if not verification.valid or verification.active_chunks != 73:
            raise SystemExit("screening_trusted_index_invalid")
        before = _trusted_counts(session)
        sample_rows: list[dict[str, Any]] = []
        exact_rule_matches = 0
        exact_span_matches = 0
        expected_findings = 0
        actual_findings = 0
        supported = 0
        partial = 0
        insufficient = 0
        deterministic = True
        institution_valid = True
        consumer_valid = True
        evidence_links_total = 0
        evidence_links_passed = 0
        evidence_links_rejected = 0
        status_matches_expected = 0
        expected_status_findings = 0
        enforcement_semantically_matched = 0
        unrelated_enforcement_selected = 0
        product_context_material_matched = 0
        product_context_illustrative = 0
        first_run_ids: dict[str, int] = {}
        canonical_report_hashes: dict[str, dict[str, str]] = {}
        selected_evidence_links = 0
        selected_evidence_links_semantically_valid = 0
        selected_irrelevant_links = 0
        forbidden_normative_links = 0
        matched_pattern_groups_valid = True
        for sample in samples:
            text = _sample_text(sample)
            kwargs = {
                "title": f"构造评估 {sample['sample_id']}",
                "material_type": "sales_script",
                "raw_text": text,
                "source_label": "constructed_screening_eval_v1",
                "external_reference": sample["sample_id"],
                "is_constructed_evaluation": True,
            }
            first = service.run(session, **kwargs)
            first_run_ids[sample["sample_id"]] = first.id
            first_detail = service.show(session, first.id)
            second = service.run(session, **kwargs)
            second_detail = service.show(session, second.id)
            first_rows = first_detail["findings"]
            second_rows = second_detail["findings"]
            first_rules = sorted({row["rule_id"] for row in first_rows})
            rules_match = first_rules == sorted(sample["expected_rule_ids"])
            actual_spans = [
                {
                    "rule_id": row["rule_id"],
                    "start": row["raw_start_offset"],
                    "end": row["raw_end_offset"],
                    "text": row["matched_text"],
                }
                for row in first_rows
            ]
            spans_match = actual_spans == sample["expected_spans"]
            rerun_match = (
                first.run_payload_sha256 == second.run_payload_sha256
                and [row["finding_sha256"] for row in first_rows]
                == [row["finding_sha256"] for row in second_rows]
                and first_rows == second_rows
            )
            exact_rule_matches += int(rules_match)
            exact_span_matches += int(spans_match)
            expected_findings += len(sample["expected_spans"])
            actual_findings += len(first_rows)
            deterministic &= rerun_match
            statuses = [row["evidence_status"] for row in first_rows]
            supported += statuses.count("supported")
            partial += statuses.count("partially_supported")
            insufficient += statuses.count("evidence_insufficient")
            expected_status = sample.get("expected_evidence_status")
            if expected_status is not None:
                expected_status_findings += len(first_rows)
                status_matches_expected += sum(status == expected_status for status in statuses)
            actual_pilot_ids = {
                str(link["source"]["pilot_id"])
                for row in first_rows
                for link in row["evidence"]
                if link["source"].get("pilot_id")
            }
            actual_chunk_identities = {
                str(link["chunk_identity_sha256"]) for row in first_rows for link in row["evidence"]
            }
            expected_chunk_identities = set(sample.get("expected_support_chunk_identities", []))
            forbidden_chunk_identities = set(sample.get("forbidden_support_chunk_identities", []))
            chunk_expectations_match = expected_chunk_identities.issubset(
                actual_chunk_identities
            ) and not forbidden_chunk_identities.intersection(actual_chunk_identities)
            expected_pilot_ids = set(sample.get("expected_support_pilot_ids", []))
            forbidden_pilot_ids = set(sample.get("forbidden_support_pilot_ids", []))
            pilot_expectations_match = expected_pilot_ids.issubset(
                actual_pilot_ids
            ) and not forbidden_pilot_ids.intersection(actual_pilot_ids)
            unrelated_enforcement_selected += sum(
                link["support_type"] == "enforcement_example"
                and str(link["source"].get("pilot_id")) in forbidden_pilot_ids
                for row in first_rows
                for link in row["evidence"]
            )
            required_support_types_match = (
                all(
                    tuple(row["rule_snapshot"]["evidence_requirements"])
                    == tuple(sample.get("required_support_types", ()))
                    for row in first_rows
                )
                if first_rows
                else True
            )
            actual_product_scopes = [
                link["context_scope"]
                for row in first_rows
                for link in row["evidence"]
                if link["support_type"] == "product_term_context"
            ]
            expected_product_scope = sample.get("expected_product_context_scope")
            product_scope_match = (
                not actual_product_scopes
                if expected_product_scope is None
                else bool(actual_product_scopes)
                and set(actual_product_scopes) == {expected_product_scope}
            )
            first_evidence = [link for row in first_rows for link in row["evidence"]]
            forbidden_normative_links += sum(
                link["support_type"] == "normative_basis"
                and str(link["chunk_identity_sha256"])
                in FORBIDDEN_NORMATIVE_CHUNKS.get(str(row["rule_id"]), set())
                for row in first_rows
                for link in row["evidence"]
            )
            matched_pattern_groups_valid &= all(
                _matched_groups_complete(row, link)
                for row in first_rows
                for link in row["evidence"]
            )
            evidence_links_total += len(first_evidence)
            evidence_links_passed += sum(
                bool(link["support_evaluation_passed"]) for link in first_evidence
            )
            evidence_links_rejected += int(
                first.evidence_evaluation_summary_json["candidates_rejected"]
            )
            enforcement_semantically_matched += sum(
                link["support_type"] == "enforcement_example" and link["support_evaluation_passed"]
                for link in first_evidence
            )
            product_context_material_matched += actual_product_scopes.count(
                "material_product_matched"
            )
            product_context_illustrative += actual_product_scopes.count(
                "illustrative_not_material_specific"
            )
            institution = service.institution_report(session, first.id)
            consumer = service.consumer_notice(session, first.id)
            summary = institution["evidence_summary"]
            selected_evidence_links += int(summary["selected_evidence_links"])
            selected_evidence_links_semantically_valid += int(
                summary["selected_evidence_links_semantically_valid"]
            )
            selected_irrelevant_links += int(summary["selected_irrelevant_links"])
            canonical_report_hashes[str(sample["sample_id"])] = {
                "institution_report": canonical_report_sha256(institution),
                "consumer_notice": canonical_report_sha256(consumer),
            }
            institution_valid &= (
                institution["summary"]["finding_count"] == len(first_rows)
                and "不构成违法认定" in institution["disclaimer"]
            )
            consumer_valid &= "不构成违法认定" in consumer["disclaimer"] and all(
                link["source_url"] for link in consumer["evidence_links"]
            )
            if expected_product_scope == "illustrative_not_material_specific":
                institution_valid &= (
                    institution["product_context_notice"] == ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE
                )
                consumer_valid &= (
                    consumer["product_context_notice"] == ILLUSTRATIVE_PRODUCT_CONTEXT_NOTICE
                )
            sample_rows.append(
                {
                    "sample_id": sample["sample_id"],
                    "text": text,
                    "expected_rule_ids": sample["expected_rule_ids"],
                    "actual_rule_ids": first_rules,
                    "exact_rule_match": rules_match,
                    "expected_spans": sample["expected_spans"],
                    "actual_spans": actual_spans,
                    "exact_span_match": spans_match,
                    "findings": first_rows,
                    "deterministic_rerun": rerun_match,
                    "evidence_statuses": statuses,
                    "expected_evidence_status": expected_status,
                    "evidence_status_match": (
                        all(status == expected_status for status in statuses)
                        if expected_status is not None
                        else True
                    ),
                    "actual_support_pilot_ids": sorted(actual_pilot_ids),
                    "support_pilot_expectations_match": pilot_expectations_match,
                    "actual_support_chunk_identities": sorted(actual_chunk_identities),
                    "support_chunk_expectations_match": chunk_expectations_match,
                    "required_support_types_match": required_support_types_match,
                    "product_context_scopes": actual_product_scopes,
                    "product_context_scope_match": product_scope_match,
                }
            )
        historical_snapshot_pass = _historical_snapshot_check(
            session,
            ruleset,
            first_run_ids["S044"],
        )
        after = _trusted_counts(session)
        regulatory_case_evidence = session.scalar(
            select(func.count(FindingEvidenceLink.id))
            .join(
                KnowledgeChunk,
                KnowledgeChunk.id == FindingEvidenceLink.knowledge_chunk_id,
            )
            .where(KnowledgeChunk.record_type == "regulatory_case")
        )
        constructed_in_source = session.scalar(
            select(func.count(SourceDocument.id)).where(
                SourceDocument.source_title.like("构造评估%")
            )
        )
        cross_database_report = _cross_database_report_check(
            args.comparison_database_url,
            args.data_dir,
            samples,
            canonical_report_hashes,
        )
        report = {
            "schema_version": "deterministic-screening-acceptance-v3",
            "archive_inputs": {
                "non_penalty_post_review_v2_1_sha256": NON_PENALTY_ARCHIVE_SHA256,
                "penalty_post_review_v3_3_sha256": PENALTY_ARCHIVE_SHA256,
            },
            "ruleset_version": ruleset.ruleset_version,
            "ruleset_sha256": ruleset.sha256,
            "trusted_index_payload_hash": (
                service.show(session, first.id)["trusted_index_payload_hash"]
                if sample_rows
                else None
            ),
            "trusted_index_verification": {
                "valid": verification.valid,
                "active_chunks": verification.active_chunks,
                "chunks_by_record_type": verification.chunks_by_record_type,
                "regulatory_case_active_chunks": verification.regulatory_case_active_chunks,
            },
            "evaluation_sample_count": len(samples),
            "expected_finding_count": expected_findings,
            "actual_finding_count": actual_findings,
            "exact_rule_match_count": exact_rule_matches,
            "unexpected_findings": [
                row["sample_id"] for row in sample_rows if not row["exact_rule_match"]
            ],
            "missing_findings": [
                row["sample_id"] for row in sample_rows if not row["exact_span_match"]
            ],
            "exact_span_match_count": exact_span_matches,
            "evidence_supported_count": supported,
            "evidence_partially_supported_count": partial,
            "evidence_insufficient_count": insufficient,
            "evidence_semantic_evaluation_version": SUPPORT_EVALUATION_VERSION,
            "evidence_links_total": evidence_links_total,
            "evidence_links_passed": evidence_links_passed,
            "evidence_links_rejected_as_irrelevant": evidence_links_rejected,
            "selected_evidence_links": selected_evidence_links,
            "selected_evidence_links_semantically_valid": (
                selected_evidence_links_semantically_valid
            ),
            "selected_irrelevant_links": selected_irrelevant_links,
            "forbidden_normative_links_selected": forbidden_normative_links,
            "matched_pattern_groups_validation": matched_pattern_groups_valid,
            "supported_count": supported,
            "partially_supported_count": partial,
            "status_matches_expected_count": status_matches_expected,
            "status_expected_finding_count": expected_status_findings,
            "enforcement_examples_semantically_matched": enforcement_semantically_matched,
            "unrelated_enforcement_examples_selected": unrelated_enforcement_selected,
            "product_context_material_matched": product_context_material_matched,
            "product_context_illustrative": product_context_illustrative,
            "historical_report_snapshot_pass": historical_snapshot_pass,
            "local_context_adversarial_pass": all(
                row["exact_rule_match"] and row["exact_span_match"]
                for row in sample_rows
                if "S031" <= row["sample_id"] <= "S038"
            ),
            "strong_boundary_and_negation_pass": all(
                row["exact_rule_match"] and row["exact_span_match"]
                for row in sample_rows
                if "S045" <= row["sample_id"] <= "S060"
            ),
            "cross_database_report_stability": cross_database_report,
            "long_segment_boundary_pass": all(
                row["exact_rule_match"] and row["exact_span_match"] and row["deterministic_rerun"]
                for row in sample_rows
                if "S039" <= row["sample_id"] <= "S041"
            ),
            "nfkc_combining_cluster_pass": (
                normalize_marketing_text("e\u0301保证收益").text == "é保证收益"
            ),
            "institution_report_validation": institution_valid,
            "consumer_notice_validation": consumer_valid,
            "regulatory_case_evidence_count": regulatory_case_evidence,
            "deterministic_rerun_result": deterministic,
            "constructed_source_document_count": constructed_in_source,
            "trusted_counts_before": before,
            "trusted_counts_after": after,
            "trusted_knowledge_unchanged": before == after,
            "samples": sample_rows,
        }
        sensitive_matches = _sensitive_matches(
            json.dumps(report, ensure_ascii=False, sort_keys=True)
        )
        report["sensitive_data_scan"] = {
            "status": "passed" if not sensitive_matches else "failed",
            "scope": "constructed samples and generated reports",
            "matches": sensitive_matches,
        }
    failures = [
        exact_rule_matches != len(samples),
        exact_span_matches != len(samples),
        not deterministic,
        not institution_valid,
        not consumer_valid,
        regulatory_case_evidence != 0,
        constructed_in_source != 0,
        before != after,
        bool(report["sensitive_data_scan"]["matches"]),
        status_matches_expected != expected_status_findings,
        unrelated_enforcement_selected != 0,
        not all(row["support_pilot_expectations_match"] for row in sample_rows),
        not all(row["support_chunk_expectations_match"] for row in sample_rows),
        not all(row["required_support_types_match"] for row in sample_rows),
        not all(row["product_context_scope_match"] for row in sample_rows),
        not historical_snapshot_pass,
        not report["local_context_adversarial_pass"],
        not report["long_segment_boundary_pass"],
        not report["nfkc_combining_cluster_pass"],
        selected_irrelevant_links != 0,
        forbidden_normative_links != 0,
        not matched_pattern_groups_valid,
        not cross_database_report["passed"],
    ]
    if any(failures):
        raise SystemExit("deterministic_screening_acceptance_failed")
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_report.write_text(_markdown(report), encoding="utf-8")


def _sample_text(sample: dict[str, Any]) -> str:
    if "text" in sample:
        return str(sample["text"])
    builder = sample["text_builder"]
    return str(builder["prefix"]) * int(builder["repeat"]) + str(builder["suffix"])


def _matched_groups_complete(row: dict[str, Any], link: dict[str, Any]) -> bool:
    matcher = row["rule_snapshot"]["evidence_matchers"][link["support_type"]]
    required_groups = matcher.get("required_all_pattern_groups", [])
    actual_groups = link["matched_pattern_groups"]
    return (
        len(actual_groups) == len(required_groups)
        and all(group["matched_patterns"] for group in actual_groups)
        and bool(link["actual_matched_substrings"])
        and float(link["semantic_support_score"]) == 1.0
        and link["semantic_support_reason"] == "all_declared_semantic_patterns_matched"
    )


def _cross_database_report_check(
    database_url: str,
    data_dir: Path,
    samples: list[dict[str, Any]],
    expected_hashes: dict[str, dict[str, str]],
) -> dict[str, Any]:
    engine = create_engine(database_url)
    with engine.begin() as connection:
        for sequence in (
            "marketing_materials_id_seq",
            "material_segments_id_seq",
            "screening_runs_id_seq",
            "risk_findings_id_seq",
            "finding_evidence_links_id_seq",
        ):
            connection.execute(text(f"SELECT setval('{sequence}', 100000, false)"))
    settings = Settings(database_url=database_url, data_dir=data_dir)
    service = DeterministicScreeningService(load_ruleset(), settings)
    actual_hashes: dict[str, dict[str, str]] = {}
    with Session(engine, expire_on_commit=False) as session:
        verification = KnowledgeIndexService(settings).verify_chunks(session)
        if not verification.valid or verification.active_chunks != 73:
            raise SystemExit("screening_comparison_trusted_index_invalid")
        for sample in samples:
            run = service.run(
                session,
                title=f"构造评估 {sample['sample_id']}",
                material_type="sales_script",
                raw_text=_sample_text(sample),
                source_label="constructed_screening_eval_v1",
                external_reference=sample["sample_id"],
                is_constructed_evaluation=True,
            )
            actual_hashes[str(sample["sample_id"])] = {
                "institution_report": canonical_report_sha256(
                    service.institution_report(session, run.id)
                ),
                "consumer_notice": canonical_report_sha256(
                    service.consumer_notice(session, run.id)
                ),
            }
    engine.dispose()
    mismatches = sorted(
        sample_id
        for sample_id, expected in expected_hashes.items()
        if actual_hashes.get(sample_id) != expected
    )
    return {
        "passed": not mismatches,
        "sample_count": len(samples),
        "primary_key_sequence_offset": 100000,
        "mismatched_samples": mismatches,
        "canonical_report_hashes": actual_hashes,
    }


def _historical_snapshot_check(
    session: Session,
    ruleset: MarketingRuleSet,
    run_id: int,
) -> bool:
    baseline_service = DeterministicScreeningService(ruleset)
    baseline_institution = baseline_service.institution_report(session, run_id)
    baseline_consumer = baseline_service.consumer_notice(session, run_id)
    changed_payload = ruleset.model_dump(mode="json")
    target_rule_id = baseline_institution["findings"][0]["rule_id"]
    for rule in changed_payload["rules"]:
        if rule["rule_id"] == target_rule_id:
            rule["institution_remediation_template"] = "验收时修改但不得影响历史"
            rule["consumer_notice_template"] = "验收时修改但不得影响历史"
    changed = DeterministicScreeningService(MarketingRuleSet.model_validate(changed_payload))
    changed_pass = (
        changed.institution_report(session, run_id) == baseline_institution
        and changed.consumer_notice(session, run_id) == baseline_consumer
    )
    deleted_payload = ruleset.model_dump(mode="json")
    deleted_payload["rules"] = [
        rule for rule in deleted_payload["rules"] if rule["rule_id"] != target_rule_id
    ]
    deleted = DeterministicScreeningService(MarketingRuleSet.model_validate(deleted_payload))
    return changed_pass and (
        deleted.institution_report(session, run_id) == baseline_institution
        and deleted.consumer_notice(session, run_id) == baseline_consumer
    )


def _trusted_counts(session: Session) -> dict[str, object]:
    return {
        "source_documents": session.query(SourceDocument).count(),
        "knowledge_chunks": session.query(KnowledgeChunk).count(),
        "active_knowledge_chunks": session.query(KnowledgeChunk).filter_by(is_active=True).count(),
    }


def _sensitive_matches(value: str) -> list[str]:
    patterns = {
        "api_key": r"sk-[A-Za-z0-9_-]{16,}",
        "github_token": r"(?:ghp_[A-Za-z0-9]{20,}|github_pat_)",
        "private_key": r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY",
        "authorization": r"Authorization:\s*Bearer",
        "absolute_path": r"(?:/Users/|/home/[^/]+/|[A-Za-z]:\\\\Users\\\\)",
    }
    return sorted(name for name, pattern in patterns.items() if re.search(pattern, value))


def _markdown(report: dict[str, Any]) -> str:
    verification = report["trusted_index_verification"]
    sample_count = report["evaluation_sample_count"]
    rule_count = report["exact_rule_match_count"]
    span_count = report["exact_span_match_count"]
    expected_count = report["expected_finding_count"]
    actual_count = report["actual_finding_count"]
    evidence_counts = "/".join(
        str(report[key])
        for key in (
            "evidence_supported_count",
            "evidence_partially_supported_count",
            "evidence_insufficient_count",
        )
    )
    chunk_counts = json.dumps(
        verification["chunks_by_record_type"], ensure_ascii=False, sort_keys=True
    )
    lines = [
        "# 确定性营销合规筛查正式离线验收",
        "",
        f"- 规则集：`{report['ruleset_version']}`",
        f"- 规则集 SHA-256：`{report['ruleset_sha256']}`",
        f"- 可信索引 payload hash：`{report['trusted_index_payload_hash']}`",
        f"- 评估样本：{sample_count}",
        f"- 规则集合精确匹配：{rule_count}/{sample_count}",
        f"- 原始 span 精确匹配：{span_count}/{sample_count}",
        f"- 预期/实际 findings：{expected_count}/{actual_count}",
        f"- 证据 supported/partial/insufficient：{evidence_counts}",
        f"- 证据语义评估：`{report['evidence_semantic_evaluation_version']}`",
        f"- 证据链接总数/通过：{report['evidence_links_total']}/{report['evidence_links_passed']}",
        f"- 被拒绝的不相关候选：{report['evidence_links_rejected_as_irrelevant']}",
        "- 正式入选/严格语义有效/无关链接："
        f"{report['selected_evidence_links']}/"
        f"{report['selected_evidence_links_semantically_valid']}/"
        f"{report['selected_irrelevant_links']}",
        f"- 禁止法规块误选：{report['forbidden_normative_links_selected']}",
        "- pattern groups 完整："
        f"{'PASS' if report['matched_pattern_groups_validation'] else 'FAIL'}",
        "- 证据状态符合预期："
        f"{report['status_matches_expected_count']}/"
        f"{report['status_expected_finding_count']}",
        f"- 语义匹配处罚案例：{report['enforcement_examples_semantically_matched']}",
        f"- 误选无关处罚案例：{report['unrelated_enforcement_examples_selected']}",
        "- 产品上下文 material matched/illustrative："
        f"{report['product_context_material_matched']}/"
        f"{report['product_context_illustrative']}",
        f"- 历史报告快照：{'PASS' if report['historical_report_snapshot_pass'] else 'FAIL'}",
        f"- 局部上下文对抗：{'PASS' if report['local_context_adversarial_pass'] else 'FAIL'}",
        f"- 强边界与否定语境：{'PASS' if report['strong_boundary_and_negation_pass'] else 'FAIL'}",
        "- 跨数据库报告 SHA："
        f"{'PASS' if report['cross_database_report_stability']['passed'] else 'FAIL'}",
        f"- 超长分段边界：{'PASS' if report['long_segment_boundary_pass'] else 'FAIL'}",
        f"- 组合字符 NFKC：{'PASS' if report['nfkc_combining_cluster_pass'] else 'FAIL'}",
        f"- 可信知识块：{verification['active_chunks']} {chunk_counts}",
        f"- RegulatoryCase 证据：{report['regulatory_case_evidence_count']}",
        f"- 机构报告：{'PASS' if report['institution_report_validation'] else 'FAIL'}",
        f"- 消费者提示：{'PASS' if report['consumer_notice_validation'] else 'FAIL'}",
        f"- 确定性复跑：{'PASS' if report['deterministic_rerun_result'] else 'FAIL'}",
        f"- 可信知识状态保持：{'PASS' if report['trusted_knowledge_unchanged'] else 'FAIL'}",
        "",
        "完整逐样本规则、span、解释和证据快照见 JSON 报告。所有样本均为人工构造，",
        "不是监管事实、处罚案例、法规证据或真实营销材料。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
