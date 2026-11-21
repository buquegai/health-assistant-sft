from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, inspect, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import BACKEND_DIR, settings
from .db import Base, SessionLocal, engine, get_db
from .model_client import ModelFailure, confirm_generation_exit, stream_completion, verify_model_runtime
from .models import Conversation, LoginSession, Message, User, utcnow
from .model_registry import DEFAULT_MODEL_ID, ModelConfig, get_model, inference_config, public_models
from .rate_limit import rate_limiter
from .security import (
    delete_session,
    get_session_user,
    hash_password,
    normalize_username,
    new_session,
    validate_password,
    validate_username,
    verify_password,
)
from .token_budget import fit_context


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("health_assistant")
@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if "model_id" not in {column["name"] for column in inspect(engine).get_columns("conversations")}:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE conversations ADD COLUMN model_id VARCHAR(64) NOT NULL DEFAULT 'health-assistant'"))
    with SessionLocal() as db:
        stale = db.scalars(select(Message).where(Message.role == "assistant", Message.status == "generating")).all()
        for message in stale:
            message.status = "failed"
            message.error_code = "SERVER_RESTARTED"
            message.updated_at = utcnow()
        if stale:
            db.commit()
    yield


app = FastAPI(title="健康助手 API", docs_url=None, redoc_url=None, lifespan=lifespan)
generation_guard = asyncio.Lock()
generation_active = False
busy_users: set[str] = set()


class GenerationState:
    def __init__(self, assistant_id: str, conversation_id: str, user_id: str, model_id: str) -> None:
        self.assistant_id = assistant_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.model_id = model_id
        self.cancel = asyncio.Event()
        self.done = asyncio.Event()
        self.stop_confirmed = False
        self.release_slot = True


active_generations: dict[str, GenerationState] = {}


class Credentials(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=256)


class Registration(Credentials):
    pass


class SendMessage(BaseModel):
    content: str = Field(max_length=2000)
    client_request_id: UUID


class CreateConversation(BaseModel):
    model_id: str = Field(default=DEFAULT_MODEL_ID, min_length=1, max_length=64)


class SelectModel(BaseModel):
    model_id: str = Field(min_length=1, max_length=64)


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}}, headers=headers)


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        body = exc.detail
    else:
        body = {"code": "REQUEST_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": body}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "VALIDATION_ERROR", "提交内容不符合要求，请检查后重试。")


@app.middleware("http")
async def origin_and_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(UUID(bytes=__import__("secrets").token_bytes(16), version=4))
    request.state.request_id = request_id
    if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin != settings.app_origin:
            return error_response(403, "ORIGIN_FORBIDDEN", "请求来源无效。", {"X-Request-ID": request_id})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    found = get_session_user(db, request.cookies.get(settings.cookie_name))
    if not found:
        raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "登录已过期，请重新登录。"})
    return found[1]


def _user_payload(user: User) -> dict[str, str]:
    return {"id": user.id, "username": user.username}


async def _reserve_generation(user_id: str) -> None:
    global generation_active
    async with generation_guard:
        if generation_active or user_id in busy_users:
            raise HTTPException(429, {"code": "MODEL_BUSY", "message": "模型正在处理其他请求，请稍后重试。"})
        generation_active = True
        busy_users.add(user_id)


async def _release_generation(user_id: str) -> None:
    global generation_active
    async with generation_guard:
        generation_active = False
        busy_users.discard(user_id)


def _conversation_for_user(db: Session, conversation_id: str, user_id: str) -> Conversation:
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))
    if not conversation:
        raise HTTPException(404, {"code": "CONVERSATION_NOT_FOUND", "message": "会话不存在或无法访问。"})
    return conversation


def _model_config(model_id: str) -> ModelConfig:
    try:
        return get_model(model_id)
    except KeyError:
        raise HTTPException(422, {"code": "MODEL_NOT_AVAILABLE", "message": "所选模型不可用，请刷新后重试。"}) from None


def _conversation_cursor(updated_at: datetime, conversation_id: str) -> str:
    value = f"{updated_at.isoformat()}|{conversation_id}".encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_conversation_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        timestamp, conversation_id = raw.rsplit("|", 1)
        return datetime.fromisoformat(timestamp), conversation_id
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, {"code": "INVALID_CURSOR", "message": "分页位置无效，请刷新后重试。"}) from None


