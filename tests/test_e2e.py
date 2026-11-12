"""End-to-end-Test mit gefälschtem OpenAI-Client (keine echten API-Aufrufe).

Startet den echten Server, sendet Nachrichten, verarbeitet SSE-Events,
bestätigt bzw. lehnt Schreibzugriffe ab und prüft Dateisystem + Datenbank.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp())
os.environ["AGENTCHAT_DATA_DIR"] = str(TMP / "data")
os.environ["AGENTCHAT_TOKEN"] = "testtoken"
os.environ["OPENAI_API_KEY"] = "sk-test-not-real-0000000000"
os.environ["AGENTCHAT_ALLOW_ENV_KEY"] = "1"

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import agent, config  # noqa: E402
from app.server import app  # noqa: E402

PROJECT = TMP / "lina"
(PROJECT / "prompts").mkdir(parents=True)
(PROJECT / "prompts" / "main.md").write_text("Du bist Lina.\n")
(PROJECT / "config.yaml").write_text("voice: alloy\napi_key: sk-abcdefghijklmnopqrstuvwx\n")
(PROJECT / ".env").write_text("OPENAI_API_KEY=sk-real\n")

CREATED: list[dict] = []
STREAMED: list[str] = []
SCRIPT: list[str] = []  # Steuerung pro Turn: "approve-flow"


class FakeStream:
    def __init__(self, sid, input, tool_handlers):
        self.handlers = tool_handlers or {}
        self.input = input

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._gen()

    async def _call(self, name, args, cid):
        item = NS(type="function_call", id=cid, name=name, arguments=json.dumps(args), status="in_progress")
        yield NS(type="agent.session.turn.item.added", item=item)
        result = await self.handlers[name](args)
        self.results.append((name, result))
        yield NS(type="agent.session.turn.item.done", item=NS(id=cid, status="completed"))

    async def _gen(self):
        self.results = []
        yield NS(type="agent.session.turn.reasoning_summary_text.delta", delta="Ich prüfe die Ordner.")
        async for ev in self._call("list_folders", {}, "c1"):
            yield ev
        async for ev in self._call("read_file", {"folder": "lina", "path": "config.yaml", "offset": 0}, "c2"):
            yield ev
        async for ev in self._call("read_file", {"folder": "lina", "path": ".env", "offset": 0}, "c3"):
            yield ev
        async for ev in self._call("read_file", {"folder": "lina", "path": "../../etc/passwd", "offset": 0}, "c4"):
            yield ev
        async for ev in self._call("write_file", {"folder": "lina", "path": "prompts/main.md",
                                                  "content": "Du bist Lina, freundlich.\n", "mode": "overwrite"}, "c5"):
            yield ev
        yield NS(type="agent.session.turn.item.added",
                 item=NS(type="mcp_call", id="m1", server_label="openai_docs", name="search", status="in_progress"))
        yield NS(type="agent.session.turn.item.done", item=NS(id="m1", status="completed"))
        LAST_RESULTS[:] = self.results
        for part in ["**Fertig.** ", "Prompt ", "aktualisiert."]:
            yield NS(type="agent.session.turn.output_text.delta", delta=part)


LAST_RESULTS: list = []


class FakeSessions:
    events = NS(create=None)

    def __init__(self):
        async def ev_create(sid, events):
            return None
        self.events = NS(create=ev_create)

    async def create(self, **kw):
        # Wie die echte API: ohne Environment ist eine initiale Eingabe Pflicht
        if not kw.get("input"):
            raise RuntimeError("conversation-only sessions currently require initial input")
        assert kw.get("stream") is True
        CREATED.append(kw)
        sid = f"sess_{len(CREATED)}"
        inner = FakeStream(sid, kw["input"], kw.get("tool_handlers"))

        class CreateStream:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False

            def __aiter__(self_):
                return self_.gen()

            async def gen(self_):
                yield NS(type="agent.session.created", session=NS(id=sid))
                yield NS(type="agent.session.turn.created", turn_id="t1", session_id=sid, turn=NS(subagent_id=None))
                async for ev in inner._gen():
                    ev.session_id = sid
                    yield ev
                yield NS(type="agent.session.turn.completed", turn_id="t1", session_id=sid, turn=NS(subagent_id=None))
                yield NS(type="agent.session.idle", session_id=sid)
                yield NS(type="agent.session.turn.output_text.delta", delta="NACH-IDLE-DARF-NICHT-ERSCHEINEN", session_id=sid)

        return CreateStream()

    async def retrieve(self, sid):
        return NS(status="idle")

    def stream(self, sid, input, tool_handlers=None):
        STREAMED.append(sid)
        return FakeStream(sid, input, tool_handlers)


class FakeClient:
    def __init__(self):
        saved_tools = [
            NS(model_dump=lambda **k: {"type": "mcp", "server_label": "openai_docs", "transport": {"type": "http", "server_url": "https://developers.openai.com/mcp"}}),
            NS(model_dump=lambda **k: {"type": "function", "name": "inspect_project", "parameters": {}}),
        ]

        async def retrieve(agent_id):
            return NS(id=agent_id, name="Test", model="gpt-x", instructions="BASIS", tools=saved_tools)

        self.beta = NS(agents=NS(retrieve=retrieve, sessions=FakeSessions()))


agent.client = lambda: FakeClient()


def serve():
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")).run()


threading.Thread(target=serve, daemon=True).start()
time.sleep(1.5)
H = {"X-AgentChat-Token": "testtoken"}
B = "http://127.0.0.1:8765"
c = httpx.Client(base_url=B, headers=H, timeout=30)


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)


FAILED: list[str] = []

# Auth
check(httpx.get(B + "/api/chats").status_code == 401, "API ohne Token wird abgelehnt")
check(httpx.get(B + "/").status_code == 200, "Startseite lädt ohne Token")

# Einstellungen
r = c.put("/api/settings", json={"agent_id": "agent_test", "folders": [{"name": "../x", "path": str(PROJECT), "mode": "read"}]})
check(r.status_code == 400, f"Ungültiger Ordnername abgelehnt ({r.json().get('detail')})")
r = c.put("/api/settings", json={"agent_id": "agent_test", "folders": [{"name": "home", "path": str(Path.home()), "mode": "read"}]})
check(r.status_code == 400, "Home-Ordner als Freigabe abgelehnt")
r = c.put("/api/settings", json={"agent_id": "agent_test", "show_reasoning": True, "local_tools": True,
                                 "spend_limit_cents": 500,
                                 "folders": [{"name": "lina", "path": str(PROJECT), "mode": "readwrite"}]})
check(r.status_code == 200, "Einstellungen gespeichert")

# Upload
up = c.post("/api/uploads", files={"file": ("notiz.txt", b"Testinhalt Upload", "text/plain")}).json()
check(len(up["id"]) == 32, "Upload gespeichert")


def run_turn(text, decision, chat=None):
    chat = chat or c.post("/api/chats").json()
    c.post(f"/api/chats/{chat['id']}/send", json={"text": text, "attachments": [up["id"]]}).raise_for_status()
    events = []
    with c.stream("GET", f"/api/chats/{chat['id']}/events") as s:
        for line in s.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            events.append(ev)
            if ev["type"] == "approval":
                c.post(f"/api/chats/{chat['id']}/approvals/{ev['id']}", json={"approved": decision}).raise_for_status()
            if ev["type"] == "done":
                break
    return chat, events


# Turn 1: Schreiben erlauben
chat, events = run_turn("Bitte Prompt verbessern", True)
types = [e["type"] for e in events]
check("approval" in types and "approval_resolved" in types, "Freigabe-Dialog kam und wurde aufgelöst")
check("".join(e["delta"] for e in events if e["type"] == "text") == "**Fertig.** Prompt aktualisiert.", "Text-Stream vollständig")
check(any(e["type"] == "reasoning" for e in events), "Reasoning gestreamt")
check(any(e["type"] == "activity" and e["kind"] == "mcp" for e in events), "MCP-Aktivität angezeigt")
check((PROJECT / "prompts" / "main.md").read_text() == "Du bist Lina, freundlich.\n", "Datei nach Freigabe geschrieben")
res = dict((n, r) for n, r in LAST_RESULTS if n != "read_file")
reads = [r for n, r in LAST_RESULTS if n == "read_file"]
check("[REDACTED]" in reads[0]["content"] and "sk-abc" not in reads[0]["content"], "API-Key in config.yaml geschwärzt")
check("gesperrt" in reads[1].get("error", ""), ".env wird verweigert")
check("außerhalb" in reads[2].get("error", ""), "Pfad-Ausbruch ../../ verweigert")
check(res["list_folders"] == {"folders": [{"name": "lina", "mode": "readwrite"}]}, "list_folders korrekt")

kw = CREATED[0]
names = [t.get("name") or t.get("server_label") for t in kw["agent"]["tools"]]
check(kw["environment"] == {"type": "none"} and kw["agent_id"] == "agent_test", "Session mit environment none + agent_id")
check(kw["spend_control"] == {"limit": 500}, "Ausgabenlimit übergeben")
check("openai_docs" in names and names.count("inspect_project") == 1 and "write_file" in names, "Gespeicherte Tools behalten, lokale ergänzt, keine Duplikate")
check(kw["agent"]["instructions"].startswith("BASIS") and "write_file" in kw["agent"]["instructions"], "Instruktionen ergänzt statt ersetzt")

detail = c.get(f"/api/chats/{chat['id']}").json()
roles = [m["role"] for m in detail["messages"]]
check(roles[:2] == ["user", "assistant"], "Verlauf gespeichert (user + assistant)")
check(detail["messages"][0]["attachments"][0]["name"] == "notiz.txt", "Anhang im Verlauf")
check(detail["chat"]["title"] == "Bitte Prompt verbessern", "Chat-Titel aus erster Nachricht")
check(detail["chat"]["session_id"] == "sess_1", "Session-ID am Chat gespeichert")

check("NACH-IDLE" not in "".join(e.get("delta", "") for e in events if e["type"] == "text"), "Erstellungs-Stream endet nach dem ersten Turn")
check(CREATED[0]["input"][0]["content"][-1]["text"] == "Bitte Prompt verbessern", "Erste Nachricht beim Anlegen der Session mitgeschickt")

# Folgenachricht im selben Chat -> bestehende Session per sessions.stream
(PROJECT / "prompts" / "main.md").write_text("X\n")
chat_b, events_b = run_turn("Noch eine Frage", True, chat=chat)
check(len(CREATED) == 1 and STREAMED == ["sess_1"], "Folgenachricht nutzt bestehende Session")
check([m["role"] for m in c.get(f"/api/chats/{chat['id']}").json()["messages"]] == ["user", "assistant", "user", "assistant"], "Verlauf mit 2 Runden gespeichert")

# Turn 2: Schreiben ablehnen
(PROJECT / "prompts" / "main.md").write_text("ORIGINAL\n")
chat2, events2 = run_turn("Nochmal", False)
check((PROJECT / "prompts" / "main.md").read_text() == "ORIGINAL\n", "Abgelehnt: Datei unverändert")
w = dict(LAST_RESULTS)["write_file"]
check(w.get("written") is False and "abgelehnt" in w.get("error", ""), "Agent bekommt Ablehnung zurück")

# Suche + Löschen
check(any(x["id"] == chat["id"] for x in c.get("/api/chats", params={"q": "Prompt verbessern"}).json()["chats"]), "Suche findet Chat")
check(c.delete(f"/api/chats/{chat2['id']}").status_code == 200, "Chat gelöscht")
check(c.get(f"/api/chats/{chat2['id']}").status_code == 404, "Gelöschter Chat weg")

# Fremder Key aus der Umgebung darf nicht verwendet werden
from app import config as _cfg
os.environ["AGENTCHAT_ALLOW_ENV_KEY"] = "0"; os.environ["OPENAI_API_KEY"] = "mnfst_fake"
check(_cfg.get_api_key() is None, "OPENAI_API_KEY aus Umgebung wird ohne Freigabe ignoriert")
import importlib, app.agent as _ag
os.environ["AGENTCHAT_ALLOW_ENV_KEY"] = "1"
try:
    importlib.reload(_ag).client(); check(False, "Nicht-sk-Key wird abgelehnt")
except RuntimeError as e:
    check("kein OpenAI-API-Key" in str(e), "Nicht-sk-Key wird abgelehnt")

print("\nERGEBNIS:", "ALLE TESTS BESTANDEN" if not FAILED else f"{len(FAILED)} FEHLGESCHLAGEN: {FAILED}")
sys.exit(1 if FAILED else 0)
