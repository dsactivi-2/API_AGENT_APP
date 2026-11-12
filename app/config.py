"""Pfade, Einstellungen und API-Key-Speicher (macOS-Schlüsselbund)."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

APP_NAME = "AgentChat"
KEYRING_SERVICE = "AgentChat"
KEYRING_USER = "openai_api_key"


def data_dir() -> Path:
    override = os.environ.get("AGENTCHAT_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path.home() / ".local" / "share" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    (base / "uploads").mkdir(exist_ok=True)
    return base


DEFAULT_SETTINGS: dict[str, Any] = {
    "agent_id": "",                  # leer = noch kein Agent gewählt (Einstellungen → anlegen oder auswählen)
    "model_override": "",            # leer = Modell des gespeicherten Agenten
    "reasoning_effort": "",          # leer = wie gespeicherter Agent, sonst low/medium/high/xhigh
    "local_tools": True,             # Datei-Tools für freigegebene Ordner anbieten
    "show_reasoning": True,          # Reasoning-Zusammenfassungen anzeigen
    "spend_limit_cents": None,       # Ausgabenlimit pro Session in US-Cent, None = unbegrenzt
    "folders": [],                   # [{"name": "lina", "path": "/Users/…/lina", "mode": "read"|"readwrite"}]
    "base_url": "https://api.openai.com/v1",  # fest, Umgebungsvariable OPENAI_BASE_URL wird ignoriert
}

_lock = threading.Lock()


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    with _lock:
        path = settings_path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        return {**DEFAULT_SETTINGS, **data}


def validate_settings(new: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    agent_id = str(new.get("agent_id", "")).strip()
    if agent_id and not agent_id.startswith("agent_"):
        raise ValueError("agent_id muss mit 'agent_' beginnen.")
    out["agent_id"] = agent_id
    out["model_override"] = str(new.get("model_override") or "").strip()
    effort = str(new.get("reasoning_effort") or "").strip()
    if effort not in ("", "low", "medium", "high", "xhigh"):
        raise ValueError(f"Ungültiger Reasoning-Wert '{effort}'.")
    out["reasoning_effort"] = effort
    base_url = str(new.get("base_url") or DEFAULT_SETTINGS["base_url"]).strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise ValueError("API-Adresse muss mit https:// beginnen.")
    out["base_url"] = base_url
    out["local_tools"] = bool(new.get("local_tools", True))
    out["show_reasoning"] = bool(new.get("show_reasoning", True))

    limit = new.get("spend_limit_cents")
    if limit in (None, "", 0, "0"):
        out["spend_limit_cents"] = None
    else:
        limit = int(limit)
        if limit <= 0:
            raise ValueError("Ausgabenlimit muss positiv sein.")
        out["spend_limit_cents"] = limit

    folders, names = [], set()
    for f in new.get("folders") or []:
        name = str(f.get("name", "")).strip()
        raw_path = str(f.get("path", "")).strip()
        mode = f.get("mode", "read")
        if not name or not raw_path:
            raise ValueError("Jeder Ordner braucht Name und Pfad.")
        if not all(c.isalnum() or c in "-_" for c in name):
            raise ValueError(f"Ordnername '{name}': nur Buchstaben, Ziffern, - und _.")
        if name in names:
            raise ValueError(f"Ordnername '{name}' ist doppelt.")
        if mode not in ("read", "readwrite"):
            raise ValueError(f"Ungültiger Modus '{mode}'.")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Ordner existiert nicht: {path}")
        if path == Path(path.anchor) or path == Path.home():
            raise ValueError(f"'{path}' ist zu weit gefasst. Bitte einen Projektordner wählen.")
        names.add(name)
        folders.append({"name": name, "path": str(path), "mode": mode})
    out["folders"] = folders
    return out


def save_settings(new: dict[str, Any]) -> dict[str, Any]:
    clean = validate_settings(new)
    with _lock:
        tmp = settings_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(settings_path())
    return clean


# --- API-Key -----------------------------------------------------------------
def _keyring():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def get_api_key() -> str | None:
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            if value:
                return value
        except Exception:
            pass
    # Umgebungsvariablen nur, wenn ausdrücklich erlaubt (z. B. für Tests).
    # Sonst würde z. B. ein Gateway-Key aus der Shell (OPENAI_API_KEY) still übernommen.
    if os.environ.get("AGENTCHAT_ALLOW_ENV_KEY") == "1":
        return os.environ.get("OPENAI_API_KEY") or None
    return None


def set_api_key(value: str) -> None:
    value = value.strip()
    if not value.startswith("sk-"):
        raise ValueError("Der Key sieht nicht nach einem OpenAI-API-Key aus (beginnt nicht mit 'sk-').")
    kr = _keyring()
    if kr is None:
        raise RuntimeError("Schlüsselbund nicht verfügbar. Bitte OPENAI_API_KEY als Umgebungsvariable setzen.")
    kr.set_password(KEYRING_SERVICE, KEYRING_USER, value)


def delete_api_key() -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass


def api_key_status() -> dict[str, Any]:
    key = get_api_key()
    if not key:
        return {"set": False}
    return {"set": True, "masked": f"{key[:7]}…{key[-4:]}", "looks_valid": key.startswith("sk-")}
