"""HTTP-Server der Chat-App (nur 127.0.0.1, geschützt durch ein Sitzungstoken)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import NotFoundError
from pydantic import BaseModel

from . import agent, config, db, uploads

log = logging.getLogger("agentchat")
STATIC = Path(__file__).parent / "static"
TOKEN = os.environ.get("AGENTCHAT_TOKEN") or secrets.token_urlsafe(32)

app = FastAPI(title="Agent Chat", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def require_token(request: Request, call_next):
    # Schutz gegen Webseiten im Browser, die localhost ansprechen: eigener Header
    # erzwingt einen CORS-Preflight, den dieser Server nie erlaubt.
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-agentchat-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, TOKEN):
            return JSONResponse({"detail": "Nicht autorisiert"}, status_code=401)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- Einstellungen ----------------------------------------------------------------
@app.get("/api/settings")
async def get_settings():
    return {"settings": config.load_settings(), "api_key": config.api_key_status()}


@app.put("/api/settings")
async def put_settings(body: dict[str, Any]):
    return {"settings": config.save_settings(body)}


class ApiKeyBody(BaseModel):
    api_key: str


@app.put("/api/api-key")
async def put_api_key(body: ApiKeyBody):
    config.set_api_key(body.api_key)
    return config.api_key_status()


@app.delete("/api/api-key")
async def del_api_key():
    config.delete_api_key()
    return config.api_key_status()


async def _list_agents(c) -> list[dict[str, Any]]:
    out = []
    async for a in c.beta.agents.list(limit=100):
        out.append({"id": a.id, "name": a.name, "model": a.model})
        if len(out) >= 200:
            break
    return out


@app.get("/api/agents")
async def list_agents():
    try:
        return {"agents": await _list_agents(agent.client())}
    except Exception as exc:
        raise HTTPException(400, f"Agents konnten nicht geladen werden: {agent._err(exc)}")


@app.post("/api/agents/create-designer")
async def create_designer():
    """Legt den Voice-Agent-Designer im Projekt des aktuellen API-Keys an und wählt ihn aus."""
    template = json.loads((Path(__file__).parent / "agent_template.json").read_text(encoding="utf-8"))
    c = agent.client()
    try:
        a = await c.beta.agents.create(**template)
    except Exception as exc:
        raise HTTPException(400, f"Agent konnte nicht angelegt werden: {agent._err(exc)}")
    s = config.load_settings()
    s["agent_id"] = a.id
    config.save_settings(s)
    return {"ok": True, "agent": {"id": a.id, "name": a.name, "model": a.model}}


@app.post("/api/test-connection")
async def test_connection():
    s = config.load_settings()
    if not s["agent_id"]:
        raise HTTPException(400, "Noch kein Agent gewählt. „Voice-Agent-Designer in diesem Projekt anlegen“ "
                                 "oder „Agents dieses API-Keys laden“.")
    try:
        c = agent.client()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    try:
        a = await c.beta.agents.retrieve(s["agent_id"])
    except NotFoundError:
        try:
            found = await _list_agents(c)
        except Exception:
            found = []
        hint = ("Für diesen API-Key sichtbare Agents: " + ", ".join(f"{x['name'] or '(ohne Name)'} ({x['id']})" for x in found)
                if found else "Für diesen API-Key sind keine Agents sichtbar.")
        raise HTTPException(400, f"Agent {s['agent_id']} gehört nicht zum Projekt dieses API-Keys. {hint} "
                                 "Lösung: Key aus dem Projekt des Agenten verwenden oder unten einen Agent wählen.")
    except Exception as exc:
        raise HTTPException(400, f"Verbindung fehlgeschlagen: {agent._err(exc)}")
    tools = [getattr(t, "name", None) or getattr(t, "server_label", None) or t.type for t in a.tools or []]
    reasoning = getattr(a, "reasoning", None)
    effort = getattr(reasoning, "effort", None) if reasoning is not None else None
    return {"ok": True, "agent": {"id": a.id, "name": a.name, "model": a.model, "tools": tools,
                                  "reasoning_effort": effort or "Modell-Standard"}}


# --- Chats ------------------------------------------------------------------------
@app.get("/api/chats")
async def chats(q: str = ""):
    return {"chats": db.list_chats(q.strip())}


@app.post("/api/chats")
async def new_chat():
    return db.create_chat()


@app.get("/api/chats/{chat_id}")
async def chat_detail(chat_id: str):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat nicht gefunden")
    run = agent.RUNS.get(chat_id)
    return {"chat": chat, "messages": db.list_messages(chat_id), "running": bool(run and not run.done)}


class RenameBody(BaseModel):
    title: str


@app.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, body: RenameBody):
    title = body.title.strip()[:120]
    if not title:
        raise HTTPException(400, "Titel darf nicht leer sein")
    db.update_chat(chat_id, title=title)
    return db.get_chat(chat_id)


@app.delete("/api/chats/{chat_id}")
async def remove_chat(chat_id: str):
    run = agent.RUNS.get(chat_id)
    if run and not run.done:
        raise HTTPException(409, "Chat läuft noch. Bitte erst stoppen.")
    db.delete_chat(chat_id)
    return {"deleted": True}


# --- Uploads ----------------------------------------------------------------------
@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    data = await file.read(uploads.MAX_UPLOAD_BYTES + 1)
    return uploads.save_upload(file.filename or "datei", data)


@app.get("/api/uploads/{uid}")
async def get_upload(uid: str):
    meta, path = uploads.get_upload(uid)
    return FileResponse(path, media_type=meta["mime"], filename=meta["name"])


# --- Nachrichten / Turns ------------------------------------------------------------
class SendBody(BaseModel):
    text: str = ""
    attachments: list[str] = []


@app.post("/api/chats/{chat_id}/send")
async def send(chat_id: str, body: SendBody):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(404, "Chat nicht gefunden")
    existing = agent.RUNS.get(chat_id)
    if existing and not existing.done:
        raise HTTPException(409, "In diesem Chat läuft bereits eine Antwort.")
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, "Leere Nachricht")

    content: list[dict[str, Any]] = []
    attachment_meta = []
    for uid in body.attachments[:20]:
        meta, _ = uploads.get_upload(uid)
        content.append(await asyncio.to_thread(uploads.to_content, uid))
        attachment_meta.append(meta)
    if text:
        content.append({"type": "input_text", "text": text})

    db.add_message(chat_id, "user", text, attachments=attachment_meta)
    if chat["title"] == "Neuer Chat":
        title = (text or attachment_meta[0]["name"]).replace("\n", " ")[:60]
        db.update_chat(chat_id, title=title)

    run = agent.Run(chat_id=chat_id)
    agent.RUNS[chat_id] = run
    run.task = asyncio.create_task(agent.run_turn(run, content))
    return {"started": True}


@app.get("/api/chats/{chat_id}/events")
async def events(chat_id: str, request: Request):
    run = agent.RUNS.get(chat_id)
    if not run:
        raise HTTPException(404, "Kein laufender Turn")

    async def stream():
        while True:
            if await request.is_disconnected():
                return
            try:
                ev = await asyncio.wait_for(run.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev.get("type") == "done":
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/chats/{chat_id}/stop")
async def stop(chat_id: str):
    run = agent.RUNS.get(chat_id)
    if not run or run.done:
        return {"stopped": False}
    await agent.cancel_run(run)
    return {"stopped": True}


class ApprovalBody(BaseModel):
    approved: bool


@app.post("/api/chats/{chat_id}/approvals/{approval_id}")
async def approve(chat_id: str, approval_id: str, body: ApprovalBody):
    run = agent.RUNS.get(chat_id)
    fut = run.approvals.get(approval_id) if run else None
    if not fut or fut.done():
        raise HTTPException(404, "Freigabe nicht (mehr) offen")
    fut.set_result(body.approved)
    return {"ok": True}
