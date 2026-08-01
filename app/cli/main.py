import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.models import (
    DataSource,
    EvaluationSample,
    Penalty,
    ProductDocument,
    Regulation,
    RegulatoryCase,
    SourceDocument,
)
from app.models.enums import (
    AuthenticityType,
    DatasetSplit,
    DataType,
    DocumentDataType,
    RegulatoryCaseCategory,
    RegulatoryCaseUsage,
    ReviewStatus,
    SampleCategory,
)
from app.repositories import DocumentRepository
from app.services.collection import (
    FileCollector,
    LocalDirectoryCollector,
    LocalManifestCollector,
    SafeUrlPolicy,
    WebPageCollector,
)
from app.services.knowledge import (
    KnowledgeIndexService,
    SearchRequest,
    TrustedKnowledgeSearchService,
)
from app.services.parsing import ParsingService
from app.services.penalty_entries import (
    build_penalty_identity_material,
    build_penalty_source_entry_fragments,
    penalty_source_entry_fingerprint,
    source_entry_content_sha256,
)
from app.services.pilot.reporting import PilotReportService
from app.services.pilot.service import (
    PilotConfigurationError,
    PilotService,
    collection_outcomes_jsonl,
    write_collection_outcomes,
)
from app.services.review import ReviewService
from app.services.state_machine import StateMachineService
from app.services.structured_records import StructuredRecordService
from app.services.structured_revisions import StructuredDraftRevisionService
from app.services.validation import ValidationService

app = typer.Typer(help="保销智审后端数据与审核工作流 CLI", no_args_is_help=True)
knowledge_app = typer.Typer(help="可验证的可信词法检索管理", no_args_is_help=True)
app.add_typer(knowledge_app, name="knowledge")


@knowledge_app.command("rebuild")
def knowledge_rebuild(
    document_id: int | None = typer.Option(None, min=1, help="仅重建指定文档"),
) -> None:
    """Materialize trusted structured records into immutable lexical chunks."""
    with SessionLocal() as session:
        service = KnowledgeIndexService()
        if document_id is not None:
            summary = service.rebuild_document_chunks(session, document_id)
            payload = asdict(summary)
        else:
            all_summary = service.rebuild_all_trusted_chunks(session)
            payload = asdict(all_summary)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@knowledge_app.command("verify")