def _conversation_payload(item: Conversation) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "model_id": item.model_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _message_payload(item: Message) -> dict[str, Any]:
    return {
        "id": item.id,
        "conversation_id": item.conversation_id,
        "role": item.role,
        "content": item.content,
        "status": item.status,
        "reply_to_id": item.reply_to_id,
        "finish_reason": item.finish_reason,
        "error_code": item.error_code,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _completed_pairs(db: Session, conversation_id: str, before_seq: int | None = None) -> list[tuple[str, str]]:
    conditions = [
        Message.conversation_id == conversation_id,
        Message.role == "assistant",
        Message.status == "completed",
        Message.reply_to_id.is_not(None),
    ]
    if before_seq is not None:
        conditions.append(Message.seq < before_seq)
    assistants = db.scalars(select(Message).where(*conditions).order_by(Message.seq.desc())).all()
    pairs: list[tuple[str, str]] = []
    for assistant in assistants:
        user_message = db.scalar(select(Message).where(Message.id == assistant.reply_to_id, Message.role == "user"))
        if user_message:
            pairs.append((user_message.content, assistant.content))
    pairs.reverse()
    return pairs


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")


def _update_message(message_id: str, **values: Any) -> None:
    with SessionLocal() as db:
        message = db.scalar(select(Message).where(Message.id == message_id))
        if message:
            for key, value in values.items():
                setattr(message, key, value)
            message.updated_at = utcnow()
            db.commit()


def _touch_conversation(conversation_id: str) -> None:
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation:
            conversation.updated_at = utcnow()
            db.commit()


def _user_question(db: Session, message_id: str) -> Message | None:
    return db.scalar(select(Message).where(Message.id == message_id, Message.role == "user"))


async def _generation_stream(
    state: GenerationState,
    user_message_id: str,
    context: list[dict[str, str]],
    history_trimmed: bool,
) -> Any:
    assistant_id = state.assistant_id
    model = _model_config(state.model_id)
    final_status = "failed"
    request_started = time.monotonic()
    text_buffer = ""
    last_saved = time.monotonic()
    yield _sse("meta", {
        "conversation_id": state.conversation_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_id,
        "history_trimmed": history_trimmed,
    })
    try:
        async for item in stream_completion(context, state.cancel, assistant_id, model):
            if item.get("heartbeat"):
                yield b": heartbeat\n\n"
                continue
            if item.get("stopped"):
                if not await confirm_generation_exit(assistant_id, model):
                    state.release_slot = False
                    final_status = "failed"
                    _update_message(assistant_id, status="failed", error_code="MODEL_CANCEL_UNCONFIRMED")
                    yield _sse("error", {
                        "code": "MODEL_CANCEL_UNCONFIRMED",
                        "message": "模型仍在停止，暂时无法接受新问题。",
                        "assistant_message_id": assistant_id,
                    })
                    return
                final_status = "stopped"
                state.stop_confirmed = True
                _update_message(assistant_id, status="stopped", error_code=None)
                yield _sse("done", {"status": "stopped", "finish_reason": "stop"})
                return
            fragment = item.get("text")
            if isinstance(fragment, str) and fragment:
                text_buffer += fragment
                now = time.monotonic()
                if now - last_saved >= 1:
                    _update_message(assistant_id, content=text_buffer, status="generating")
                    last_saved = now
                yield _sse("delta", {"text": fragment})
            finish_reason = item.get("finish_reason")
            if isinstance(finish_reason, str):
                _update_message(assistant_id, content=text_buffer, status="completed", finish_reason=finish_reason, error_code=None)
                _touch_conversation(state.conversation_id)
                final_status = "completed"
                yield _sse("done", {"status": "completed", "finish_reason": finish_reason})
                return
        if state.cancel.is_set():
            final_status = "stopped"
            _update_message(assistant_id, content=text_buffer, status="stopped", finish_reason="stop", error_code=None)
            yield _sse("done", {"status": "stopped", "finish_reason": "stop"})
        else:
            _update_message(assistant_id, content=text_buffer, status="completed", finish_reason="stop", error_code=None)
            _touch_conversation(state.conversation_id)
            final_status = "completed"
            yield _sse("done", {"status": "completed", "finish_reason": "stop"})
    except asyncio.CancelledError:
        state.cancel.set()
        state.stop_confirmed = await confirm_generation_exit(assistant_id, model)
        if state.stop_confirmed:
            final_status = "stopped"
            _update_message(assistant_id, content=text_buffer, status="stopped", finish_reason="disconnect", error_code=None)
        else:
            state.release_slot = False
            final_status = "failed"
            _update_message(assistant_id, content=text_buffer, status="failed", error_code="MODEL_CANCEL_UNCONFIRMED")
        raise
    except ModelFailure as exc:
        final_status = "failed"
        if exc.code == "MODEL_CANCEL_UNCONFIRMED":
            state.release_slot = False
        _update_message(assistant_id, content=text_buffer, status="failed", error_code=exc.code)
        yield _sse("error", {"code": exc.code, "message": exc.message, "assistant_message_id": assistant_id})
    except Exception as exc:
        logger.warning("generation failed request_id=%s error_type=%s", assistant_id, type(exc).__name__)
        final_status = "failed"
        _update_message(assistant_id, content=text_buffer, status="failed", error_code="MODEL_UNAVAILABLE")
        yield _sse("error", {"code": "MODEL_UNAVAILABLE", "message": "模型服务暂时不可用，请稍后重试。", "assistant_message_id": assistant_id})
    finally:
        state.done.set()
        active_generations.pop(assistant_id, None)
        if state.release_slot:
            await _release_generation(state.user_id)
        logger.info("generation request_id=%s status=%s elapsed_ms=%d", assistant_id, final_status, int((time.monotonic() - request_started) * 1000))


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/models")
def list_models(_user: User = Depends(current_user)):
    return public_models()


@app.post("/api/auth/register", status_code=201)
def register(payload: Registration, response: Response, request: Request, db: Session = Depends(get_db)):
    allowed, wait_seconds = rate_limiter.register_allowed(_client_key(request))
    if not allowed:
        raise HTTPException(429, {"code": "RATE_LIMITED", "message": f"注册尝试过多，请在 {wait_seconds} 秒后重试。"}, {"Retry-After": str(wait_seconds)})
    username_error = validate_username(payload.username)
    if username_error:
        raise HTTPException(422, {"code": "INVALID_USERNAME", "message": username_error})
    password_error = validate_password(payload.password)
    if password_error:
        raise HTTPException(422, {"code": "INVALID_PASSWORD", "message": password_error})
    username = normalize_username(payload.username)
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(409, {"code": "USERNAME_TAKEN", "message": "用户名已被使用。"})
    user = User(username=username, password_hash=hash_password(payload.password))
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, {"code": "USERNAME_TAKEN", "message": "用户名已被使用。"}) from None
    token = new_session(db, user)
    _set_cookie(response, token)
    return {"user": _user_payload(user)}


