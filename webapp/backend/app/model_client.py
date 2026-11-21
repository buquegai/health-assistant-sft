from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress

import httpx

from .model_registry import ModelConfig


class ModelFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


def headers(model: ModelConfig, generation_id: str | None = None) -> dict[str, str]:
    result = {"Accept": "text/event-stream"}
    if model.api_key:
        result["Authorization"] = f"Bearer {model.api_key}"
    if generation_id:
        result["X-Generation-ID"] = generation_id
    return result


async def verify_model_runtime(model: ModelConfig) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            models = await client.get(f"{model.base_url}/models", headers=headers(model))
            models.raise_for_status()
            if model.model_name not in {item.get("id") for item in models.json().get("data", [])}:
                raise ValueError("unknown model")
            health = await client.get(f"{model.base_url}/health/generation", headers=headers(model))
            health.raise_for_status()
            if health.json().get("cancellation") != "thread-join-v1":
                raise ValueError("cancellation adapter missing")
            if health.json().get("active"):
                raise ModelFailure("MODEL_BUSY", "模型正在处理其他请求，请稍后重试。")
    except ModelFailure:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise ModelFailure("MODEL_UNAVAILABLE", "模型服务暂时不可用，请稍后重试。") from None


async def confirm_generation_exit(generation_id: str, model: ModelConfig) -> bool:
    """False keeps the app permit occupied until a later successful confirmation."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=180, write=10, pool=10)) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                stopped = await client.post(f"{model.base_url}/generations/{generation_id}/cancel", headers=headers(model))
                stopped.raise_for_status()
                result = stopped.json()
                status = await client.get(f"{model.base_url}/health/generation", headers=headers(model))
                status.raise_for_status()
                state = status.json()
                if result.get("found") and result.get("running") is False and state.get("active") is False:
                    return state.get("cancellation") == "thread-join-v1"
                await asyncio.sleep(0.1)
            return False
    except (httpx.HTTPError, ValueError):
        return False


async def stream_completion(
    messages: list[dict[str, str]],
    cancel_event: asyncio.Event,
    generation_id: str,
    model: ModelConfig,
) -> AsyncIterator[dict]:
    payload = {"model": model.model_name, "messages": messages, "stream": True,
               "temperature": 0.7, "top_p": 0.9, "max_tokens": 512}
    started = time.monotonic()
    last_text = started
    received_text = False
    finish_reason = None
    line_task = None
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10)) as client:
            async with client.stream("POST", f"{model.base_url}/chat/completions", headers=headers(model, generation_id), json=payload) as response:
                if response.status_code == 429:
                    raise ModelFailure("MODEL_BUSY", "模型正在处理其他请求，请稍后重试。")
                response.raise_for_status()
                lines = response.aiter_lines().__aiter__()
                while True:
                    if cancel_event.is_set():
                        yield {"stopped": True}
                        return
                    now = time.monotonic()
                    remaining = min(180 - (now - started), (60 if received_text else 90) - (now - last_text))
                    if remaining <= 0:
                        raise ModelFailure("MODEL_TIMEOUT", "等待回答超时，请稍后重试。")
                    if line_task is None:
                        line_task = asyncio.create_task(lines.__anext__())
                    ready, _ = await asyncio.wait({line_task, cancel_task}, timeout=min(15, remaining), return_when=asyncio.FIRST_COMPLETED)
                    if cancel_task in ready:
                        yield {"stopped": True}
                        return
                    if line_task not in ready:
                        yield {"heartbeat": True}
                        continue  # Keep the same read alive across heartbeats.
                    try:
                        line = line_task.result()
                    except StopAsyncIteration:
                        raise ModelFailure("MODEL_STREAM_INTERRUPTED", "回答中断，请重试。") from None
                    finally:
                        line_task = None
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        if not finish_reason or not received_text:
                            raise ModelFailure("MODEL_STREAM_INTERRUPTED", "回答中断，请重试。")
                        yield {"finish_reason": finish_reason}
                        return
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        raise ModelFailure("MODEL_STREAM_INTERRUPTED", "回答中断，请重试。") from None
                    if "error" in chunk:
                        raise ModelFailure("MODEL_STREAM_INTERRUPTED", "回答中断，请重试。")
                    choice = (chunk.get("choices") or [{}])[0]
                    text = (choice.get("delta") or {}).get("content")
                    if isinstance(text, str) and text:
                        received_text = True
                        last_text = time.monotonic()
                        yield {"text": text}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
    except ModelFailure:
        raise
    except httpx.TimeoutException:
        raise ModelFailure("MODEL_TIMEOUT", "等待回答超时，请稍后重试。") from None
    except (httpx.HTTPError, OSError):
        code = "MODEL_STREAM_INTERRUPTED" if received_text else "MODEL_UNAVAILABLE"
        message = "回答中断，请重试。" if received_text else "模型服务暂时不可用，请稍后重试。"
        raise ModelFailure(code, message) from None
    finally:
        for task in (line_task, cancel_task):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration, httpx.HTTPError):
                    await task
