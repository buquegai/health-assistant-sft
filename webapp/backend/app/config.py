from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parents[1]
load_dotenv(BACKEND_DIR / ".env")


def _path_value(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(BACKEND_DIR / 'data' / 'app.db').as_posix()}")
    model_base_url: str = os.getenv("MODEL_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")
    model_name: str = os.getenv("MODEL_NAME", "health-assistant")
    model_api_key: str = os.getenv("MODEL_API_KEY", "")
    model_registry_path: Path = _path_value(os.getenv("MODEL_REGISTRY_PATH", "model_registry.yaml"))
    inference_config_path: Path = _path_value(os.getenv("INFERENCE_CONFIG_PATH", "../../configs/psychology_infer.yaml"))
    tokenizer_path: str = os.getenv("TOKENIZER_PATH", "")
    app_origin: str = os.getenv("APP_ORIGIN", "http://localhost:5173").rstrip("/")
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "7"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    cookie_name: str = "health_session"
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_DAYS", "7")) * 86400
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8080"))


settings = Settings()


def load_system_prompt() -> str:
    import yaml

    if not settings.inference_config_path.is_file():
        raise RuntimeError("inference configuration is unavailable")
    with settings.inference_config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    prompt = config.get("default_system")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("default_system is missing")
    return prompt.strip()


def tokenizer_location() -> str:
    if settings.tokenizer_path.strip():
        return settings.tokenizer_path.strip()
    import yaml

    if not settings.inference_config_path.is_file():
        raise RuntimeError("inference configuration is unavailable")
    with settings.inference_config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    model_path = config.get("model_name_or_path")
    if not isinstance(model_path, str) or not model_path.strip():
        raise RuntimeError("model tokenizer path is missing")
    return model_path.strip()
