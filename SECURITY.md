# Sicherheit

Agent Chat gibt einem Sprachmodell Zugriff auf Dateien auf deinem Mac. Dieses Dokument beschreibt, was der Agent darf, was nicht, und wo die Grenzen liegen.

## Was der Agent darf

| Aktion | Bedingung |
|---|---|
| Ordner auflisten, Dateien lesen, Text suchen | Nur in Ordnern, die du in den Einstellungen freigegeben hast |
| Dateien schreiben (anlegen, überschreiben, anhängen) | Nur in Ordnern mit Modus „Lesen + Schreiben“ **und** nur nach deinem Klick auf „Schreiben erlauben“, mit Diff-Vorschau |
| Web-Suche, MCP-Server | Laufen bei OpenAI, gemäß Konfiguration des Agenten |

## Was der Agent nicht darf

- Dateien löschen, verschieben oder umbenennen (es gibt kein solches Tool).
- Befehle oder Code auf deinem Mac ausführen (Session läuft mit `environment: none`).
- Pfade außerhalb freigegebener Ordner lesen oder schreiben (`../`-Ausbrüche und Symlinks nach außen werden abgewiesen).
- Secret-Dateien lesen oder schreiben: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `.netrc`, `.npmrc`, `.pypirc`, `*secret*`, `*credential*`.
- Das Home-Verzeichnis oder ein Laufwerk als Ganzes freigeben (wird von der App abgelehnt).

## Schutzmechanismen

- **Schwärzung:** Gelesene Inhalte werden vor der Übergabe an das Modell nach typischen Secrets durchsucht (`api_key: …`, `token=…`, `sk-…`, `ghp_…`, `github_pat_…`, `AKIA…`, `xox…-`, JWTs) und durch `[REDACTED]` ersetzt. Das ist eine Heuristik, keine Garantie.
- **Schreibschutz bei parallelen Änderungen:** Hat sich eine Datei zwischen Vorschau und Freigabe geändert, wird nicht geschrieben.
- **API-Key:** liegt im macOS-Schlüsselbund, nie in Dateien oder Logs. Umgebungsvariablen (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) werden bewusst ignoriert.
- **Lokaler Server:** lauscht nur auf `127.0.0.1`, zufälliger Port, zufälliges Sitzungstoken pro Start. Das Token wird als eigener Header gesendet; fremde Webseiten im Browser können die API deshalb nicht aufrufen (CORS-Preflight wird nie erlaubt).
- **Fehlermeldungen an das Modell** enthalten keine internen Exception-Texte.

## Bekannte Grenzen

- Die Schwärzung erkennt nur bekannte Muster. Gib keine Ordner frei, in denen ungeschützte Zugangsdaten in beliebigen Dateien liegen.
- Inhalte freigegebener Ordner werden an OpenAI übertragen. Freigeben nur, was du an OpenAI senden darfst (Datenschutz, Kundendaten, NDAs).
- Prompt Injection: Liest der Agent eine Datei oder Webseite mit eingeschleusten Anweisungen, kann er dadurch beeinflusst werden. Die Freigabe jedes Schreibvorgangs ist die wichtigste Absicherung dagegen – Diff vor dem Bestätigen lesen.
- Chats und Uploads liegen unverschlüsselt in `~/Library/Application Support/AgentChat/`.

## Sicherheitslücke melden

Bitte nicht als öffentliches Issue, sondern direkt an den Repository-Owner über GitHub.
