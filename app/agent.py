"""Verbindung zur OpenAI Agents API: Sessions, Turns, lokale Tool-Handler, Freigaben.

Ablauf pro Chat:
  1. Beim ersten Turn wird eine Session für den gespeicherten Agenten angelegt
     (environment = none; Web-Suche und MCP laufen bei OpenAI, Datei-Tools lokal).
  2. Jeder Turn läuft über sessions.stream(...). Die SDK-Helfer rufen die lokalen
     Tool-Handler auf und senden deren Ergebnisse automatisch zurück.
  3. Ereignisse gehen über eine Queue an die Oberfläche (Server-Sent Events).
  4. Schreibzugriffe warten auf eine Bestätigung in der App (Future pro Freigabe).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI, BadRequestError, NotFoundError

from . import db, files
from .config import get_api_key, load_settings

log = logging.getLogger("agentchat")

APPROVAL_TIMEOUT_S = 15 * 60
TOOL_NOTE = (
    "\n\n## Lokale Datei-Tools (Chat-App auf dem Mac des Nutzers)\n"
    "Du hast Zugriff auf Ordner, die der Nutzer freigegeben hat: list_folders, list_directory, read_file, "
    "search_files, write_file, inspect_project. Rufe zuerst list_folders auf. write_file zeigt dem Nutzer "
    "einen Diff; er muss bestätigen. Lehnt er ab, akzeptiere das und frage nach, statt es erneut zu versuchen. "
    "Lies eine Datei vor dem Überschreiben. Löschen ist nicht möglich."
)


@dataclass
class Run:
    chat_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    session_id: str | None = None
    approvals: dict[str, asyncio.Future] = field(default_factory=dict)
    done: bool = False


RUNS: dict[str, Run] = {}


def client() -> AsyncOpenAI:
    key = get_api_key()
    if not key:
        raise RuntimeError("Kein OpenAI-API-Key gesetzt. Bitte in den Einstellungen eintragen.")
    if not key.startswith("sk-"):
        raise RuntimeError("Der gespeicherte Key ist kein OpenAI-API-Key (muss mit 'sk-' beginnen).")
    # base_url explizit setzen: sonst würde OPENAI_BASE_URL aus der Umgebung greifen.
    return AsyncOpenAI(api_key=key, base_url=load_settings()["base_url"])


# --- Session ------------------------------------------------------------------
async def _session_agent_config(c: AsyncOpenAI, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Overrides für die Session: lokale Tools anhängen, optional Modell wechseln."""
    if not settings["local_tools"] and not settings["model_override"] and not settings.get("reasoning_effort"):
        return None
    saved = await c.beta.agents.retrieve(settings["agent_id"])
    cfg: dict[str, Any] = {}
    if settings["model_override"]:
        cfg["model"] = settings["model_override"]
    if settings.get("reasoning_effort"):
        # Ein übergebenes Objekt ersetzt die ganze Reasoning-Konfiguration -> summary mitgeben.
        cfg["reasoning"] = {"effort": settings["reasoning_effort"], "summary": "auto"}
    if settings["local_tools"]:
        kept = []
        for t in saved.tools or []:
            d = t.model_dump(mode="json", exclude_none=True)
            if d.get("type") == "function" and d.get("name") in files.LOCAL_TOOL_NAMES:
                continue  # durch die lokale Version ersetzt
            kept.append(d)
        cfg["tools"] = kept + files.tool_definitions()
        cfg["instructions"] = (saved.instructions or "") + TOOL_NOTE
    return cfg


async def _session_params(c: AsyncOpenAI, settings: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "environment": {"type": "none"},
        "agent_id": settings["agent_id"],
        "metadata": {"client": "agentchat-mac"},
    }
    if settings["spend_limit_cents"]:
        params["spend_control"] = {"limit": settings["spend_limit_cents"]}
    agent_cfg = await _session_agent_config(c, settings)
    if agent_cfg:
        params["agent"] = agent_cfg
    return params


async def usable_session(c: AsyncOpenAI, chat: dict[str, Any], emit) -> str | None:
    """Gibt die bestehende Session zurück, wenn sie weiterverwendet werden kann, sonst None."""
    sid = chat.get("session_id")
    if not sid:
        return None
    try:
        s = await c.beta.agents.sessions.retrieve(sid)
        if s.status == "idle":
            return sid
        if s.status in ("in_progress", "requires_action"):
            await c.beta.agents.sessions.events.create(sid, events=[{"type": "agent.session.input.cancel"}])
            for _ in range(20):
                await asyncio.sleep(0.5)
                s = await c.beta.agents.sessions.retrieve(sid)
                if s.status == "idle":
                    return sid
        await emit({"type": "notice", "text": "Die bisherige Session war nicht mehr nutzbar. Es wird eine neue "
                                              "gestartet; der Agent kennt den früheren Verlauf nicht."})
    except NotFoundError:
        await emit({"type": "notice", "text": "Die bisherige Session existiert nicht mehr. Es wird eine neue gestartet; "
                                              "der Agent kennt den früheren Verlauf nicht."})
    return None


