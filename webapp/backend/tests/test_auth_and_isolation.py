import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.config import settings
from app.models import LoginSession, Message
from app.model_registry import DEFAULT_MODEL_ID, MODELS, ModelConfig, public_models


ORIGIN = settings.app_origin


def register(client, username):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "Health1234"},
        headers={"Origin": ORIGIN},
    )


def test_register_normalizes_username_and_sets_hardened_cookie(client):
    response = register(client, "  Demo_User  ")
    assert response.status_code == 201
    assert response.json()["user"]["username"] == "demo_user"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=604800" in cookie
    assert "password_hash" not in response.text
    assert "health1234" not in response.text.lower()


def test_registration_validation_and_duplicate_are_specific(client):
    invalid = client.post(
        "/api/auth/register",
        json={"username": "ab", "password": "abcdefgh"},
        headers={"Origin": ORIGIN},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_USERNAME"
    assert register(client, "Case_User").status_code == 201
    duplicate = register(client, " case_user ")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["message"] == "用户名已被使用。"


def test_cookie_session_survives_refresh_and_logout_revokes_old_token(client):
    response = register(client, "stay_user")
    old_token = client.cookies.get("health_session")
    assert client.get("/api/auth/me").json()["user"]["username"] == "stay_user"
    logout = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert logout.status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    revoked = client.get("/api/auth/me", cookies={"health_session": old_token})
    assert revoked.status_code == 401
    assert response.status_code == 201


def test_expired_cookie_session_is_rejected(client):
    assert register(client, "expired_user").status_code == 201
    with SessionLocal() as db:
        login_session = db.scalar(select(LoginSession))
        assert login_session is not None
        login_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert client.get("/api/auth/me", cookies={"health_session": client.cookies.get("health_session")}).status_code == 401


def test_origin_is_required_for_state_changes(client):
    response = client.post("/api/conversations", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_FORBIDDEN"


def test_model_catalog_and_conversation_selection_are_allowlisted(client, monkeypatch):
    assert register(client, "model_user").status_code == 201
    alternate = ModelConfig(
        id="demo-alternate",
        name="验收用备用模型",
        base_url="http://127.0.0.1:9999/v1",
        model_name="demo-model",
        api_key="not-returned",
        inference_config_path="/private/model/config.yaml",
    )
    monkeypatch.setitem(MODELS, alternate.id, alternate)
    response = client.get("/api/models")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog == public_models()
    assert catalog["default_model_id"] == DEFAULT_MODEL_ID
    assert {item["id"] for item in catalog["items"]} == set(MODELS)
    assert {DEFAULT_MODEL_ID, "qwen3-base", alternate.id} <= set(MODELS)
    assert "base_url" not in response.text
    assert "api_key" not in response.text

    created = client.post("/api/conversations", json={"model_id": DEFAULT_MODEL_ID}, headers={"Origin": ORIGIN})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["model_id"] == DEFAULT_MODEL_ID
    assert client.get(f"/api/conversations/{conversation_id}").json()["model_id"] == DEFAULT_MODEL_ID

    changed = client.patch(
        f"/api/conversations/{conversation_id}/model",
        json={"model_id": alternate.id},
        headers={"Origin": ORIGIN},
    )
    assert changed.status_code == 200
    assert changed.json()["model_id"] == alternate.id
    assert client.get(f"/api/conversations/{conversation_id}").json()["model_id"] == alternate.id
    separate = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    assert separate.json()["model_id"] == DEFAULT_MODEL_ID
    no_body = client.post("/api/conversations", headers={"Origin": ORIGIN})
    assert no_body.status_code == 201
    assert no_body.json()["model_id"] == DEFAULT_MODEL_ID

    invalid_create = client.post("/api/conversations", json={"model_id": "http://attacker.invalid"}, headers={"Origin": ORIGIN})
    assert invalid_create.status_code == 422
    invalid_change = client.patch(
        f"/api/conversations/{conversation_id}/model",
        json={"model_id": "http://attacker.invalid"},
        headers={"Origin": ORIGIN},
    )
    assert invalid_change.status_code == 422
    assert client.get(f"/api/conversations/{conversation_id}").json()["model_id"] == alternate.id


def test_conversation_model_change_is_isolated_by_account(client):
    assert register(client, "model_owner").status_code == 201
    created = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    conversation_id = created.json()["id"]
    owner_cookie = client.cookies.get("health_session")
    client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert register(client, "model_other").status_code == 201
    response = client.patch(
        f"/api/conversations/{conversation_id}/model",
        json={"model_id": DEFAULT_MODEL_ID},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 404
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/api/conversations/{conversation_id}", cookies={"health_session": owner_cookie}).status_code == 401


def test_conversations_are_isolated_and_foreign_ids_are_not_disclosed(client):
    assert register(client, "owner_user").status_code == 201
    created = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    owner_cookie = client.cookies.get("health_session")

    client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert register(client, "other_user").status_code == 201
    assert client.get("/api/conversations").json()["items"] == []
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    denied_delete = client.delete(f"/api/conversations/{conversation_id}", headers={"Origin": ORIGIN})
    assert denied_delete.status_code == 404

    client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    owner = client.get("/api/auth/me", cookies={"health_session": owner_cookie})
    assert owner.status_code == 401


def test_other_account_cannot_stop_or_retry_foreign_message(client):
    assert register(client, "message_owner").status_code == 201
    conversation_response = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    conversation_id = conversation_response.json()["id"]
    question_id = str(uuid4())
    assistant_id = str(uuid4())
    with SessionLocal() as db:
        question = Message(
            id=question_id,
            conversation_id=conversation_id,
            role="user",
            content="合成验收问题",
            status="completed",
            client_request_id=str(uuid4()),
        )
        db.add(question)
        db.flush()
        db.add(Message(
            id=assistant_id,
            conversation_id=conversation_id,
            role="assistant",
            content="部分回答",
            status="stopped",
            reply_to_id=question.id,
        ))
        db.commit()

    client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert register(client, "message_other").status_code == 201
    stop = client.post(f"/api/messages/{assistant_id}/stop", headers={"Origin": ORIGIN})
    retry = client.post(f"/api/messages/{assistant_id}/retry", headers={"Origin": ORIGIN})
    assert stop.status_code == 404
    assert retry.status_code == 404
    assert "message_owner" not in stop.text
    assert "合成验收问题" not in retry.text


def test_owner_deletion_makes_messages_unavailable(client):
    assert register(client, "delete_owner").status_code == 201
    conversation_response = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    conversation_id = conversation_response.json()["id"]
    with SessionLocal() as db:
        db.add(Message(
            conversation_id=conversation_id,
            role="user",
            content="合成删除验收消息",
            status="completed",
            client_request_id=str(uuid4()),
        ))
        db.commit()

    deleted = client.delete(f"/api/conversations/{conversation_id}", headers={"Origin": ORIGIN})
    assert deleted.status_code == 204
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert client.delete(f"/api/conversations/{conversation_id}", headers={"Origin": ORIGIN}).status_code == 404


def test_restart_marks_generating_answer_failed(client):
    assert register(client, "restart_owner").status_code == 201
    conversation_response = client.post("/api/conversations", json={}, headers={"Origin": ORIGIN})
    conversation_id = conversation_response.json()["id"]
    with SessionLocal() as db:
        question = Message(
            conversation_id=conversation_id,
            role="user",
            content="合成重启恢复问题",
            status="completed",
            client_request_id=str(uuid4()),
        )
        db.add(question)
        db.flush()
        assistant = Message(
            conversation_id=conversation_id,
            role="assistant",
            content="重启前的部分回答",
            status="generating",
            reply_to_id=question.id,
        )
        db.add(assistant)
        db.commit()
        assistant_id = assistant.id

    async def restart_application():
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(restart_application())
    response = client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    recovered = next(item for item in response.json()["items"] if item["id"] == assistant_id)
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "SERVER_RESTARTED"
