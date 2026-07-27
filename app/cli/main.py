from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.core.database import SessionLocal, engine
from app.models import DataSource
from app.models.enums import AuthenticityType, DataType
from app.repositories import DocumentRepository
from app.services.collection import LocalDirectoryCollector, WebPageCollector
from app.services.knowledge import KnowledgeIndexService
from app.services.parsing import ParsingService
from app.services.review import ReviewService
from app.services.validation import ValidationService

app = typer.Typer(help="保销智审后端数据与审核工作流 CLI", no_args_is_help=True)


@app.command("init-db")
def init_db() -> None:
    """Apply all Alembic migrations."""
    command.upgrade(Config("alembic.ini"), "head")
    typer.echo("Database migrations applied")


@app.command("register-source")
def register_source(
    name: str = typer.Option(..., help="来源名称"),
    base_url: str = typer.Option(..., help="来源基础 URL"),
    source_type: DataType = typer.Option(..., help="数据类型"),
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
    source_type: DataType = typer.Option(..., help="数据类型"),
    source_id: int | None = typer.Option(None, help="已注册来源 ID"),
    authenticity_type: AuthenticityType = typer.Option(
        AuthenticityType.PENDING_VERIFICATION, help="真实性标识"
    ),
) -> None:
    """Collect one web page, PDF, DOCX or text URL."""
    collector = WebPageCollector()
    with SessionLocal() as session:
        document, created = collector.persist(
            session,
            collector.collect(url),
            source_type.value,
            source_id=source_id,
            authenticity_type=authenticity_type.value,
        )
        typer.echo(f"document_id={document.id} created={created} sha256={document.sha256}")


@app.command("collect-directory")
def collect_directory(
    path: Path = typer.Option(..., exists=True, file_okay=False, help="本地目录"),
    source_type: DataType = typer.Option(..., help="数据类型"),
    authenticity_type: AuthenticityType = typer.Option(
        AuthenticityType.PENDING_VERIFICATION, help="真实性标识"
    ),
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
                    authenticity_type=authenticity_type.value,
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
        parsed, errors = ParsingService().parse_pending(session)
    typer.echo(f"parsed={parsed} failed={len(errors)}")
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


@app.command("index-approved")
def index_approved() -> None:
    """Mark eligible human-approved records as indexed."""
    with SessionLocal() as session:
        count = KnowledgeIndexService().index_approved(session)
    typer.echo(f"indexed={count}")


@app.command("health-check")
def health_check() -> None:
    """Verify database access and disabled-provider-safe startup."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    typer.echo("status=ok llm=optional embedding=optional ocr=optional")


@app.command("seed")
def seed() -> None:
    """Print seed guidance; demo files are collected with collect-directory."""
    with SessionLocal() as session:
        count = len(list(session.scalars(select(DataSource))))
    typer.echo(f"registered_sources={count}; demo files are in data/samples")


if __name__ == "__main__":
    app()
