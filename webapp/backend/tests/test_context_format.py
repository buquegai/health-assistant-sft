from uuid import uuid4

from app.config import settings
from app.db import SessionLocal
from app.main import _completed_pairs
from app.models import Message
from app.token_budget import render_qwen3_nothink


def test_qwen3_nothink_prompt_keeps_role_boundaries_and_utf8_text():
    rendered = render_qwen3_nothink([
        {"role": "system", "content": "健康提示"},
        {"role": "user", "content": "最近在准备考试"},
        {"role": "assistant", "content": "可以分段安排复习。"},
        {"role": "user", "content": "刚才说的事情让我紧张怎么办"},
    ])
    assert rendered.startswith("<|im_start|>system\n健康提示<|im_end|>")
    assert "<|im_start|>assistant\n可以分段安排复习。<|im_end|>" in rendered
    assert rendered.endswith("<|im_start|>assistant\n")


def test_completed_context_is_scoped_to_its_conversation(client):
    registered = client.post(
        "/api/auth/register",
        json={"username": "context_owner", "password": "Health1234"},
        headers={"Origin": settings.app_origin},
    )
    assert registered.status_code == 201
    first = client.post("/api/conversations", json={}, headers={"Origin": settings.app_origin}).json()
    second = client.post("/api/conversations", json={}, headers={"Origin": settings.app_origin}).json()
    with SessionLocal() as db:
        question = Message(
            conversation_id=first["id"],
            role="user",
            content="只属于第一个会话的内容",
            status="completed",
            client_request_id=str(uuid4()),
        )
        db.add(question)
        db.flush()
        db.add(Message(
            conversation_id=first["id"],
            role="assistant",
            content="第一个会话的回答",
            status="completed",
            reply_to_id=question.id,
        ))
        db.commit()

        assert _completed_pairs(db, first["id"]) == [("只属于第一个会话的内容", "第一个会话的回答")]
        assert _completed_pairs(db, second["id"]) == []
