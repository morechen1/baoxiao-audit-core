"""Validated JSON/HTML export from completed V1-backed platform reports."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ExplanationRun
from app.services.platform.reports import PlatformReportService
from app.services.platform.store import PlatformScreeningResult

REPORT_VERSION = "v1_core_platform_report_1.0"
SAFETY_STATEMENT = (
    "AI语义解析用于发现风险候选，最终 RiskFinding 经系统确定性机制校验形成；"
    "本报告不构成违法认定或最终法律意见。"
)
RISK_LEVEL_LABELS_ZH = {"high": "高风险", "medium": "中风险", "low": "低风险"}


def risk_level_label_zh(value: str) -> str:
    return RISK_LEVEL_LABELS_ZH.get(value, "待复核")


class PlatformAuditExportService:
    def __init__(self) -> None:
        self.reports = PlatformReportService()

    def build(self, session: Session, result: PlatformScreeningResult) -> dict[str, Any]:
        detail = self.reports.detail(session, result)
        institution = self.reports.institution(session, result)
        consumer = self.reports.consumer(session, result)
        summary = self.reports.summary(session, result)
        artifacts = self._artifacts(
            session,
            [chunk.screening_run_id for chunk in result.chunks],
        )
        return {
            "report_version": REPORT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "safety_statement": SAFETY_STATEMENT,
            "material": {
                "title": result.material.title,
                "material_type": result.material.material_type,
                "source_filename": result.material.source_filename,
                "source_format": result.material.source_format,
                "character_count": len(result.material.raw_text),
                "raw_text": result.material.raw_text,
            },
            "screening": {
                "platform_result_id": result.result_id,
                "screening_run_ids": detail["screening_run_ids"],
                "status": detail["status"],
                "finding_count": detail["finding_count"],
                "risk_level": summary["risk_level"],
                "risk_level_label_zh": risk_level_label_zh(str(summary["risk_level"])),
                "runtime": detail["runtime"],
            },
            "findings": institution["findings"],
            "evidence_summary": institution["evidence_summary"],
            "institution": {
                "base_report": institution,
                "artifacts": artifacts["institution"],
            },
            "consumer": {
                "base_report": consumer,
                "artifacts": artifacts["consumer"],
            },
            "validation": {
                "institution": [item["validation_status"] for item in artifacts["institution"]],
                "consumer": [item["validation_status"] for item in artifacts["consumer"]],
            },
        }

    @staticmethod
    def _artifacts(
        session: Session, screening_run_ids: list[int]
    ) -> dict[str, list[dict[str, Any]]]:
        values = session.scalars(
            select(ExplanationRun)
            .options(selectinload(ExplanationRun.artifact))
            .where(ExplanationRun.screening_run_id.in_(screening_run_ids))
            .order_by(ExplanationRun.id)
        ).all()
        latest: dict[tuple[int, str], ExplanationRun] = {}
        for run in values:
            latest[(run.screening_run_id, run.audience)] = run
        output: dict[str, list[dict[str, Any]]] = {"institution": [], "consumer": []}
        for (_, audience), run in sorted(latest.items()):
            artifact = run.artifact
            output[audience].append(
                {
                    "screening_run_id": run.screening_run_id,
                    "explanation_run_id": run.id,
                    "status": run.status,
                    "validation_status": run.validation_status,
                    "provider_name": run.provider_name,
                    "provider_model": run.provider_model,
                    "validated_output": (
                        artifact.validated_output_json if artifact is not None else None
                    ),
                    "resolved_citations": (
                        artifact.resolved_citations_json if artifact is not None else []
                    ),
                    "disclaimer": artifact.disclaimer if artifact is not None else None,
                }
            )
        return output

    def html(self, report: dict[str, Any]) -> str:
        material = cast(dict[str, Any], report["material"])
        screening = cast(dict[str, Any], report["screening"])
        finding_sections = []
        for finding in cast(list[dict[str, Any]], report["findings"]):
            evidence_rows = []
            for item in cast(list[dict[str, Any]], finding.get("evidence", [])):
                source = cast(dict[str, Any], item.get("source", {}))
                references = cast(list[dict[str, Any]], item.get("evidence_references", []))
                quote = references[0].get("quote", "") if references else ""
                source_title = html.escape(str(source.get("title") or "可信监管来源"))
                evidence_rows.append(
                    f"<li><strong>{source_title}</strong>"
                    f"<blockquote>{html.escape(str(quote))}</blockquote></li>"
                )
            finding_sections.append(
                "<article><h3>{key} · {rule}</h3><p><b>风险等级：</b>{severity}</p>"
                "<p><b>原文：</b>{quote}（{start}–{end}）</p><p>{explanation}</p>"
                "<h4>可信监管证据（EvidenceLink）</h4><ul>{evidence}</ul></article>".format(
                    key=html.escape(str(finding["finding_key"])),
                    rule=html.escape(str(finding["rule_id"])),
                    severity=html.escape(risk_level_label_zh(str(finding["severity"]))),
                    quote=html.escape(str(finding["matched_text"])),
                    start=finding["raw_start_offset"],
                    end=finding["raw_end_offset"],
                    explanation=html.escape(str(finding["explanation"])),
                    evidence="".join(evidence_rows) or "<li>未绑定充分证据。</li>",
                )
            )
        css = """
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#17231d;
max-width:980px;margin:40px auto;padding:0 28px;line-height:1.65}h1,h2,h3{color:#173f2d}
article,.meta,.runtime{border:1px solid #dfe9e2;border-radius:14px;padding:18px;margin:16px 0}
blockquote,.raw{background:#f4faf6;border-left:4px solid #39a66a;padding:10px 14px;
white-space:pre-wrap}
.meta{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.note{color:#52635a}
"""
        runtime = cast(dict[str, Any], screening["runtime"])
        risk_label = risk_level_label_zh(str(screening["risk_level"]))
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>保销智审核报告</title><style>{css}</style></head><body><h1>保销智审核报告</h1>
<p class="note">{html.escape(str(report['safety_statement']))}</p><div class="meta">
<span><b>材料：</b>{html.escape(str(material['title']))}</span>
<span><b>格式：</b>{html.escape(str(material['source_format']))}</span>
<span><b>风险：</b>{html.escape(risk_label)}</span>
<span><b>Findings：</b>{screening['finding_count']}</span></div>
<h2>原始材料</h2><div class="raw">{html.escape(str(material['raw_text']))}</div>
<h2>风险详情与可信证据</h2>{''.join(finding_sections) or '<p>未形成有效风险 Finding。</p>'}
<h2>机构合规解释</h2><p>独立 Artifact 数：{len(report['institution']['artifacts'])}</p>
<h2>消费者权益解释</h2><p>独立 Artifact 数：{len(report['consumer']['artifacts'])}</p>
<h2>运行信息</h2><div class="runtime">检测基线：V1；文档分块：{runtime['document_chunks']}；
Parser calls：{runtime['parser_calls']}；Cache hits：{runtime['cache_hits']}；
耗时：{runtime['latency_ms']} ms。</div></body></html>"""
