"""LLaMA-Factory OpenAI API adapter. Run in the existing model environment.

The installed HF streaming implementation does not cancel its generate thread
and always reports finish_reason=stop. This launcher adds cancellation and
thread-exit acknowledgement without modifying LLaMA-Factory or model weights.
"""
import asyncio
import json
import logging
import os
import sys
import time
from queue import Empty
from threading import Event, Thread
from uuid import uuid4

import anyio
import torch
import uvicorn
from fastapi import Depends, Header, HTTPException
from starlette.responses import StreamingResponse
from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

from llamafactory.api.app import create_app
from llamafactory.api.protocol import ChatCompletionRequest, ModelCard, ModelList
from llamafactory.chat import ChatModel

from model_variants import ModelVariants


class StopRequested(StoppingCriteria):
    def __init__(self, event):
        self.event = event

    def __call__(self, input_ids, scores, **kwargs):
        return self.event.is_set()


def build_app(model):
    logging.getLogger("llamafactory.api.chat").setLevel(logging.WARNING)
    variants = ModelVariants(
        model.engine.model,
        os.getenv("API_MODEL_NAME", "health-assistant"),
        os.getenv("API_BASE_MODEL_NAME", "qwen3-1.7b-base"),
    )
    app = create_app(model)
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) not in {"/v1/chat/completions", "/v1/models"}]
    current = {"job": None}
    jobs = {}

    def authorize(authorization: str = Header(default="")):
        key = os.environ.get("API_KEY", "")
        if key and authorization != f"Bearer {key}":
            raise HTTPException(401, "Invalid API key")

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    def list_models():
        return ModelList(data=[ModelCard(id=name) for name in variants.names])

    @app.get("/v1/health/generation", dependencies=[Depends(authorize)])
    def generation_health():
        job = current["job"]
        return {"cancellation": "thread-join-v1", "active": bool(job and job["thread"] and job["thread"].is_alive())}

    @app.post("/v1/generations/{request_id}/cancel", dependencies=[Depends(authorize)])
    def cancel(request_id: str):
        job = jobs.get(request_id)
        if job:
            job["cancel"].set()
            job["thread"].join()
        return {"found": job is not None, "running": bool(job and job["thread"].is_alive())}

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def completions(payload: ChatCompletionRequest, x_generation_id: str = Header(default="")):
        if payload.model not in variants.names:
            raise HTTPException(404, "Unknown model")
        if not payload.stream or payload.n > 1 or payload.tools:
            raise HTTPException(400, "Only single text streaming is supported by this launcher")
        if current["job"] and current["job"]["thread"].is_alive():
            raise HTTPException(429, "Model busy")
        # Use LLaMA-Factory's request validation and actual qwen3_nothink encoding.
        from llamafactory.api.chat import _process_request
        messages, system, tools, images, videos, audios = _process_request(payload)
        if images or videos or audios or tools:
            raise HTTPException(400, "Only text is supported")
        engine = model.engine
        kwargs, _ = engine._process_args(
            engine.model, engine.tokenizer, engine.processor, engine.template,
            engine.generating_args, messages, system, None, None, None, None,
            {"temperature": payload.temperature, "top_p": payload.top_p,
             "max_new_tokens": payload.max_tokens, "do_sample": payload.do_sample},
        )
        job = {"cancel": Event(), "finished": Event(), "reason": None, "error": None, "thread": None}
        request_id = x_generation_id or str(uuid4())
        # Keep bounded diagnostic state, never retain prompts or replies here.
        if len(jobs) > 100:
            for key in list(jobs)[:-20]:
                if jobs[key]["finished"].is_set():
                    jobs.pop(key)
        jobs[request_id] = job
        current["job"] = job
        streamer = TextIteratorStreamer(engine.tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=0.2)
        kwargs["streamer"] = streamer
        kwargs["stopping_criteria"] = StoppingCriteriaList([StopRequested(job["cancel"])])

        def generate():
            try:
                with variants.generation_context(payload.model), torch.inference_mode():
                    logging.getLogger("uvicorn.error").info(
                        "generation model=%s adapter_enabled=%s", payload.model,
                        engine.model.get_model_status().enabled,
                    )
                    output = engine.model.generate(**kwargs)
                eos_ids = kwargs["generation_config"].eos_token_id
                if isinstance(eos_ids, int):
                    eos_ids = [eos_ids]
                job["reason"] = "stop" if job["cancel"].is_set() or int(output[0, -1]) in (eos_ids or []) else "length"
            except BaseException as exc:
                job["error"] = type(exc).__name__
                streamer.on_finalized_text("", stream_end=True)
            finally:
                job["finished"].set()

        thread = Thread(target=generate, daemon=True, name="health-model-generate")
        job["thread"] = thread
        thread.start()

        def chunk(content=None, reason=None):
            data = {"id": request_id, "object": "chat.completion.chunk", "model": payload.model,
                    "choices": [{"index": 0, "delta": {"content": content} if content is not None else {}, "finish_reason": reason}]}
            return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"

        def take():
            try:
                return "text", next(streamer)
            except Empty:
                return "wait", ""
            except StopIteration:
                return "end", ""

        async def stream():
            started = time.monotonic()
            try:
                yield chunk("")
                while True:
                    kind, text = await asyncio.to_thread(take)
                    if time.monotonic() - started > 180:
                        job["cancel"].set()
                    if kind == "end":
                        break
                    if kind == "text" and text:
                        yield chunk(text)
                await asyncio.to_thread(thread.join)
                if job["error"]:
                    yield 'data: {"error":{"code":"GENERATION_FAILED"}}\n\n'
                else:
                    yield chunk(reason=job["reason"])
                    yield "data: [DONE]\n\n"
            finally:
                job["cancel"].set()
                # Keep model permit occupied until the actual GPU thread exits.
                with anyio.CancelScope(shield=True):
                    await asyncio.to_thread(thread.join)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python serve_model.py /path/to/psychology_infer.yaml")
    import yaml
    with open(sys.argv[1], encoding="utf-8") as source:
        config = yaml.safe_load(source)
    os.environ.setdefault("API_MODEL_NAME", "health-assistant")
    chat_model = ChatModel(config)
    uvicorn.run(build_app(chat_model), host=os.getenv("API_HOST", "127.0.0.1"), port=int(os.getenv("API_PORT", "8000")))