class _TurnEnd:
    """Erkennt das Ende des ersten (Root-)Turns in einem Erstellungs-Stream."""

    def __init__(self) -> None:
        self.turn_id: str | None = None
        self.ended = False

    def done(self, event: Any) -> bool:
        t = event.type
        turn = getattr(event, "turn", None)
        if t == "agent.session.turn.created" and turn is not None and turn.subagent_id is None and self.turn_id is None:
            self.turn_id = event.turn_id
        if t in ("agent.session.turn.completed", "agent.session.turn.failed", "agent.session.turn.cancelled"):
            if self.turn_id is None or event.turn_id == self.turn_id:
                self.ended = True
        return t == "agent.session.failed" or (t == "agent.session.idle" and self.ended)


# --- Tool-Handler ---------------------------------------------------------------
def make_handlers(run: Run, settings: dict[str, Any]) -> dict[str, Any]:
    async def emit(ev: dict[str, Any]) -> None:
        await run.queue.put(ev)

    def safe(fn):
        async def wrapper(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return await fn(args)
            except files.ToolError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # Details ins Log, nicht an das Modell
                log.exception("Tool-Fehler")
                return {"error": f"Interner Fehler im lokalen Tool ({type(exc).__name__})."}
        return wrapper

    @safe
    async def list_folders(args):
        return files.list_folders(settings)

    @safe
    async def list_directory(args):
        return await asyncio.to_thread(files.list_directory, settings, args["folder"], args.get("path", "."),
                                       bool(args.get("recursive")))

    @safe
    async def read_file(args):
        return await asyncio.to_thread(files.read_file, settings, args["folder"], args["path"], args.get("offset", 0))

    @safe
    async def search_files(args):
        return await asyncio.to_thread(files.search_files, settings, args["folder"], args["query"], args.get("glob", "*"))

    @safe
    async def inspect_project(args):
        return await asyncio.to_thread(files.inspect_project, settings, args["project_id"], args["focus"])

    @safe
    async def write_file(args):
        plan = await asyncio.to_thread(files.prepare_write, settings, args["folder"], args["path"],
                                       args["content"], args.get("mode", "overwrite"))
        approval_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        run.approvals[approval_id] = fut
        await emit({"type": "approval", "id": approval_id, "folder": plan["folder"], "path": plan["path"],
                    "mode": plan["mode"], "exists": plan["exists"], "diff": plan["diff"]})
        try:
            approved = await asyncio.wait_for(fut, APPROVAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            approved = False
        finally:
            run.approvals.pop(approval_id, None)
        await emit({"type": "approval_resolved", "id": approval_id, "approved": approved})
        if not approved:
            return {"written": False, "error": "Der Nutzer hat das Schreiben abgelehnt."}
        return await asyncio.to_thread(files.commit_write, plan)

    return {
        "list_folders": list_folders,
        "list_directory": list_directory,
        "read_file": read_file,
        "search_files": search_files,
        "inspect_project": inspect_project,
        "write_file": write_file,
    }


# --- Turn -----------------------------------------------------------------------
def _err(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body["error"] if isinstance(body.get("error"), dict) else body
        msg = inner.get("message")
        if msg:
            return str(msg)
    return str(exc) or type(exc).__name__


def _item_label(item: Any) -> dict[str, Any] | None:
    kind = getattr(item, "type", None)
    if kind == "function_call":
        args = getattr(item, "arguments", None)
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        return {"kind": "tool", "label": item.name, "detail": (args or "")[:300]}
    if kind == "mcp_call":
        return {"kind": "mcp", "label": f"{item.server_label} · {item.name}", "detail": ""}
    if kind == "web_search_call":
        action = getattr(item, "action", None)
        query = getattr(action, "query", None) if action is not None else None
        return {"kind": "web", "label": "Websuche", "detail": query or ""}
    if kind == "command_execution":
        return {"kind": "cmd", "label": "Befehl", "detail": str(getattr(item, "command", ""))[:300]}
    if kind and kind.endswith("subagent_call"):
        return {"kind": "subagent", "label": kind.replace("_", " "), "detail": ""}
    return None


async def run_turn(run: Run, user_content: list[dict[str, Any]]) -> None:
    settings = load_settings()
    text_parts: list[str] = []
    activity: list[dict[str, Any]] = []
    error: str | None = None

    async def emit(ev: dict[str, Any]) -> None:
        await run.queue.put(ev)

    async def handle(event: Any) -> None:
        nonlocal error
        t = event.type
        if t == "agent.session.turn.output_text.delta":
            text_parts.append(event.delta)
            await emit({"type": "text", "delta": event.delta})
        elif t == "agent.session.turn.reasoning_summary_text.delta":
            if settings["show_reasoning"]:
                await emit({"type": "reasoning", "delta": event.delta})
        elif t == "agent.session.turn.item.added":
            label = _item_label(event.item)
            if label:
                entry = {"id": getattr(event.item, "id", None), **label, "status": "running"}
                activity.append(entry)
                await emit({"type": "activity", **entry})
        elif t == "agent.session.turn.item.done":
            item_id = getattr(event.item, "id", None)
            status = str(getattr(event.item, "status", "completed") or "completed")
            for entry in activity:
                if entry["id"] == item_id:
                    entry["status"] = status
                    await emit({"type": "activity_done", "id": item_id, "status": status})
        elif t == "agent.session.turn.failed":
            if event.turn.subagent_id is None:
                err = event.turn.error
                error = getattr(err, "message", None) or "Der Turn ist fehlgeschlagen."
        elif t == "agent.session.turn.cancelled":
            if event.turn.subagent_id is None:
                error = "Abgebrochen."
        elif t == "agent.session.failed":
            error = "Die Session ist fehlgeschlagen."
        elif t == "error":
            error = event.error.message

    try:
        if not settings["agent_id"]:
            raise RuntimeError("Noch kein Agent gewählt. Einstellungen → „Voice-Agent-Designer in diesem Projekt anlegen“ "
                               "oder „Agents dieses API-Keys laden“.")
        c = client()
        chat = db.get_chat(run.chat_id)
        handlers = make_handlers(run, settings) if settings["local_tools"] else None
        message = {"role": "user", "content": user_content}
        sid = await usable_session(c, chat, emit)

        if sid:
            run.session_id = sid
            async with c.beta.agents.sessions.stream(sid, input=[message], tool_handlers=handlers) as stream:
                async for event in stream:
                    await handle(event)
        else:
            # Neue Session: Die erste Nachricht muss direkt beim Anlegen mitgeschickt werden
            # (bei environment "none" verlangt die API eine initiale Eingabe).
            params = await _session_params(c, settings)
            try:
                stream = await c.beta.agents.sessions.create(**params, input=[message], stream=True,
                                                            tool_handlers=handlers)
            except BadRequestError as exc:
                msg = _err(exc).lower()
                if "agent" not in params or not any(k in msg for k in ("tool", "function", "instruction")):
                    raise
                log.warning("Session mit lokalen Tools abgelehnt, versuche ohne: %s", exc)
                await emit({"type": "notice", "text": f"Lokale Tools konnten nicht aktiviert werden ({_err(exc)}). "
                                                      "Der Chat läuft ohne Dateizugriff weiter."})
                params.pop("agent")
                stream = await c.beta.agents.sessions.create(**params, input=[message], stream=True)
            end = _TurnEnd()
            async with stream:
                async for event in stream:
                    sid_ev = getattr(event, "session_id", None) or getattr(getattr(event, "session", None), "id", None)
                    if sid_ev and run.session_id != sid_ev:
                        run.session_id = sid_ev
                        db.update_chat(run.chat_id, session_id=sid_ev)
                    await handle(event)
                    if end.done(event):
                        break
    except asyncio.CancelledError:
        error = "Abgebrochen."
    except Exception as exc:  # API-, Netzwerk- und Konfigurationsfehler
        log.exception("Turn fehlgeschlagen")
        error = _err(exc)
    finally:
        for fut in run.approvals.values():
            if not fut.done():
                fut.set_result(False)
        content = "".join(text_parts)
        if content or activity or error:
            msg = db.add_message(run.chat_id, "assistant", content, activity=activity + (
                [{"kind": "error", "label": "Fehler", "detail": error, "status": "failed"}] if error else []))
            await emit({"type": "saved", "message_id": msg["id"]})
        if error:
            await emit({"type": "error", "text": error})
        await emit({"type": "done"})
        run.done = True


async def cancel_run(run: Run) -> None:
    if run.session_id:
        try:
            await client().beta.agents.sessions.events.create(
                run.session_id, events=[{"type": "agent.session.input.cancel"}]
            )
        except Exception:
            log.exception("Abbrechen fehlgeschlagen")
    if run.task and not run.task.done():
        run.task.cancel()
