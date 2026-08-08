"""Minimal synchronous endpoints for the file-backed Evaluation Center."""
# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.config import get_settings
from app.services.evaluation import (
    EvaluationRunner,
    ExposureAuditor,
    ManifestError,
    ReportStore,
    load_manifest,
)
from app.services.evaluation.core import canonical_sha256

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])
_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACTS = _ROOT / "data" / "evaluations"


def _manifest(track: str, *, sealed: bool = False) -> dict[str, Any]:
    return load_manifest(_ARTIFACTS / f"{track}_manifest_v1.json", require_sealed=sealed)


def _store() -> ReportStore:
    return ReportStore(get_settings().data_dir / "evaluation_reports")


def _safety_state() -> dict[str, Any]:
    fixture = json.loads(
        (_ROOT / "tests" / "fixtures" / "controlled_rag_eval_v1" / "responses.json").read_text(
            encoding="utf-8"
        )
    )
    samples = fixture["samples"]
    report = _store().read("controlled_rag_acceptance")
    return {
        "status": "COMPLETED" if report and report.get("status") == "COMPLETED" else "NOT_READY",
        "corpus_schema_version": fixture["schema_version"],
        "valid_samples": sum(row["expected"] == "passed" for row in samples),
        "invalid_samples": sum(row["expected"] != "passed" for row in samples),
        "report": report,
        "notice": "该指标验证受控 RAG 的 validator/provider 门禁能力，不代表真实模型输出的总体安全率。",
    }


def _latest_track(track: str) -> dict[str, Any]:
    manifest = _manifest(track)
    report = _store().read(track, manifest=manifest if manifest.get("status") == "SEALED" else None)
    provisional_path = (
        _ARTIFACTS
        / "provisional"
        / f"m6{'b_constructed_provisional_v1' if track == 'constructed' else 'c_external_provisional_v2'}.json"
    )
    provisional: dict[str, Any] | None = None
    if provisional_path.exists():
        try:
            candidate = json.loads(provisional_path.read_text(encoding="utf-8"))
            digest = candidate.pop("report_sha256", None)
            if candidate.get("provisional") is True and digest == canonical_sha256(candidate):
                candidate["report_sha256"] = digest
                provisional = candidate
        except (OSError, json.JSONDecodeError):
            provisional = None
    return {
        "manifest": {
            "version": manifest["version"],
            "status": manifest["status"],
            "content_sha256": manifest.get("content_sha256"),
            "case_count": len(manifest["cases"]),
        },
        "status": report.get("status") if report else "NOT_READY",
        "report": report,
        "provisional": provisional,
    }


@router.get("/latest")
def latest_evaluations() -> dict[str, Any]:
    return {
        "constructed": _latest_track("constructed"),
        "external": _latest_track("external"),
        "controlled_rag_safety": _safety_state(),
    }


def _run(track: str, session: Session) -> dict[str, Any]:
    try:
        manifest = _manifest(track, sealed=True)
    except ManifestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runner = EvaluationRunner()
    if not runner.acquire_single_flight():
        raise HTTPException(status_code=409, detail="evaluation_single_flight_active")
    try:
        if track == "constructed":
            report = runner.run_constructed(session, manifest)
        else:
            inventory = json.loads(
                (_ARTIFACTS / "exposure_inventory_v1.json").read_text(encoding="utf-8")
            )
            # Candidate and sealed manifests live under this project. Scanning their own
            # URLs would turn every evaluated row into a false "exposed" match; the
            # versioned exposure inventory remains the authoritative exact audit source.
            report = runner.run_external(session, manifest, ExposureAuditor(inventory))
        path = _store().write(track, report)
        return {"status": report["status"], "report_path": path.name, "report": report}
    finally:
        runner.release_single_flight()


@router.post("/constructed/run")
def run_constructed_evaluation(session: Session = Depends(get_db)) -> dict[str, Any]:
    return _run("constructed", session)


@router.post("/external/run")
def run_external_evaluation(session: Session = Depends(get_db)) -> dict[str, Any]:
    return _run("external", session)
