"""Aktualisiert einen gespeicherten Agenten mit der Konfiguration aus app/agent_template.json.

Die Vorlage ist dieselbe, die die App beim Button „Voice-Agent-Designer in diesem Projekt anlegen“
verwendet. So gibt es nur eine Quelle für Instructions, Modell und Tools.

Ablauf:
  1. Aktuellen Stand des Agenten als JSON-Backup sichern (tools/backups/, nicht im Git).
  2. Ohne --apply: nur Backup (Dry Run).
  3. Mit --apply: Update senden.

Aufruf:
  export OPENAI_API_KEY=sk-...              # Key aus dem Projekt des Agenten
  python tools/update_agent.py --agent-id agent_...            # nur Backup
  python tools/update_agent.py --agent-id agent_... --apply    # Backup + Update

Optional GitHub-MCP (die Credential-ID ist projektgebunden und gehört nicht ins Repo):
  python tools/update_agent.py --agent-id agent_... --github-credential credential_... --apply
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "agent_template.json"
BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def build_config(github_credential: str | None) -> dict:
    cfg = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    if github_credential:
        cfg["tools"].append({
            "type": "mcp",
            "server_label": "github",
            "credential_id": github_credential,
            "transport": {"type": "http", "server_url": "https://api.githubcopilot.com/mcp/", "headers": {}},
            "request_metadata": {},
            "allowed_tools": None,
            # False: ein abgelaufenes GitHub-Credential soll nicht jeden Run blockieren.
            "required": False,
            "connection_origin": "service",
        })
    return cfg


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", required=True, help="ID des zu aktualisierenden Agenten (agent_...)")
    parser.add_argument("--github-credential", help="Optional: credential_... für den GitHub-MCP")
    parser.add_argument("--apply", action="store_true", help="Update wirklich senden")
    args = parser.parse_args()

    if not args.agent_id.startswith("agent_"):
        parser.error("--agent-id muss mit 'agent_' beginnen")

    # base_url fest, damit kein Gateway aus OPENAI_BASE_URL dazwischenfunkt
    async with AsyncOpenAI(base_url="https://api.openai.com/v1") as client:
        current = await client.beta.agents.retrieve(args.agent_id)
        BACKUP_DIR.mkdir(exist_ok=True)
        path = BACKUP_DIR / f"{args.agent_id}_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(current.model_dump_json(indent=2), encoding="utf-8")
        print(f"Backup gespeichert: {path}")

        if not args.apply:
            print("Dry Run: kein Update gesendet. Mit --apply ausführen, um zu aktualisieren.")
            return

        agent = await client.beta.agents.update(args.agent_id, **build_config(args.github_credential))
        print(f"Aktualisiert: {agent.id} ({agent.name}, {agent.model})")


if __name__ == "__main__":
    asyncio.run(main())
