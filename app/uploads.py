"""Uploads speichern und in Eingabeinhalte für den Agenten umwandeln.

Die Agents API nimmt als Nachrichteninhalt nur `input_text` und `input_image` an.
Text, Code, PDF und DOCX werden deshalb lokal in Text umgewandelt; Bilder gehen als Data-URL.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .config import data_dir

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024


def upload_dir() -> Path:
    return data_dir() / "uploads"


def _safe_name(name: str) -> str:
    name = Path(name or "datei").name
    return re.sub(r"[^\w.\- ]", "_", name)[:120] or "datei"


def save_upload(filename: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Datei ist größer als 25 MB.")
    uid = uuid.uuid4().hex
    name = _safe_name(filename)
    folder = upload_dir() / uid
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    meta = {"id": uid, "name": name, "size": len(data), "mime": mime}
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def get_upload(uid: str) -> tuple[dict[str, Any], Path]:
    if not re.fullmatch(r"[0-9a-f]{32}", uid):
        raise ValueError("Ungültige Upload-ID.")
    folder = upload_dir() / uid
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return meta, folder / meta["name"]


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        parts.append(f"--- Seite {i} ---\n{page.extract_text() or ''}")
        if sum(len(p) for p in parts) > MAX_TEXT_CHARS:
            break
    return "\n".join(parts)


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def to_content(uid: str) -> dict[str, Any]:
    """Wandelt einen Upload in ein InputContent-Objekt um."""
    meta, path = get_upload(uid)
    mime, name = meta["mime"], meta["name"]
    if mime in IMAGE_TYPES:
        if meta["size"] > MAX_IMAGE_BYTES:
            raise ValueError(f"Bild '{name}' ist zu groß (max. 15 MB).")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "input_image", "image_url": f"data:{mime};base64,{b64}"}

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, kind = _pdf_text(path), "PDF, Text extrahiert"
    elif suffix == ".docx":
        text, kind = _docx_text(path), "Word, Text extrahiert"
    else:
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            raise ValueError(f"'{name}' ist eine Binärdatei und kann nicht gelesen werden.")
        text, kind = raw.decode("utf-8", errors="replace"), "Text"
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]
    note = " (gekürzt)" if truncated else ""
    return {"type": "input_text", "text": f"[Angehängte Datei: {name} – {kind}{note}]\n```\n{text}\n```"}
