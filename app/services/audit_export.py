"""Validated JSON/HTML audit report export without prompts, credentials, or hidden reasoning."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import ScreeningError
from app.models import ExplanationRun, ScreeningRun
from app.services.screening.reports import ScreeningReportService

AUDIT_REPORT_VERSION = "v2_audit_report_1.0"
AUDIT_SAFETY_STATEMENT = (
    "AI语义解析用于生成风险候选，最终 Finding 经系统确定性机制校验形成；"
    "本报告不构成违法认定或最终法律意见。"
)
TAXONOMY_LABELS = {
    "absolute_or_superlative_claim": "绝对化或最高级表述",
    "concealment_or_minimization_of_exclusions": "免责事项隐瞒或弱化",
    "extra_contractual_benefit": "合同外利益承诺",
    "false_promotion_or_prize": "虚假促销或奖品宣传",
    "guaranteed_return_or_principal": "收益或本金保证",
    "improper_comparison_or_ranking": "不当比较或排名",
    "misleading_interest_or_yield": "利率或收益误导",
    "no_risk_or_no_loss": "零风险或无损失",
    "product_nature_confusion": "产品性质混淆",
    "regulatory_endorsement": "监管背书误导",
    "surrender_or_cash_value_misstatement": "退保或现金价值误述",
    "waiting_or_cooling_period_misstatement": "等待期或犹豫期误述",
}


class AuditReportExportService:
    def build(self, session: Session, run_id: int) -> dict[str, Any]:
        run = session.scalar(
            select(ScreeningRun)
            .options(
                selectinload(ScreeningRun.material),
                selectinload(ScreeningRun.explanation_runs).selectinload(ExplanationRun.artifact),
            )
            .where(ScreeningRun.id == run_id)
        )
        if run is None or run.status != "completed":
            raise ScreeningError("screening_run_not_found")
        reports = ScreeningReportService()
        detail = reports.run_detail(session, run_id)
        institution = reports.institution_report(session, run_id)
        consumer = reports.consumer_notice(session, run_id)
        audience = {
            name: self._latest_audience(run.explanation_runs, name)
            for name in ("institution", "consumer")
        }
        counts = institution["summary"]
        risk_rating = (
            "high" if counts["high_count"] else "medium" if counts["medium_count"] else "low"
        )
        findings = []
        for row in institution["findings"]:
            enriched = dict(row)
            enriched["taxonomy_label"] = TAXONOMY_LABELS.get(row["rule_id"], row["category"])
            findings.append(enriched)
        evaluation = detail["evidence_evaluation_summary"]
        semantic = evaluation.get("semantic_parser", {})
        return {
            "report_version": AUDIT_REPORT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "safety_statement": AUDIT_SAFETY_STATEMENT,
            "material": {
                "title": run.material.title,
                "type": run.material.material_type,
                "source_label": run.material.source_label,
                "raw_text": run.material.raw_text,
                "input_sha256": run.material.input_sha256,
            },
            "screening": {
                "screening_run_id": run.id,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                "risk_rating": risk_rating,
                "finding_count": run.finding_count,
                "ruleset_version": run.ruleset_version,
                "ruleset_sha256": run.ruleset_sha256,
                "semantic_parser": semantic,
                "system_versions": {
                    "audit_report": AUDIT_REPORT_VERSION,
                    "semantic_parser": semantic.get("version"),
                    "semantic_schema": semantic.get("schema_version"),
                    "parser_model": semantic.get("provider_model"),
                },
                "runtime_trace": {
                    "material_parsed": True,
                    "rule_candidates": evaluation.get("deterministic_candidates", 0),
                    "semantic_candidates": semantic.get("model_candidates", 0),
                    "semantic_rejected": semantic.get("rejected_candidates", 0),
                    "final_findings": run.finding_count,
                    "evidence_links": institution["evidence_summary"]["link_count"],
                    "knowledge_retrievals": evaluation.get("knowledge_retrievals", 0),
                    "institution_artifact": audience["institution"]["status"],
                    "consumer_artifact": audience["consumer"]["status"],
                    "citation_validation": {
                        "institution": audience["institution"]["validation_status"],
                        "consumer": audience["consumer"]["validation_status"],
                    },
                },
            },
            "findings": findings,
            "evidence_summary": institution["evidence_summary"],
            "institution": audience["institution"],
            "consumer": audience["consumer"],
            "consumer_notice": consumer,
        }

    @staticmethod
    def _latest_audience(runs: list[ExplanationRun], audience: str) -> dict[str, Any]:
        matching = sorted(
            (run for run in runs if run.audience == audience),
            key=lambda run: run.id,
            reverse=True,
        )
        if not matching:
            return {"status": "not_generated", "validation_status": "pending", "artifact": None}
        run = matching[0]
        artifact = run.artifact
        return {
            "explanation_run_id": run.id,
            "status": run.status,
            "validation_status": run.validation_status,
            "provider_name": run.provider_name,
            "provider_model": run.provider_model,
            "artifact": (
                {
                    "validated_output": artifact.validated_output_json,
                    "resolved_citations": artifact.resolved_citations_json,
                    "disclaimer": artifact.disclaimer,
                }
                if artifact is not None
                else None
            ),
        }

    @staticmethod
    def _validated_texts(audience: dict[str, Any]) -> list[str]:
        artifact = audience.get("artifact")
        output = artifact.get("validated_output") if isinstance(artifact, dict) else None
        values: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                text = value.get("text")
                if isinstance(text, str) and text.strip() and text not in values:
                    values.append(text)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(output)
        return values

    def html(self, report: dict[str, Any]) -> str:
        material = report["material"]
        screening = report["screening"]
        finding_rows = []
        for finding in report["findings"]:
            evidence = "".join(
                "<li><strong>{}</strong><blockquote>{}</blockquote></li>".format(
                    html.escape(str(item["source"].get("title") or "可信监管来源")),
                    html.escape(
                        str((item.get("evidence_references") or [{}])[0].get("quote") or "")
                    ),
                )
                for item in finding.get("evidence", [])
            )
            finding_rows.append(
                "<article><h3>{} · {}</h3><p><b>风险分类：</b>{}</p><p><b>风险等级：</b>{}</p>"
                "<p><b>原文：</b>{}（{}–{}）</p><p>{}</p><p><b>整改建议：</b>{}</p>"
                "<h4>EvidenceLinks</h4><ul>{}</ul></article>".format(
                    html.escape(str(finding["finding_key"])),
                    html.escape(str(finding["rule_id"])),
                    html.escape(
                        str(finding.get("taxonomy_label") or finding.get("category") or "")
                    ),
                    html.escape(str(finding["severity"])),
                    html.escape(str(finding["matched_text"])),
                    finding["raw_start_offset"],
                    finding["raw_end_offset"],
                    html.escape(str(finding["explanation"])),
                    html.escape(str(finding.get("remediation_template") or "请由合规人员复核。")),
                    evidence,
                )
            )
        css = """
