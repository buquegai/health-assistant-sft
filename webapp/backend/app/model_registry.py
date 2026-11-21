from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

import yaml

from .config import BACKEND_DIR, settings


@dataclass(frozen=True)
class ModelConfig:
    id: str
    name: str
    base_url: str
    model_name: str
    api_key: str
    inference_config_path: str


def _configured_value(entry: dict, key: str, env_key: str, fallback: str = "") -> str:
    variable = entry.get(env_key)
    value = os.getenv(variable, "") if isinstance(variable, str) and variable else ""
    raw = value or entry.get(key) or fallback
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"Model registry field {key} is missing")
    return raw.strip()


def _load_registry() -> tuple[dict[str, ModelConfig], str]:
    if not settings.model_registry_path.is_file():
        raise RuntimeError("model registry is unavailable")
    with settings.model_registry_path.open("r", encoding="utf-8") as source:
        registry = yaml.safe_load(source) or {}
    entries = registry.get("models")
    default_id = registry.get("default_model_id")
    if not isinstance(entries, list) or not entries or not isinstance(default_id, str):
        raise RuntimeError("model registry must define models and default_model_id")

    models: dict[str, ModelConfig] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("model registry entries must be mappings")
        model_id = entry.get("id")
        name = entry.get("name")
        inference_config = entry.get("inference_config")
        if not isinstance(model_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", model_id):
            raise RuntimeError("model registry contains an invalid id")
        if model_id in models or not isinstance(name, str) or not name.strip():
            raise RuntimeError("model registry contains a duplicate id or missing name")
        if not isinstance(inference_config, str) or not inference_config.strip():
            raise RuntimeError(f"inference_config is missing for model {model_id}")

        base_fallback = settings.model_base_url if model_id == default_id else ""
        name_fallback = settings.model_name if model_id == default_id else ""
        base_url = _configured_value(entry, "base_url", "base_url_env", base_fallback).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(f"invalid model service URL for {model_id}")
        model_name = _configured_value(entry, "model_name", "model_name_env", name_fallback)
        key_name = entry.get("api_key_env")
        api_key = os.getenv(key_name, "") if isinstance(key_name, str) and key_name else ""
        inference_path = (BACKEND_DIR / inference_config).resolve() if not os.path.isabs(inference_config) else inference_config
        models[model_id] = ModelConfig(model_id, name.strip(), base_url, model_name, api_key, str(inference_path))

    if default_id not in models:
        raise RuntimeError("default_model_id does not exist in the model registry")
    return models, default_id


MODELS, DEFAULT_MODEL_ID = _load_registry()


def get_model(model_id: str) -> ModelConfig:
    try:
        return MODELS[model_id]
    except KeyError:
        raise KeyError(model_id) from None


def public_models() -> dict:
    return {
        "items": [{"id": model.id, "name": model.name} for model in MODELS.values()],
        "default_model_id": DEFAULT_MODEL_ID,
    }


@lru_cache(maxsize=32)
def inference_config(model_id: str) -> dict:
    model = get_model(model_id)
    with open(model.inference_config_path, "r", encoding="utf-8") as source:
        config = yaml.safe_load(source) or {}
    system_prompt = config.get("default_system")
    tokenizer_path = config.get("model_name_or_path")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise RuntimeError(f"default_system is missing for model {model_id}")
    if not isinstance(tokenizer_path, str) or not tokenizer_path.strip():
        raise RuntimeError(f"tokenizer path is missing for model {model_id}")
    return {"system_prompt": system_prompt.strip(), "tokenizer_path": tokenizer_path.strip()}