@app.post("/api/auth/login")
def login(payload: Credentials, response: Response, request: Request, db: Session = Depends(get_db)):
    source = _client_key(request)
    wait_seconds = rate_limiter.login_wait_seconds(source)
    if wait_seconds:
        raise HTTPException(429, {"code": "RATE_LIMITED", "message": f"尝试次数过多，请稍后再试（还需等待 {wait_seconds} 秒）。"}, {"Retry-After": str(wait_seconds)})
    username = normalize_username(payload.username)
    user = db.scalar(select(User).where(User.username == username)) if validate_username(payload.username) is None else None
    valid = bool(user and verify_password(user.password_hash, payload.password))
    if not valid:
        wait_seconds = rate_limiter.login_failed(source)
        headers = {"Retry-After": str(wait_seconds)} if wait_seconds else None
        message = f"尝试次数过多，请稍后再试（还需等待 {wait_seconds} 秒）。" if wait_seconds else "用户名或密码错误。"
        code = "RATE_LIMITED" if wait_seconds else "INVALID_CREDENTIALS"
        raise HTTPException(429 if wait_seconds else 401, {"code": code, "message": message}, headers)
    rate_limiter.login_succeeded(source)
    token = new_session(db, user)
    _set_cookie(response, token)
    return {"user": _user_payload(user)}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_payload(user)}


@app.post("/api/auth/logout", status_code=204)
async def logout(request: Request, response: Response, db: Session = Depends(get_db), user: User = Depends(current_user)):
    pending = [state for state in active_generations.values() if state.user_id == user.id]
    for state in pending:
        state.cancel.set()
    if pending:
        try:
            await asyncio.wait_for(asyncio.gather(*(state.done.wait() for state in pending)), timeout=15)
        except asyncio.TimeoutError:
            raise HTTPException(503, {"code": "STOP_TIMEOUT", "message": "回答仍在停止，请稍后重试退出。"}) from None
    delete_session(db, request.cookies.get(settings.cookie_name))
    _clear_cookie(response)
    response.status_code = 204
    return response