body { font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;
  color:#17231d;max-width:980px;margin:40px auto;padding:0 28px;line-height:1.65 }
h1,h2,h3 { color:#173f2d }
article { border:1px solid #dfe9e2;border-radius:14px;padding:18px;margin:16px 0 }
blockquote { background:#f4faf6;border-left:4px solid #39a66a;margin:8px 0;
  padding:8px 12px;white-space:pre-wrap }
.meta { display:grid;grid-template-columns:repeat(2,1fr);gap:8px;background:#f4faf6;
  padding:16px;border-radius:14px }
.raw { white-space:pre-wrap;background:#fafcfb;padding:16px;border-radius:14px }
"""
        safety = html.escape(str(report["safety_statement"]))
        title = html.escape(str(material["title"]))
        kind = html.escape(str(material["type"]))
        risk = html.escape(str(screening["risk_rating"]))
        raw = html.escape(str(material["raw_text"]))
        findings = "".join(finding_rows) or "<p>未形成有效风险 Finding。</p>"
        institution = html.escape(str(report["institution"]["status"]))
        institution_validation = html.escape(str(report["institution"]["validation_status"]))
        consumer_status = html.escape(str(report["consumer"]["status"]))
        consumer_validation = html.escape(str(report["consumer"]["validation_status"]))
        institution_text = (
            "".join(
                f"<li>{html.escape(value)}</li>"
                for value in self._validated_texts(report["institution"])
            )
            or "<li>未生成有效机构端解释。</li>"
        )
        consumer_text = (
            "".join(
                f"<li>{html.escape(value)}</li>"
                for value in self._validated_texts(report["consumer"])
            )
            or "<li>未生成有效消费者端解释。</li>"
        )
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>保销智审核查报告</title><style>{css}</style></head><body>
<h1>保销智审 V2 审查报告</h1><p>{safety}</p>
<div class="meta"><span><b>材料：</b>{title}</span><span><b>类型：</b>{kind}</span>
<span><b>风险等级：</b>{risk}</span><span><b>Findings：</b>{screening["finding_count"]}</span>
</div><h2>原始材料</h2><div class="raw">{raw}</div>
<h2>风险发现与可信证据</h2>{findings}<h2>验证状态</h2>
<p>机构端：{institution} / {institution_validation}</p>
<p>消费者端：{consumer_status} / {consumer_validation}</p>
<h2>机构合规解释</h2><ul>{institution_text}</ul>
<h2>消费者权益解释</h2><ul>{consumer_text}</ul></body></html>"""
