"""Talk to the team work-log server: join, check, fetch rules, send the log.

  python -I team_sync.py join --key KEY      save the team key (and optional --server URL), then check
  python -I team_sync.py check               is the key valid and is this account registered?
  python -I team_sync.py rules               print the current team rules (also saved to rules.json)
  python -I team_sync.py send [FILE]         send the team document (default: team-log.json)

Settings live in ~/.claude/claude-work-log/config.json (server_url, team_key, optional account).
The team key is never printed. Standard library only, and nothing here is specific to Claude
except finding the signed-in account, so the Codex version can reuse it with --tool codex --account.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_SERVER = "https://work-log-server-production.up.railway.app"
PLUGIN_VERSION = "2.0.0"
WORK_DIR = Path.home() / ".claude" / "claude-work-log"
CONFIG = WORK_DIR / "config.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_config(cfg: dict) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def signed_in_email():
    """Email of the Claude account signed in to Claude Code on this computer (same source as work_sources.py)."""
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
        return (data.get("oauthAccount") or {}).get("emailAddress")
    except (OSError, ValueError):
        return None


def account(cfg: dict, override=None):
    return (override or cfg.get("account") or signed_in_email() or "").strip().lower() or None


def call(cfg: dict, method: str, path: str, body=None):
    url = (cfg.get("server_url") or DEFAULT_SERVER).rstrip("/") + path
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Team-Key", cfg.get("team_key") or "")
    req.add_header("X-Plugin-Version", PLUGIN_VERSION)
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(text).get("detail", text)
        except ValueError:
            return e.code, text
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, f"cannot reach the server: {e}"


def explain(status: int, detail) -> str:
    if status == 401:
        return "TEAM_KEY_INVALID: the team key is wrong or was changed. Ask the team lead for the current key."
    if status == 403:
        return "NOT_REGISTERED: this account's email is not on the team's employee list. Ask the team lead to add it."
    if status == 0:
        return f"SERVER_UNREACHABLE: {detail}. Nothing was lost; the next run sends again."
    return f"ERROR {status}: {detail}"


def cmd_check(cfg: dict, args) -> int:
    if not cfg.get("team_key"):
        print("NO_TEAM_KEY: run `join --key KEY` first.")
        return 1
    status, rules = call(cfg, "GET", "/rules")
    if status != 200:
        print(explain(status, rules))
        return 1
    email = account(cfg, args.account)
    if not email:
        print("NO_ACCOUNT: could not find the signed-in Claude account; set \"account\" in config.json.")
        return 1
    status, me = call(cfg, "GET", "/members/check?account=" + urllib.request.quote(email))
    if status != 200:
        print(explain(status, me))
        return 1
    print(f"server: {(cfg.get('server_url') or DEFAULT_SERVER)}")
    print(f"team key: OK | rules version: {rules['version']}")
    print(f"account: {email}")
    if not me.get("registered") or not me.get("active"):
        print("NOT_REGISTERED: this email is not on the team's employee list (or was deactivated). Ask the team lead to add it.")
        return 1
    print(f"registered: yes | company: {me.get('company') or '-'} | team: {me.get('team') or '-'}"
          f" | last received: {me.get('last_received_at') or 'never'}")
    return 0


def cmd_join(cfg: dict, args) -> int:
    cfg["team_key"] = args.key.strip()
    if args.server:
        cfg["server_url"] = args.server.strip().rstrip("/")
    save_config(cfg)
    print("saved team key to config.json")
    return cmd_check(cfg, args)


def cmd_rules(cfg: dict, args) -> int:
    status, rules = call(cfg, "GET", "/rules")
    if status != 200:
        print(explain(status, rules))
        return 1
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    (WORK_DIR / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rules, ensure_ascii=False, indent=2))
    return 0


def cmd_send(cfg: dict, args) -> int:
    path = Path(args.file) if args.file else WORK_DIR / "team-log.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    email = account(cfg, args.account)
    if not email:
        print("NO_ACCOUNT: could not find the signed-in Claude account; set \"account\" in config.json.")
        return 1
    body = {
        "account": email,
        "team": doc.get("team") or cfg.get("team"),
        "tool": args.tool,
        "updatedAt": doc.get("updatedAt"),
        "rows": doc.get("rows", []),
        "secrets": doc.get("secrets", []),
    }
    status, result = call(cfg, "POST", "/logs", body)
    if status != 200:
        print(explain(status, result))
        return 1
    print(f"SENT: {result['rows']} rows, {result['secrets']} secrets"
          f" ({result['skipped']} rows left out by the team rules)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", help="email to send as (default: signed-in Claude account)")
    ap.add_argument("--tool", default="claude", choices=["claude", "codex"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("join")
    j.add_argument("--key", required=True)
    j.add_argument("--server")
    sub.add_parser("check")
    sub.add_parser("rules")
    s = sub.add_parser("send")
    s.add_argument("file", nargs="?")
    args = ap.parse_args()
    cfg = load_config()
    return {"join": cmd_join, "check": cmd_check, "rules": cmd_rules, "send": cmd_send}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