@app.get("/api/conversations")
def list_conversations(
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    query = select(Conversation).where(Conversation.user_id == user.id)
    if cursor:
        timestamp, conversation_id = _decode_conversation_cursor(cursor)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        query = query.where(or_(Conversation.updated_at < timestamp, and_(Conversation.updated_at == timestamp, Conversation.id < conversation_id)))
    entries = db.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit + 1)).all()
    has_more = len(entries) > limit
    entries = entries[:limit]
    next_cursor = _conversation_cursor(entries[-1].updated_at, entries[-1].id) if has_more and entries else None
    return {"items": [_conversation_payload(item) for item in entries], "next_cursor": next_cursor}


@app.post("/api/conversations", status_code=201)
def create_conversation(
    payload: CreateConversation | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    model_id = payload.model_id if payload else DEFAULT_MODEL_ID
    _model_config(model_id)
    conversation = Conversation(user_id=user.id, title="新对话", model_id=model_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return _conversation_payload(conversation)


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _conversation_payload(_conversation_for_user(db, str(conversation_id), user.id))


@app.patch("/api/conversations/{conversation_id}/model")
def select_conversation_model(
    conversation_id: UUID,
    payload: SelectModel,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = _conversation_for_user(db, str(conversation_id), user.id)
    if conversation.id in {state.conversation_id for state in active_generations.values()}:
        raise HTTPException(409, {"code": "GENERATION_ACTIVE", "message": "请先停止当前回答。"})
    _model_config(payload.model_id)
    conversation.model_id = payload.model_id
    conversation.updated_at = utcnow()
    db.commit()
    db.refresh(conversation)
    return _conversation_payload(conversation)


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    conversation = _conversation_for_user(db, str(conversation_id), user.id)
    if conversation.id in {state.conversation_id for state in active_generations.values()}:
        raise HTTPException(409, {"code": "GENERATION_ACTIVE", "message": "请先停止当前回答。"})
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


@app.get("/api/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: UUID,
    before: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = _conversation_for_user(db, str(conversation_id), user.id)
    query = select(Message).where(Message.conversation_id == conversation.id)
    if before is not None:
        query = query.where(Message.seq < before)
    entries = db.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)).all()
    has_more = len(entries) > limit
    entries = list(reversed(entries[:limit]))
    next_cursor = entries[0].seq if has_more and entries else None
    return {"items": [_message_payload(item) for item in entries], "next_cursor": next_cursor}


async def _prepare_send(
    conversation: Conversation,
    question: str,
    request_id: str,
    db: Session,
    user: User,
) -> tuple[GenerationState, str, list[dict[str, str]], bool]:
    model = _model_config(conversation.model_id)
    normalized = question.strip()
    if not normalized:
        raise HTTPException(422, {"code": "EMPTY_MESSAGE", "message": "请输入你的问题。"})
    if len(normalized) > 2000:
        raise HTTPException(422, {"code": "MESSAGE_TOO_LONG", "message": "最多输入 2000 个字符。"})
    if db.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.client_request_id == request_id)):
        raise HTTPException(409, {"code": "DUPLICATE_REQUEST", "message": "这条消息已接收，请刷新会话确认结果。"})
    try:
        await verify_model_runtime(model)
    except ModelFailure as exc:
        raise HTTPException(503, {"code": exc.code, "message": exc.message}) from None
    try:
        model_inference = inference_config(model.id)
        context, trimmed = fit_context(
            model_inference["system_prompt"],
            _completed_pairs(db, conversation.id),
            normalized,
            model_inference["tokenizer_path"],
        )
    except ValueError as exc:
        if str(exc) == "PROMPT_TOO_LONG":
            raise HTTPException(422, {"code": "PROMPT_TOO_LONG", "message": "问题过长，请精简后发送。"}) from None
        raise
    except Exception as exc:
        logger.warning("tokenizer unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(503, {"code": "TOKENIZER_UNAVAILABLE", "message": "模型分词器暂时不可用，请检查服务配置。"}) from None
    await _reserve_generation(user.id)
    try:
        if conversation.title == "新对话":
            compact = re.sub(r"\s+", " ", normalized)
            conversation.title = compact[:20] + ("…" if len(compact) > 20 else "")
        user_message = Message(
            conversation_id=conversation.id,
            role="user",
            content=normalized,
            status="completed",
            client_request_id=request_id,
        )
        db.add(user_message)
        db.flush()
        assistant_message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="generating",
            reply_to_id=user_message.id,
        )
        db.add(assistant_message)
        conversation.updated_at = utcnow()
        db.commit()
        db.refresh(user_message)
        db.refresh(assistant_message)
    except Exception:
        db.rollback()
        await _release_generation(user.id)
        raise
    state = GenerationState(assistant_message.id, conversation.id, user.id, model.id)
    active_generations[assistant_message.id] = state
    return state, user_message.id, context, trimmed


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    payload: SendMessage,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    conversation = _conversation_for_user(db, str(conversation_id), user.id)
    state, user_message_id, context, trimmed = await _prepare_send(conversation, payload.content, str(payload.client_request_id), db, user)
    return _sse_response(_generation_stream(state, user_message_id, context, trimmed))