def knowledge_verify() -> None:
    """Verify active chunks, trust state, locators and source identities."""
    with SessionLocal() as session:
        report = KnowledgeIndexService().verify_chunks(session)
    typer.echo(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    if not report.valid:
        raise typer.Exit(code=1)


@knowledge_app.command("search")
def knowledge_search(
    query: str = typer.Argument("", help="查询文本；留空时必须使用有界过滤"),
    record_type: list[str] | None = typer.Option(None, "--record-type"),
    pilot_id: list[str] | None = typer.Option(None, "--pilot-id"),
    authority: str | None = typer.Option(None),
    date_from: str | None = typer.Option(None, help="YYYY-MM-DD"),
    date_to: str | None = typer.Option(None, help="YYYY-MM-DD"),
    evidence_quality: list[str] | None = typer.Option(None, "--evidence-quality"),
    limit: int = typer.Option(20, min=1, max=100),
    offset: int = typer.Option(0, min=0, max=10_000),
) -> None:
    """Search active trusted chunks using the shared deterministic search service."""
    request = SearchRequest(
        query=query,
        record_types=tuple(record_type or ()),
        pilot_ids=tuple(pilot_id or ()),
        authority=authority,
        date_from=_parse_cli_date(date_from),
        date_to=_parse_cli_date(date_to),
        evidence_quality=tuple(evidence_quality or ()),
        limit=limit,
        offset=offset,
    )
    with SessionLocal() as session:
        results = TrustedKnowledgeSearchService().search(session, request)
    typer.echo(
        json.dumps(
            [result.as_dict() for result in results],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


@knowledge_app.command("stats")
def knowledge_stats() -> None:
    """Show trusted knowledge materialization statistics."""
    with SessionLocal() as session:
        report = KnowledgeIndexService().verify_chunks(session)
    typer.echo(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


def _parse_cli_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("date must use YYYY-MM-DD") from exc


@app.command("init-db")
def init_db() -> None:
    """Apply all Alembic migrations."""
    command.upgrade(Config("alembic.ini"), "head")
    typer.echo("Database migrations applied")


@app.command("register-source")
def register_source(
    name: str = typer.Option(..., help="来源名称"),
    base_url: str = typer.Option(..., help="来源基础 URL"),
    source_type: DocumentDataType = typer.Option(..., help="文档数据类型"),
    publisher: str | None = typer.Option(None, help="发布机构"),
    rate_limit_seconds: float = typer.Option(1.0, min=0, help="请求间隔秒数"),
) -> None:
    """Register a public data source."""
    with SessionLocal() as session:
        source = DataSource(
            name=name,
            base_url=base_url,
            publisher=publisher,
            source_type=source_type.value,
            rate_limit_seconds=rate_limit_seconds,
        )
        session.add(source)
        session.commit()
        typer.echo(f"Registered source id={source.id}")


@app.command("collect-url")
def collect_url(
    url: str = typer.Option(..., help="公开 HTTP(S) URL"),
    source_type: DocumentDataType = typer.Option(..., help="文档数据类型"),
    source_id: int = typer.Option(..., help="已注册来源 ID"),
) -> None:
    """Collect one web page, PDF, DOCX or text URL."""
    with SessionLocal() as session:
        source = session.get(DataSource, source_id)
        if not source or not source.enabled:
            raise typer.BadParameter("source_id must identify an enabled source")
        if source.source_type != source_type.value:
            raise typer.BadParameter("source_type does not match registered source")
        allowed_domains = source.crawl_policy.get("allowed_domains", [])
        if not isinstance(allowed_domains, list) or any(
            not isinstance(value, str) for value in allowed_domains
        ):
            raise typer.BadParameter("registered source allowed_domains must be a string list")
        allowed_hosts = SafeUrlPolicy.allowed_hosts(source.base_url, allowed_domains)
        collector = WebPageCollector(allowed_hosts=allowed_hosts)
        document, created = collector.persist(
            session,
            collector.collect(url),
            source_type.value,
            source_id=source_id,
            authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
        )
        typer.echo(f"document_id={document.id} created={created} sha256={document.sha256}")


@app.command("collect-directory")
def collect_directory(
    path: Path = typer.Option(..., exists=True, file_okay=False, help="本地目录"),
    source_type: DocumentDataType = typer.Option(..., help="文档数据类型"),
) -> None:
    """Collect supported files recursively; one failure does not stop the batch."""
    collector = LocalDirectoryCollector()
    created = duplicates = failed = 0
    with SessionLocal() as session:
        for result in collector.collect_all(path):
            try:
                _, is_created = collector.persist(
                    session,
                    result,
                    source_type.value,
                    authenticity_type=AuthenticityType.PENDING_VERIFICATION.value,
                )
                created += int(is_created)
                duplicates += int(not is_created)
            except Exception as exc:
                session.rollback()
                failed += 1
                typer.echo(f"collection error: {exc}", err=True)
    typer.echo(f"created={created} duplicates={duplicates} failed={failed}")


@app.command("parse-pending")
def parse_pending() -> None:
    """Parse all collected documents that are still pending."""
    with SessionLocal() as session:
        parsed, requires_ocr, errors = ParsingService().parse_pending(session)
    typer.echo(f"parsed={parsed} requires_ocr={requires_ocr} failed={len(errors)}")
    for error in errors:
        typer.echo(error, err=True)


@app.command("validate-pending")
def validate_pending() -> None:
    """Run deterministic validators; never auto-approve records."""
    with SessionLocal() as session:
        passed, failed = ValidationService().validate_pending(session)
    typer.echo(f"pending_review={passed} auto_validation_failed={failed}")


@app.command("export-review-batch")
def export_review_batch(
    data_type: DataType = typer.Option(..., help="待审核数据类型"),
    format: str = typer.Option("jsonl", help="jsonl 或 xlsx"),
) -> None:
    """Export pending-review records as JSONL or XLSX."""
    with SessionLocal() as session:
        batch = ReviewService().export_batch(session, data_type.value, format)
    typer.echo(f"batch_id={batch.id} records={batch.record_count} path={batch.export_path}")


@app.command("create-review-result-template")
def create_review_result_template(
    batch_id: int = typer.Option(..., min=1, help="审核批次 ID"),
) -> None:
    """Create a hash-bound JSONL decision template from an immutable review batch."""
    with SessionLocal() as session:
        path = ReviewService().create_result_template(session, batch_id)
    typer.echo(f"batch_id={batch_id} path={path}")


@app.command("export-review-bundle")
def export_review_bundle(
    data_type: DocumentDataType = typer.Option(..., help="待审核文档类型"),
) -> None:
    """Export a portable, integrity-pinned review ZIP with source artifacts."""
    with SessionLocal() as session:
        batch, path = ReviewService().export_bundle(session, data_type.value)
    typer.echo(
        f"batch_id={batch.id} records={batch.record_count} path={path} sha256={batch.bundle_sha256}"
    )


@app.command("collect-local-manifest")
def collect_local_manifest(
    file: Path = typer.Option(..., exists=True, dir_okay=False, help="本地官方资料清单"),
) -> None:
    """Import local files with attributed registered public-source occurrences."""
    with SessionLocal() as session:
        imported, errors = LocalManifestCollector().import_jsonl(session, file)
    typer.echo(f"imported={imported} failed={len(errors)}")
    for error in errors:
        typer.echo(error, err=True)


@app.command("pilot-validate-manifests")
def pilot_validate_manifests(
    path: Path = typer.Option(
        Path("pilot/manifests"),
        exists=True,
        help="Pilot JSONL manifest 文件或目录",
    ),
) -> None:
    """Validate controlled Pilot manifests without collecting any data."""
    entries, issues = PilotService().validate_manifests(path)
    for issue in issues:
        typer.echo(
            f"{issue.location}: {issue.code}: {issue.message}",
            err=True,
        )
    typer.echo(f"valid={len(entries)} failed={len(issues)}")
    if issues:
        raise typer.Exit(code=1)


@app.command("pilot-collect")
def pilot_collect(
    manifest: Path = typer.Option(
        ...,
        exists=True,
        dir_okay=False,
        help="已审批的 Pilot JSONL manifest",
    ),
    requested_by: str = typer.Option("cli_operator", help="请求采集的操作者"),
) -> None:
    """Collect only approved Pilot entries through the existing safe collector."""
    try:
        with SessionLocal() as session:
            result = PilotService().collect_manifest(
                session,
                manifest,
                requested_by=requested_by,
            )
    except PilotConfigurationError as exc:
        typer.echo("pilot_configuration_invalid", err=True)
        for issue in exc.issues:
            typer.echo(
                f"{issue.location}: {issue.code}: {issue.message}",
                err=True,
            )
        raise typer.Exit(code=1) from None
    output_path = (
        manifest.resolve().parent.parent / "reports" / f"pilot-collection-run-{result.run_id}.jsonl"
    )
    write_collection_outcomes(output_path, result.outcomes)
    typer.echo(collection_outcomes_jsonl(result.outcomes), nl=False)
    failed = sum(outcome.status == "failed" for outcome in result.outcomes)
    typer.echo(
        f"run_id={result.run_id} "
        f"collected={sum(outcome.status == 'collected' for outcome in result.outcomes)} "
        f"skipped={sum(outcome.status == 'skipped' for outcome in result.outcomes)} "
        f"failed={failed} export={output_path.name}"
    )
    if failed:
        raise typer.Exit(code=1)


@app.command("pilot-status")
def pilot_status(
    run_id: int | None = typer.Option(None, min=1, help="仅统计指定采集 Run"),
    scope: str = typer.Option("cumulative", help="统计范围：cumulative"),
) -> None:
    """Summarize Pilot progress by source type."""
    with SessionLocal() as session:
        status = PilotReportService().status(session, run_id=run_id, scope=scope)
    typer.echo("totals: " + " ".join(f"{key}={value}" for key, value in status["totals"].items()))
    for source_type, counts in status["by_type"].items():
        typer.echo(f"{source_type}: " + " ".join(f"{key}={value}" for key, value in counts.items()))


@app.command("pilot-quality-report")
def pilot_quality_report(
    output: Path = typer.Option(
        Path("pilot/reports/pilot-quality-report.json"),
        dir_okay=False,
        help="JSON 质量报告输出路径",
    ),
    run_id: int | None = typer.Option(None, min=1, help="仅统计指定采集 Run"),
    scope: str = typer.Option("cumulative", help="统计范围：cumulative"),
) -> None:
    """Write portable JSON and Markdown Pilot quality reports."""
    with SessionLocal() as session:
        json_path, markdown_path = PilotReportService().write_quality_report(
            session,
            output,
            run_id=run_id,
            scope=scope,
        )
    typer.echo(f"json={json_path.name} markdown={markdown_path.name}")


@app.command("import-review-results")
def import_review_results(
    file: Path = typer.Option(..., exists=True, dir_okay=False, help="JSONL 审核结果"),
    batch_id: int | None = typer.Option(None, help="审核批次 ID"),
) -> None:
    """Import reviewer decisions with corrections and status history."""
    with SessionLocal() as session:
        imported, errors = ReviewService().import_results(session, file, batch_id)
    typer.echo(f"imported={imported} failed={len(errors)}")
    for error in errors:
        typer.echo(error, err=True)


@app.command("list-records")
def list_records(
    status: str | None = typer.Option(None, help="审核状态过滤"),
    data_type: str | None = typer.Option(None, help="数据类型过滤"),
) -> None:
    """List collected records."""
    with SessionLocal() as session:
        records = DocumentRepository(session).list(status, data_type)
    for record in records:
        typer.echo(
            f"{record.id}\t{record.data_type}\t{record.final_review_status}"
            f"\t{record.source_title or '-'}"
        )


@app.command("list-regulatory-cases")
def list_regulatory_cases(
    case_category: RegulatoryCaseCategory | None = typer.Option(None, help="案例类别过滤"),
    case_usage: RegulatoryCaseUsage | None = typer.Option(None, help="案例用途过滤"),
    status: str | None = typer.Option(None, help="审核状态过滤"),
    authenticity: AuthenticityType | None = typer.Option(None, help="真实性过滤"),
) -> None:
    """List RegulatoryCase records and their trust/index state."""
    with SessionLocal() as session:
        statement = (
            select(RegulatoryCase, SourceDocument)
            .join(SourceDocument, SourceDocument.id == RegulatoryCase.document_id)
            .order_by(RegulatoryCase.id)
        )
        if case_category is not None:
            statement = statement.where(RegulatoryCase.case_category == case_category.value)
        if case_usage is not None:
            statement = statement.where(RegulatoryCase.case_usage == case_usage.value)
        if status is not None:
            statement = statement.where(RegulatoryCase.final_review_status == status)
        if authenticity is not None:
            statement = statement.where(SourceDocument.authenticity_type == authenticity.value)
        service = KnowledgeIndexService()
        rows = list(session.execute(statement))
        for record, document in rows:
            can_index = not service.rejection_reasons(session, document)
            typer.echo(
                f"{record.id}\tdocument_id={document.id}\t{record.case_category}"
                f"\t{record.case_usage}\t{record.final_review_status}"
                f"\t{document.authenticity_type}\tcan_index={str(can_index).lower()}"
                f"\t{record.case_title}"
            )


@app.command("show-regulatory-case")
def show_regulatory_case(
    case_id: int = typer.Option(..., min=1, help="RegulatoryCase记录 ID"),
) -> None:
    """Show one RegulatoryCase including evidence and index eligibility."""
    with SessionLocal() as session:
        record = session.get(RegulatoryCase, case_id)
        if record is None:
            raise typer.BadParameter("case_id does not identify a RegulatoryCase")
        document = session.get(SourceDocument, record.document_id)
        if document is None:
            raise typer.BadParameter("RegulatoryCase document is missing")
        reasons = KnowledgeIndexService().rejection_reasons(session, document)
        typer.echo(
            json.dumps(
                {
                    "id": record.id,
                    "document_id": document.id,
                    "case_title": record.case_title,
                    "publisher": record.publisher,
                    "published_at": record.published_at,
                    "case_category": record.case_category,
                    "scenario_text": record.scenario_text,
                    "marketing_wording_disclosed": record.marketing_wording_disclosed,
                    "marketing_wording": record.marketing_wording,
                    "case_facts": record.case_facts,
                    "regulatory_analysis": record.regulatory_analysis,
                    "consumer_advice": record.consumer_advice,
                    "case_usage": record.case_usage,
                    "field_evidence": record.field_evidence_json,
                    "evidence_quality": record.evidence_quality,
                    "final_review_status": record.final_review_status,
                    "authenticity_type": document.authenticity_type,
                    "can_index": not reasons,
                    "index_rejection_reasons": reasons,
                },
                ensure_ascii=False,
                default=str,
            )
        )


@app.command("index-approved")
def index_approved() -> None:
    """Mark eligible human-approved records as indexed."""
    with SessionLocal() as session:
        summary = KnowledgeIndexService().index_approved(session)
    typer.echo(f"indexed={summary.indexed} rejected={len(summary.rejected)}")
    for document_id, reasons in summary.rejected.items():
        typer.echo(f"document_id={document_id} rejected={','.join(reasons)}")


@app.command("import-structured-drafts")
def import_structured_drafts(
    file: Path = typer.Option(..., exists=True, dir_okay=False, help="JSONL 结构化草稿"),
) -> None:
    """Import validated structured drafts for parsed documents."""
    with SessionLocal() as session:
        imported, errors = StructuredRecordService().import_jsonl(session, file)
    typer.echo(f"imported={imported} failed={len(errors)}")
    for error in errors:
        typer.echo(error, err=True)


@app.command("revise-structured-draft")
def revise_structured_draft(
    file: Path = typer.Option(..., exists=True, dir_okay=False, help="JSONL 草稿修订"),
    reason: str = typer.Option(..., help="修订原因"),
    actor: str = typer.Option("cli_operator", help="操作人"),
) -> None:
    """Atomically revise pre-review fields and their evidence with an audit log."""
    with SessionLocal() as session:
        revised, errors = StructuredDraftRevisionService().import_jsonl(
            session,
            file,
            reason=reason,
            actor=actor,
        )
    typer.echo(f"revised={revised} failed={len(errors)}")
    for error in errors:
        typer.echo(error, err=True)


@app.command("repair-status-consistency")
def repair_status_consistency(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅报告，不修改（默认）"),
    apply_changes: bool = typer.Option(False, "--apply", help="应用结构化状态修复"),
) -> None:
    """Report or repair structured-record status mismatches."""
    if dry_run and apply_changes:
        raise typer.BadParameter("--dry-run and --apply are mutually exclusive")
    with SessionLocal() as session:
        repairs = StateMachineService.repair_consistency(session, apply=apply_changes)
    mode = "apply" if apply_changes else "dry-run"
    typer.echo(f"mode={mode} mismatches={len(repairs)}")
    for repair in repairs:
        typer.echo(
            f"document_id={repair['document_id']} record_type={repair['record_type']} "
            f"record_id={repair['record_id']} {repair['from_status']}->{repair['to_status']}"
        )


@app.command("resubmit-for-review")
def resubmit_for_review(
    record_type: DocumentDataType = typer.Option(..., help="文档类型"),
    record_id: int = typer.Option(..., min=1, help="文档 ID"),
    reason: str = typer.Option(..., help="重新送审原因"),
) -> None:
    """Explicitly resubmit eligible verification/expert-review records."""
    with SessionLocal() as session:
        document = DocumentRepository(session).get(record_id)
        if not document or document.data_type != record_type.value:
            raise typer.BadParameter("record_type and record_id do not identify a document")
        StateMachineService.resubmit_document(session, document, reason)
    typer.echo(f"record_id={record_id} status={ReviewStatus.PARSED.value}")


@app.command("cancel-review-batch")
def cancel_review_batch(
    batch_id: int = typer.Option(..., min=1, help="审核批次 ID"),
    reason: str = typer.Option(..., help="取消原因"),
) -> None:
    """Cancel one open batch and release its undecided reservations."""
    with SessionLocal() as session:
        batch = ReviewService().cancel_batch(session, batch_id, reason)
    typer.echo(f"batch_id={batch.id} status={batch.status}")


@app.command("reparse-document")
def reparse_document(
    document_id: int = typer.Option(..., min=1, help="文档 ID"),
    reason: str = typer.Option(..., help="重新解析原因"),
) -> None:
    """Safely reparse a pre-review document while preserving artifact history."""
    with SessionLocal() as session:
        document = DocumentRepository(session).get(document_id)
        if not document:
            raise typer.BadParameter("document_id does not identify a document")
        ParsingService().reparse_document(session, document, reason)
    typer.echo(f"document_id={document_id} status={ReviewStatus.PARSED.value}")


@app.command("health-check")
def health_check() -> None:
    """Verify database access and disabled-provider-safe startup."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    typer.echo("status=ok llm=optional embedding=optional ocr=optional")


@app.command("seed")
def seed() -> None:
    """Load explicitly labeled demo documents and constructed evaluation samples."""
    samples_dir = get_settings().data_dir.resolve() / "samples"
    document_specs = [
        (
            samples_dir / "demo_regulation.html",
            DataType.REGULATION.value,
            "本演示规则不具有法律效力",
        ),
        (
            samples_dir / "demo_penalty.txt",
            DataType.PENALTY.value,
            "本演示处罚事实不对应任何真实机构或个人",
        ),
        (
            samples_dir / "demo_product.txt",
            DataType.PRODUCT_DOCUMENT.value,
            "本演示产品条款不对应任何真实保险产品",
        ),
    ]
    collector = FileCollector()
    with SessionLocal() as session:
        documents = {}
        for path, data_type, _ in document_specs:
            document, _ = collector.persist(
                session,
                collector.collect(path),
                data_type,
                authenticity_type=AuthenticityType.DEMO_ONLY.value,
            )
            if document.parse_status == "pending":
                ParsingService().parse_document(session, document)
            documents[data_type] = document
        regulation = documents[DataType.REGULATION.value]
        if not session.scalar(select(Regulation).where(Regulation.document_id == regulation.id)):
            session.add(
                Regulation(
                    document_id=regulation.id,
                    title="演示监管规则",
                    article_text="本演示规则不具有法律效力",
                    source_quote="本演示规则不具有法律效力",
                    validity_status="unknown",
                    final_review_status=ReviewStatus.PARSED.value,
                )
            )
        penalty_doc = documents[DataType.PENALTY.value]
        if not session.scalar(select(Penalty).where(Penalty.document_id == penalty_doc.id)):
            punished_entity = "本演示处罚事实不对应任何真实机构或个人"
            illegal_facts = "虚构情景：演示材料信息披露不完整，仅用于验证字段校验。"
            penalty_fields = {
                "punished_entity": punished_entity,
                "illegal_facts": illegal_facts,
            }
            penalty_evidence = {}
            for field_name, quote in penalty_fields.items():
                start = (penalty_doc.raw_text or "").index(quote)
                penalty_evidence[field_name] = [
                    {
                        "quote": quote,
                        "page_number": 1,
                        "start_offset": start,
                        "end_offset": start + len(quote),
                        "mode": "verbatim",
                    }
                ]
            identity_material = build_penalty_identity_material(
                penalty_fields,
                penalty_evidence,
            )
            fragments = build_penalty_source_entry_fragments(
                penalty_fields,
                penalty_evidence,
            )
            content_sha256 = source_entry_content_sha256(identity_material)
            penalty = Penalty(
                document_id=penalty_doc.id,
                source_entry_index=1,
                source_entry_fingerprint=penalty_source_entry_fingerprint(
                    raw_artifact_sha256=penalty_doc.sha256,
                    source_entry_content_sha256=content_sha256,
                ),
                punished_entity=punished_entity,
                illegal_facts=illegal_facts,
                original_sales_wording_disclosed=False,
                original_sales_wording=None,
                source_quote=punished_entity,
                field_evidence_json=penalty_evidence,
                final_review_status=ReviewStatus.PARSED.value,
            )
            session.add(penalty)
            session.flush()
            metadata = dict(penalty_doc.metadata_json)
            metadata["structured_draft_provenance"] = [
                {
                    "structured_record_id": penalty.id,
                    "record_type": DataType.PENALTY.value,
                    "pilot_id": None,
                    "draft_generation_method": None,
                    "draft_generation_version": None,
                    "source_entry_locator": {
                        "table_index": 1,
                        "logical_row": 1,
                    },
                    "source_entry_fragments": fragments,
                    "source_entry_content_sha256": content_sha256,
                }
            ]
            penalty_doc.metadata_json = metadata
        product_doc = documents[DataType.PRODUCT_DOCUMENT.value]
        if not session.scalar(
            select(ProductDocument).where(ProductDocument.document_id == product_doc.id)
        ):
            session.add(
                ProductDocument(
                    document_id=product_doc.id,
                    product_name="演示产品（非真实产品）",
                    waiting_period="演示等待期：三十日",
                    exclusions="演示除外责任",
                    source_quote="本演示产品条款不对应任何真实保险产品",
                    final_review_status=ReviewStatus.PARSED.value,
                )
            )
        session.commit()
        ValidationService().validate_pending(session)
        if not list(session.scalars(select(EvaluationSample))):
            categories = [
                SampleCategory.COMPLIANT,
                SampleCategory.BOUNDARY,
                SampleCategory.RISKY,
                SampleCategory.ADVERSARIAL,
                SampleCategory.MULTI_RISK,
            ]
            for index, category in enumerate(categories, 1):
                session.add(
                    EvaluationSample(
                        sample_text=f"人工构造演示样本 {index}，不代表真实销售记录。",
                        sample_category=category.value,
                        risk_labels=["demo"],
                        expected_evidence={"required": "人工审核"},
                        construction_basis="为验证确定性流程人工构造",
                        authenticity_type=AuthenticityType.CONSTRUCTED_FOR_EVALUATION.value,
                        split=DatasetSplit.TRAIN.value,
                        final_review_status=ReviewStatus.PENDING_REVIEW.value,
                    )
                )
            session.commit()
        count = len(list(session.scalars(select(EvaluationSample))))
    typer.echo(f"demo_documents=3 evaluation_samples={count}")


if __name__ == "__main__":
    app()
