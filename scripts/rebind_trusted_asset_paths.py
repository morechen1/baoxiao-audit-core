#!/usr/bin/env python3
"""Safely rebind the frozen trusted corpus after a release directory moves."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Protocol

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.models import KnowledgeChunk, SourceDocument
from app.services.knowledge.service import KnowledgeIndexService
from app.services.parsed_artifacts import ParsedArtifactIntegrityService


class AssetIdentity(Protocol):
    sha256: str
    parsed_artifact_sha256: str | None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_asset_paths(document: AssetIdentity, data_dir: Path) -> tuple[Path, Path]:
    raw_candidates = sorted((data_dir / "raw").glob(f"{document.sha256}.*"))
    if len(raw_candidates) != 1 or file_sha256(raw_candidates[0]) != document.sha256:
        raise ValueError("trusted raw asset identity mismatch")

    parsed_sha = document.parsed_artifact_sha256
    if not parsed_sha:
        raise ValueError("trusted parsed asset identity missing")
    parsed_path = data_dir / "parsed_artifacts" / f"{document.sha256}-{parsed_sha}.json"
    if not parsed_path.is_file() or file_sha256(parsed_path) != parsed_sha:
        raise ValueError("trusted parsed asset identity mismatch")
    return raw_candidates[0].resolve(), parsed_path.resolve()


def rebind(settings: Settings) -> dict[str, int | str]:
    database = make_url(settings.database_url)
    if database.get_backend_name() != "postgresql" or database.database != "baoxiao_contest_final":
        raise ValueError("requires baoxiao_contest_final PostgreSQL database")

    data_dir = settings.data_dir.resolve()
    identity_map = data_dir / "m7_legacy_current_identity_map.json"
    if not identity_map.is_file():
        raise ValueError("trusted identity map missing")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        documents = list(session.scalars(select(SourceDocument).order_by(SourceDocument.id)))
        chunk_count = int(session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0)
        if len(documents) != 15 or chunk_count != 73:
            raise ValueError("trusted corpus count mismatch; refusing path repair")

        resolved = [(document, *resolve_asset_paths(document, data_dir)) for document in documents]
        changed = 0
        for document, raw_path, parsed_path in resolved:
            if Path(document.raw_file_path) != raw_path:
                document.raw_file_path = str(raw_path)
                changed += 1
            if Path(document.parsed_artifact_path or "") != parsed_path:
                document.parsed_artifact_path = str(parsed_path)
                changed += 1

        session.flush()
        validator = ParsedArtifactIntegrityService(settings)
        for document in documents:
            validator.verify(document, session=session)
        report = KnowledgeIndexService(settings).verify_chunks(session)
        if not report.valid or report.active_chunks != 73:
            raise ValueError("trusted knowledge verification failed; refusing path repair")
        session.commit()
        return {"status": "verified", "documents": 15, "chunks": 73, "paths_updated": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    result = rebind(Settings(database_url=args.database_url, data_dir=args.data_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