@app.post("/api/messages/{message_id}/stop", status_code=204)
async def stop_message(message_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    assistant = db.scalar(select(Message).where(Message.id == str(message_id), Message.role == "assistant"))
    if not assistant:
        raise HTTPException(404, {"code": "MESSAGE_NOT_FOUND", "message": "消息不存在或无法访问。"})
    conversation = _conversation_for_user(db, assistant.conversation_id, user.id)
    if assistant.status != "generating":
        return Response(status_code=204)
    state = active_generations.get(assistant.id)
    if not state:
        _update_message(assistant.id, status="failed", error_code="SERVER_RESTARTED")
        return Response(status_code=204)
    state.cancel.set()
    try:
        await asyncio.wait_for(state.done.wait(), timeout=15)
    except asyncio.TimeoutError:
        raise HTTPException(503, {"code": "STOP_TIMEOUT", "message": "回答仍在停止，请稍后重试。"}) from None
    if not state.stop_confirmed:
        raise HTTPException(503, {"code": "MODEL_CANCEL_UNCONFIRMED", "message": "模型仍在停止，暂时无法接受新问题。"})
    return Response(status_code=204)


@app.post("/api/messages/{message_id}/retry")
async def retry_message(message_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    assistant = db.scalar(select(Message).where(Message.id == str(message_id), Message.role == "assistant"))
    if not assistant:
        raise HTTPException(404, {"code": "MESSAGE_NOT_FOUND", "message": "消息不存在或无法访问。"})
    conversation = _conversation_for_user(db, assistant.conversation_id, user.id)
    latest = db.scalar(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.seq.desc()).limit(1))
    if latest is None or latest.id != assistant.id or assistant.status not in {"failed", "stopped"}:
        raise HTTPException(409, {"code": "RETRY_NOT_ALLOWED", "message": "仅能重新生成当前会话最后一条未完成回答。"})
    user_message = _user_question(db, assistant.reply_to_id or "")
    if not user_message:
        raise HTTPException(409, {"code": "RETRY_NOT_ALLOWED", "message": "原问题不存在，无法重新生成。"})
    model = _model_config(conversation.model_id)
    try:
        await verify_model_runtime(model)
    except ModelFailure as exc:
        raise HTTPException(503, {"code": exc.code, "message": exc.message}) from None
    try:
        model_inference = inference_config(model.id)
        context, trimmed = fit_context(
            model_inference["system_prompt"],
            _completed_pairs(db, conversation.id, assistant.seq),
            user_message.content,
            model_inference["tokenizer_path"],
        )
    except ValueError:
        raise HTTPException(422, {"code": "PROMPT_TOO_LONG", "message": "问题过长，请精简后发送。"}) from None
    except Exception as exc:
        logger.warning("tokenizer unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(503, {"code": "TOKENIZER_UNAVAILABLE", "message": "模型分词器暂时不可用，请检查服务配置。"}) from None
    await _reserve_generation(user.id)
    assistant.content = ""
    assistant.status = "generating"
    assistant.finish_reason = None
    assistant.error_code = None
    assistant.updated_at = utcnow()
    db.commit()
    state = GenerationState(assistant.id, conversation.id, user.id, model.id)
    active_generations[assistant.id] = state
    return _sse_response(_generation_stream(state, user_message.id, context, trimmed))


frontend_dist = BACKEND_DIR.parent / "frontend" / "dist"
assets_dir = frontend_dist / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend_fallback(path: str):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, {"code": "NOT_FOUND", "message": "接口不存在。"})
    index = frontend_dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse({"message": "前端尚未构建，请在 frontend 目录启动 Vite。"}, status_code=404)
