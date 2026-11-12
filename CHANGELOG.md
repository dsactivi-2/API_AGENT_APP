# Changelog

## 1.0.0 – 2026-11-12

Erste Version.

### Funktionen
- macOS-App (`Agent Chat.app`) mit lokalem Server und nativem Fenster (pywebview).
- Chat mit einem gespeicherten Agenten der OpenAI Agents API (Beta), Streaming-Antworten, Markdown, Kopier-Buttons, Stopp.
- Lokaler Verlauf (SQLite) mit Suche, Umbenennen, Löschen.
- Uploads per Button, Drag & Drop und Zwischenablage; Text/Code/PDF/DOCX als Text, Bilder als Bild.
- Einstellungen: API-Key im Schlüsselbund, Agent-ID, Agents des Keys laden, Voice-Agent-Designer im Projekt des Keys anlegen, Modell- und Reasoning-Override, Ausgabenlimit, Ordnerfreigaben.
- Lokale Datei-Tools für den Agenten: `list_folders`, `list_directory`, `read_file`, `search_files`, `write_file` (mit Freigabe), `inspect_project`.

### Behobene Fehler während der Entwicklung
- Neue Sessions ohne Environment verlangen die erste Nachricht beim Anlegen (`conversation-only sessions currently require initial input`).
- Fremde Keys aus `OPENAI_API_KEY` / Gateways aus `OPENAI_BASE_URL` wurden übernommen – jetzt ignoriert.
- Agents sind projektgebunden: klare Meldung bei „No agent found“ plus Auswahl bzw. Neuanlage im Projekt des Keys.
- Drop-Overlay blockierte den Senden-Button.
