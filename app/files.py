"""Datei-Tools für den Agenten: nur innerhalb freigegebener Ordner.

Sicherheitsregeln:
- Zugriff nur über Ordnernamen aus den Einstellungen, nie über freie Pfade.
- Pfade werden aufgelöst (inkl. Symlinks) und müssen im freigegebenen Ordner bleiben.
- Secret-Dateien (.env, Schlüssel, Zertifikate, ...) werden nie gelesen oder geschrieben.
- Gelesene Inhalte werden nach Secrets durchsucht und geschwärzt.
- Schreiben nur in Ordnern mit Modus "readwrite" und nur nach Bestätigung in der App.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".next", ".cache"}
SECRET_NAME = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa.*|id_ed25519.*|.*secret.*|.*credential.*|\.netrc|\.npmrc|\.pypirc)$",
    re.I,
)
SECRET_KV = re.compile(
    r"(?im)^(\s*[\"']?[\w.-]*(api[_-]?key|token|secret|password|passwd|credential|auth)[\w.-]*[\"']?\s*[:=]\s*)(\S.*)$"
)
SECRET_TOKENS = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[\w-]{20,}\.[\w-]+\.[\w-]+)"
)
MAX_READ_CHARS = 60_000
MAX_WRITE_BYTES = 2_000_000
MAX_LIST_ENTRIES = 500
MAX_SEARCH_HITS = 100
TEXT_EXT_BLOCKLIST = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz", ".dmg", ".mp3", ".mp4",
                      ".mov", ".wav", ".sqlite", ".db", ".pyc", ".so", ".dylib", ".bin", ".exe", ".woff", ".woff2"}


class ToolError(Exception):
    """Fehler, der dem Agenten als verständliche Meldung zurückgegeben wird."""


def redact(text: str) -> str:
    text = SECRET_KV.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return SECRET_TOKENS.sub("[REDACTED]", text)


def _folder(settings: dict[str, Any], name: str) -> dict[str, Any]:
    for f in settings.get("folders", []):
        if f["name"] == name:
            return f
    available = [f["name"] for f in settings.get("folders", [])]
    raise ToolError(f"Unbekannter Ordner '{name}'. Freigegeben: {available or 'keine'}")


def resolve(settings: dict[str, Any], folder: str, rel: str = ".") -> tuple[Path, Path, dict[str, Any]]:
    f = _folder(settings, folder)
    root = Path(f["path"]).resolve()
    rel = (rel or ".").strip()
    if Path(rel).is_absolute():
        raise ToolError("Bitte einen relativen Pfad innerhalb des Ordners angeben.")
    target = (root / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise ToolError("Pfad liegt außerhalb des freigegebenen Ordners.")
    relposix = target.relative_to(root).as_posix()
    if any(part in SKIP_DIRS for part in Path(relposix).parts if part != "."):
        raise ToolError("Dieser Unterordner ist gesperrt.")
    if relposix != "." and SECRET_NAME.search(relposix):
        raise ToolError("Diese Datei sieht nach Zugangsdaten aus und ist gesperrt.")
    return root, target, f


def list_folders(settings: dict[str, Any]) -> dict[str, Any]:
    return {"folders": [{"name": f["name"], "mode": f["mode"]} for f in settings.get("folders", [])]}


def list_directory(settings: dict[str, Any], folder: str, path: str = ".", recursive: bool = False) -> dict[str, Any]:
    root, target, _ = resolve(settings, folder, path)
    if not target.is_dir():
        raise ToolError("Kein Verzeichnis.")
    entries: list[dict[str, Any]] = []
    truncated = False
    walker = os.walk(target) if recursive else [(str(target), *next(os.walk(target))[1:])]
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        base = Path(dirpath)
        for d in dirnames if not recursive else []:
            entries.append({"path": (base / d).relative_to(root).as_posix() + "/", "type": "dir"})
        for name in sorted(filenames):
            p = base / name
            rel = p.relative_to(root).as_posix()
            if SECRET_NAME.search(rel):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            entries.append({"path": rel, "type": "file", "size": size})
            if len(entries) >= MAX_LIST_ENTRIES:
                truncated = True
                break
        if truncated:
            break
    return {"folder": folder, "path": target.relative_to(root).as_posix(), "entries": entries, "truncated": truncated}


def read_file(settings: dict[str, Any], folder: str, path: str, offset: int = 0, max_chars: int = MAX_READ_CHARS) -> dict[str, Any]:
    _, target, _ = resolve(settings, folder, path)
    if not target.is_file():
        raise ToolError("Datei nicht gefunden.")
    if target.suffix.lower() in TEXT_EXT_BLOCKLIST:
        raise ToolError("Binärdatei, kann nicht als Text gelesen werden.")
    raw = target.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolError("Binärdatei, kann nicht als Text gelesen werden.")
    text = raw.decode("utf-8", errors="replace")
    offset = max(0, int(offset or 0))
    max_chars = max(1, min(int(max_chars or MAX_READ_CHARS), MAX_READ_CHARS))
    chunk = text[offset : offset + max_chars]
    return {
        "folder": folder,
        "path": path,
        "content": redact(chunk),
        "offset": offset,
        "total_chars": len(text),
        "has_more": offset + max_chars < len(text),
    }


def search_files(settings: dict[str, Any], folder: str, query: str, glob: str = "*") -> dict[str, Any]:
    root, target, _ = resolve(settings, folder, ".")
    if not query:
        raise ToolError("Suchbegriff fehlt.")
    needle = query.lower()
    hits: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob or "*"):
                continue
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if SECRET_NAME.search(rel) or p.suffix.lower() in TEXT_EXT_BLOCKLIST:
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if needle in line.lower():
                        hits.append({"path": rel, "line": lineno, "text": redact(line.strip())[:300]})
                        if len(hits) >= MAX_SEARCH_HITS:
                            return {"query": query, "hits": hits, "truncated": True}
            except OSError:
                continue
    return {"query": query, "hits": hits, "truncated": False}


def prepare_write(settings: dict[str, Any], folder: str, path: str, content: str, mode: str = "overwrite") -> dict[str, Any]:
    """Prüft einen Schreibwunsch und erstellt die Vorschau (Diff). Schreibt noch nichts."""
    root, target, f = resolve(settings, folder, path)
    if f["mode"] != "readwrite":
        raise ToolError(f"Ordner '{folder}' ist nur lesbar freigegeben.")
    if mode not in ("create", "overwrite", "append"):
        raise ToolError("mode muss create, overwrite oder append sein.")
    if target == root or target.is_dir():
        raise ToolError("Pfad ist ein Verzeichnis.")
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise ToolError("Inhalt zu groß (max. 2 MB).")
    exists = target.exists()
    if mode == "create" and exists:
        raise ToolError("Datei existiert bereits. mode='overwrite' verwenden, um sie zu ersetzen.")
    old = target.read_text(encoding="utf-8", errors="replace") if exists else ""
    new = old + content if mode == "append" else content
    diff = "".join(
        difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}", n=3)
    )
    return {
        "folder": folder,
        "path": target.relative_to(root).as_posix(),
        "abs_path": str(target),
        "mode": mode,
        "exists": exists,
        "diff": diff[:100_000] or "(keine inhaltliche Änderung)",
        "new_content": new,
        "old_mtime": target.stat().st_mtime if exists else None,
    }


def commit_write(plan: dict[str, Any]) -> dict[str, Any]:
    target = Path(plan["abs_path"])
    if plan["old_mtime"] is not None and target.exists() and target.stat().st_mtime != plan["old_mtime"]:
        raise ToolError("Datei wurde inzwischen geändert. Bitte neu lesen und erneut versuchen.")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.agentchat-tmp")
    tmp.write_text(plan["new_content"], encoding="utf-8")
    os.replace(tmp, target)
    return {"written": True, "folder": plan["folder"], "path": plan["path"], "bytes": len(plan["new_content"].encode("utf-8"))}


# --- inspect_project (vom gespeicherten Agenten definiert) -------------------
FOCUS_GLOBS: dict[str, list[str]] = {
    "prompts": ["**/prompts/**/*.md", "**/prompts/**/*.txt", "**/*prompt*.md", "**/*prompt*.txt"],
    "configuration": ["**/*.yaml", "**/*.yml", "**/*.toml", "**/config*.json", "**/livekit*.json"],
    "dependencies": ["requirements*.txt", "pyproject.toml", "package.json", "uv.lock"],
    "tests": ["**/test_*.py", "**/*_test.py", "**/tests/**/*.py", "**/*.test.ts"],
}


def inspect_project(settings: dict[str, Any], project_id: str, focus: str) -> dict[str, Any]:
    root, _, _ = resolve(settings, project_id, ".")
    if focus == "overview":
        top = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in root.iterdir()
            if p.name not in SKIP_DIRS and not SECRET_NAME.search(p.name)
        )
        return {"project_id": project_id, "top_level": top[:200]}
    if focus not in FOCUS_GLOBS:
        raise ToolError(f"Unbekannter focus '{focus}'.")
    files, total = [], 0
    seen: set[Path] = set()
    for pattern in FOCUS_GLOBS[focus]:
        for p in root.glob(pattern):
            if p in seen or not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts) or SECRET_NAME.search(rel.as_posix()):
                continue
            if not p.resolve().is_relative_to(root):
                continue
            seen.add(p)
            content = redact(p.read_bytes()[:40_000].decode("utf-8", errors="replace"))
            if total + len(content) > 800_000:
                return {"project_id": project_id, "focus": focus, "files": files, "result_truncated": True}
            total += len(content)
            files.append({"path": rel.as_posix(), "content": content})
    return {"project_id": project_id, "focus": focus, "files": files, "result_truncated": False}


# --- Tool-Definitionen für die Agents API ------------------------------------
def tool_definitions() -> list[dict[str, Any]]:
    def fn(name: str, description: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
        }

    folder = {"type": "string", "description": "Name eines freigegebenen Ordners (siehe list_folders)."}
    return [
        fn("list_folders", "Listet die Ordner auf dem Mac des Nutzers, die für dich freigegeben sind, mit Modus read oder readwrite.", {}, []),
        fn(
            "list_directory",
            "Listet Dateien und Unterordner in einem freigegebenen Ordner.",
            {
                "folder": folder,
                "path": {"type": "string", "description": "Relativer Pfad im Ordner, '.' für die Wurzel."},
                "recursive": {"type": "boolean", "description": "Unterordner mit auflisten."},
            },
            ["folder", "path", "recursive"],
        ),
        fn(
            "read_file",
            "Liest eine Textdatei aus einem freigegebenen Ordner. Secrets werden geschwärzt. Große Dateien in Abschnitten über offset lesen.",
            {
                "folder": folder,
                "path": {"type": "string", "description": "Relativer Dateipfad im Ordner."},
                "offset": {"type": "integer", "description": "Startposition in Zeichen, 0 für den Anfang."},
            },
            ["folder", "path", "offset"],
        ),
        fn(
            "search_files",
            "Sucht einen Text (ohne Groß-/Kleinschreibung) in Dateien eines freigegebenen Ordners.",
            {
                "folder": folder,
                "query": {"type": "string", "description": "Gesuchter Text."},
                "glob": {"type": "string", "description": "Dateinamenmuster, z. B. '*.py' oder '*'."},
            },
            ["folder", "query", "glob"],
        ),
        fn(
            "write_file",
            "Schreibt eine Textdatei in einen Ordner mit Modus readwrite. Der Nutzer sieht vorher einen Diff und muss bestätigen; "
            "lehnt er ab, kommt eine Ablehnung zurück. Vor dem Überschreiben die Datei mit read_file lesen.",
            {
                "folder": folder,
                "path": {"type": "string", "description": "Relativer Dateipfad im Ordner."},
                "content": {"type": "string", "description": "Neuer Inhalt (bei append: anzuhängender Text)."},
                "mode": {"type": "string", "enum": ["create", "overwrite", "append"]},
            },
            ["folder", "path", "content", "mode"],
        ),
        fn(
            "inspect_project",
            "Liest den Zustand eines freigegebenen lokalen Voice-Agent-Projekts nach Fokus. project_id ist der Ordnername. "
            "Gibt keine Secrets zurück.",
            {
                "project_id": {"type": "string", "description": "Name eines freigegebenen Ordners."},
                "focus": {"type": "string", "enum": ["overview", "prompts", "configuration", "dependencies", "tests"]},
            },
            ["project_id", "focus"],
        ),
    ]


LOCAL_TOOL_NAMES = {t["name"] for t in tool_definitions()}
