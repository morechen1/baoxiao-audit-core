from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from app.core.exceptions import ExplanationError
from app.services.explanation.schemas import PromptDefinition, PromptRegistry

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "controlled_rag_prompts_v1.yaml"


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_prompt_registry(path: Path = PROMPT_PATH) -> PromptRegistry:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        registry = PromptRegistry.model_validate(raw)
    except Exception as exc:
        raise ExplanationError("explanation_prompt_registry_invalid") from exc
    audiences = [prompt.audience for prompt in registry.prompts]
    versions = [prompt.prompt_version for prompt in registry.prompts]
    if len(audiences) != len(set(audiences)) or len(versions) != len(set(versions)):
        raise ExplanationError("explanation_prompt_registry_invalid")
    return registry


def load_prompt(audience: str, path: Path = PROMPT_PATH) -> PromptDefinition:
    for prompt in load_prompt_registry(path).prompts:
        if prompt.audience == audience:
            return prompt
    raise ExplanationError("explanation_audience_invalid")


def prompt_sha256(prompt: PromptDefinition) -> str:
    return canonical_sha256(prompt.model_dump(mode="json"))
