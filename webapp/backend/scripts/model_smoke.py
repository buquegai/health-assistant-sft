from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.models import User


APP_URL = "http://127.0.0.1:8080"
APP_ORIGIN = "http://localhost:5173"


def main(all_models: bool = False) -> None:
    username = f"smoke_{uuid4().hex[-10:]}"
    headers = {"Origin": APP_ORIGIN}
    conversation_id = None
    assistant_message_id = None
    client = httpx.Client(base_url=APP_URL, timeout=httpx.Timeout(190, connect=10))
    try:
        registered = client.post(
            "/api/auth/register",
            json={"username": username, "password": "Health2026"},
            headers=headers,
        )
        registered.raise_for_status()
        catalog = client.get("/api/models")
        catalog.raise_for_status()
        model_id = catalog.json()["default_model_id"]
        selected_models = [model_id]
        if all_models:
            selected_models = [item["id"] for item in catalog.json()["items"]] + [model_id]
        created = client.post("/api/conversations", json={"model_id": model_id}, headers=headers)
        created.raise_for_status()
        conversation_id = created.json()["id"]

        for selected in selected_models:
            changed = client.patch(f"/api/conversations/{conversation_id}/model", json={"model_id": selected}, headers=headers)
            changed.raise_for_status()
            answer = ""
            event_name = ""
            completed = False
            with client.stream(
                "POST",
                f"/api/conversations/{conversation_id}/messages",
                json={"content": "你好，请只简短回复你好。", "client_request_id": str(uuid4())},
                headers=headers,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if event_name == "meta":
                            assistant_message_id = data["assistant_message_id"]
                        elif event_name == "delta":
                            answer += data.get("text", "")
                        elif event_name == "error":
                            raise RuntimeError(data.get("message", "model request failed"))
                        elif event_name == "done":
                            completed = data.get("status") == "completed"
            if not answer.strip() or not completed:
                raise RuntimeError("model returned an empty or incomplete answer")
            saved = client.get(f"/api/conversations/{conversation_id}")
            saved.raise_for_status()
            if saved.json()["model_id"] != selected:
                raise RuntimeError("conversation model selection was not persisted")
            print(f"PASS real model selection and streaming: model_id={selected}, answer_chars={len(answer)}", flush=True)
    finally:
        if assistant_message_id:
            try:
                client.post(f"/api/messages/{assistant_message_id}/stop", headers=headers)
            except httpx.HTTPError:
                pass
        if conversation_id:
            client.delete(f"/api/conversations/{conversation_id}", headers=headers)
        try:
            client.post("/api/auth/logout", headers=headers)
        except httpx.HTTPError:
            pass
        client.close()
        with SessionLocal() as db:
            test_user = db.scalar(select(User).where(User.username == username))
            if test_user:
                db.delete(test_user)
                db.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-models", action="store_true", help="Switch through every model, then back to the default")
    main(parser.parse_args().all_models)
