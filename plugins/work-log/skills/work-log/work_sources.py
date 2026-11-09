"""Read Claude history (Code, Cowork, and an optional claude.ai Chat export) for the work log.

Usage:
  python -I work_sources.py --all                 # every session from every source
  python -I work_sources.py --date today          # prompts sent on one day (local time)
  python -I work_sources.py --secrets             # tokens / API keys / passwords exposed to the AI (masked)
  add --chat-export PATH to include a claude.ai export (conversations.json, or the export .zip)

Sessions started by the daily work-log task itself are skipped (they contain MARKER).
Secret values are never printed in full: only the type, a 4-character prefix and a fingerprint.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import date, datetime

MARKER = "[daily-work-log-task]"
HOME = os.path.expanduser("~")
CODE_GLOB = os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")
# Cowork sessions live in the desktop app's data folder (Windows and macOS locations)
COWORK_ROOTS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Claude-Data"),
    os.path.join(os.environ.get("APPDATA", ""), "Claude"),
    os.path.join(HOME, "Library", "Application Support", "Claude"),
    os.path.join(HOME, "Library", "Application Support", "Claude-Data"),
]

SECRET_PATTERNS = [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("GitHub token", r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    ("Slack token", r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("AWS access key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    ("Stripe key", r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("Discord bot token", r"[MNO][A-Za-z\d_\-]{23,25}\.[A-Za-z\d_\-]{6}\.[A-Za-z\d_\-]{27,38}"),
    ("JWT / service token", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("Private key", r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    ("Database URL with password", r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/@]+:[^\s@/]{4,}@[^\s/]+"),
    ("Password / secret in text", r"(?i)(?:password|passwd|pwd|รหัสผ่าน|api[_\- ]?key|secret|access[_\- ]?token|bot[_\- ]?token|channel[_\- ]?secret)\s*[:=]\s*[\"']?([^\s\"'`,;]{6,})"),
    ("Login pair (user / password)", r"(?i)(?:user(?:name)?\s*/\s*password)[^\n]{0,40}?\s([^\s/]{3,})\s*/\s*([^\s]{6,})"),
]
SECRET_RES = [(n, re.compile(p)) for n, p in SECRET_PATTERNS]
PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|\.+|<.*>|\$\{?.*|your[_\-]?.*|example.*|changeme|password|placeholder|undefined|null|none|true|false|process\.env.*|os\.environ.*|env\..*)$")


def local_dt(ts):
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
        return datetime.fromtimestamp(int(ts) / 1000)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for x in content:
            if not isinstance(x, dict):
                continue
            if x.get("type") == "text":
                out.append(x.get("text", ""))
            elif x.get("type") == "tool_result":
                out.append(text_of(x.get("content")))
        return "\n".join(out)
    return ""


def is_tool_result(content):
    return isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content)


def read_transcript(path):
    """Yield (datetime, role, text) where role is 'prompt', 'tool' or 'assistant'. None means skip session."""
    items = []
    for line in open(path, encoding="utf-8", errors="ignore"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        typ, ts = o.get("type"), o.get("timestamp")
        # Compaction summaries restate earlier messages (maybe even the marker); they are not prompts
        if typ not in ("user", "assistant") or not ts or o.get("isMeta") or o.get("isCompactSummary"):
            continue
        content = o.get("message", {}).get("content")
        t = text_of(content).strip()
        if not t:
            continue
        role = "assistant" if typ == "assistant" else ("tool" if is_tool_result(content) else "prompt")
        # Only the session's first prompt decides whether it is a work-log task run
        if role == "prompt" and not any(r == "prompt" for _, r, _ in items) and MARKER in t:
            return None
        items.append((local_dt(ts), role, t))
    return items


def current_account():
    """(account uuid, email) of the Claude account signed in to Claude Code on this computer."""
    try:
        oa = json.load(open(os.path.join(HOME, ".claude.json"), encoding="utf-8")).get("oauthAccount") or {}
    except (ValueError, OSError):
        oa = {}
    return oa.get("accountUuid"), oa.get("emailAddress")


CUR_UUID, CUR_EMAIL = current_account()


def account_label(uuid=None, email=None):
    """Best account name for a session. '(assumed)' marks sessions that recorded no account."""
    if email:
        return email
    if uuid:
        return CUR_EMAIL if uuid == CUR_UUID and CUR_EMAIL else f"account {uuid[:8]}"
    return f"{CUR_EMAIL} (assumed)" if CUR_EMAIL else "unknown"


def code_sessions():
    for path in glob.glob(CODE_GLOB):
        items = read_transcript(path)
        if items is None:
            continue
        cwd = title = uuid = None
        for line in open(path, encoding="utf-8", errors="ignore"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            cwd = cwd or o.get("cwd")
            title = title or o.get("summary") or o.get("aiTitle")
            uuid = uuid or o.get("accountUuid") or o.get("ownerAccountUuid")
        yield {"source": "Code", "folder": os.path.basename((cwd or "").rstrip("\\/")) or "-", "title": title,
               "account": account_label(uuid), "items": items}


def cowork_sessions():
    seen = set()
    for root in COWORK_ROOTS:
        if not root or not os.path.isdir(root):
            continue
        for meta_path in glob.glob(os.path.join(root, "**", "local-agent-mode-sessions", "**", "local_*.json"), recursive=True):
            if meta_path in seen:
                continue
            seen.add(meta_path)
            try:
                meta = json.load(open(meta_path, encoding="utf-8"))
            except (ValueError, OSError):
                continue
            sdir = meta_path[:-5]
            items = []
            for t in glob.glob(os.path.join(sdir, ".claude", "projects", "*", "*.jsonl")):
                got = read_transcript(t)
                if got is None:
                    items = None
                    break
                items += got
            if items is None:
                continue
            if not items and meta.get("initialMessage") and meta.get("createdAt"):
                items = [(local_dt(meta["createdAt"]), "prompt", meta["initialMessage"])]
            folders = meta.get("userSelectedFolders") or []
            folder = os.path.basename(str(folders[0]).rstrip("\\/")) if folders else "Cowork"
            # Cowork keeps sessions under a folder named after the account uuid
            parts = os.path.normpath(meta_path).split(os.sep)
            uuid = parts[parts.index("local-agent-mode-sessions") + 1] if "local-agent-mode-sessions" in parts else None
            yield {"source": "Cowork", "folder": folder, "title": meta.get("title"),
                   "account": account_label(uuid, meta.get("emailAddress")), "items": sorted(items, key=lambda x: x[0])}


def chat_sessions(export_path):
    if not export_path:
        return
    users = []
    if export_path.lower().endswith(".zip"):
        with zipfile.ZipFile(export_path) as z:
            name = next(n for n in z.namelist() if n.endswith("conversations.json"))
            convs = json.loads(z.read(name).decode("utf-8"))
            uname = next((n for n in z.namelist() if n.endswith("users.json")), None)
            users = json.loads(z.read(uname).decode("utf-8")) if uname else []
    else:
        convs = json.load(open(export_path, encoding="utf-8"))
        upath = os.path.join(os.path.dirname(export_path), "users.json")
        users = json.load(open(upath, encoding="utf-8")) if os.path.isfile(upath) else []
    # The export's users.json names the account the export came from
    email = next((u.get("email_address") for u in users if isinstance(u, dict) and u.get("email_address")), None)
    account = email or "claude.ai export"
    for c in convs:
        items = []
        for m in c.get("chat_messages", []):
            t = m.get("text") or text_of(m.get("content"))
            ts = m.get("created_at")
            if not t or not ts:
                continue
            role = "prompt" if m.get("sender") == "human" else "assistant"
            items.append((local_dt(ts), role, t.strip()))
        if items:
            yield {"source": "Chat", "folder": "claude.ai", "title": c.get("name"), "account": account, "items": items}


def all_sessions(export_path):
    yield from code_sessions()
    yield from cowork_sessions()
    yield from chat_sessions(export_path)


def mask(value):
    value = value.strip()
    return (value[:4] + "…") if len(value) > 6 else "…"


GENERIC = {"Password / secret in text", "Login pair (user / password)"}


def plausible_generic(value):
    # A typed password or key mixes letters and digits; variable names and code fragments do not
    return (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)
            and value[0] not in "([{<$" and "(" not in value and "=" not in value
            and not re.match(r"\d+:", value) and not re.fullmatch(r"[A-Z0-9_]+", value)
            and not re.fullmatch(r"[a-z_]+\.[A-Za-z_.]+", value))


def find_secrets(text):
    specific = []
    for name, rx in SECRET_RES:
        for m in rx.finditer(text):
            value = (m.group(m.lastindex) if m.lastindex else m.group(0)).strip()
            # Real keys are high-entropy; "sk-xxxxxxxx" style placeholders are not
            if PLACEHOLDER.match(value) or (name != "Private key" and len(set(value)) < 8):
                continue
            if name in GENERIC:
                if not plausible_generic(value) or any(v in value or value in v for v in specific):
                    continue
            else:
                specific.append(value)
            yield name, value


def redact(text):
    """Replace every secret in a prompt with its masked form so listings never repeat it."""
    for name, value in find_secrets(text):
        text = text.replace(value, f"[{name}: {mask(value)}]")
    return text


def cmd_list(sessions, target):
    rows = []
    for s in sessions:
        prompts = [(d, t) for d, r, t in s["items"] if r == "prompt" and not t.startswith("<") and not t.startswith("[Request interrupted")]
        if target:
            prompts = [p for p in prompts if p[0].date().isoformat() == target]
        if prompts:
            rows.append((prompts[0][0].date().isoformat(), prompts[-1][0].date().isoformat(), s, prompts))
    if not rows:
        print(f"NO_SESSIONS {target or ''}".strip())
        return
    for first, last, s, prompts in sorted(rows, key=lambda r: r[0]):
        span = first if first == last else f"{first} -> {last}"
        print(f"=== [{s['source']}] {span} | account: {s['account']} | folder: {s['folder']} | title: {s['title']} | prompts: {len(prompts)}")
        for _, t in prompts[: 12 if target else 6]:
            print("   -", redact(t)[:400].replace("\n", " "))


def cmd_secrets(sessions):
    # One entry per distinct secret value, listing every place it reached the AI
    found = {}
    for s in sessions:
        for d, role, text in s["items"]:
            if role == "assistant":
                continue
            for name, value in find_secrets(text):
                fp = hashlib.sha256(value.encode()).hexdigest()[:8]
                hit = found.setdefault(fp, {"first": d, "last": d, "type": name, "masked": mask(value), "fp": fp,
                                            "where": {}, "via": set(), "accounts": set(), "count": 0})
                hit["accounts"].add(s["account"])
                hit["first"], hit["last"] = min(hit["first"], d), max(hit["last"], d)
                place = f"{s['source']}:{s['folder']}"
                hit["where"].setdefault(place, (d, s["title"]))
                hit["via"].add("typed in chat" if role == "prompt" else "read from file/command output")
                hit["count"] += 1
    if not found:
        print("NO_SECRETS")
        return
    print("first_seen | last_seen | type | masked | fingerprint | how | occurrences | account | where (source:folder [session title])")
    for h in sorted(found.values(), key=lambda h: h["first"]):
        where = "; ".join(f"{p} [{t}]" for p, (d, t) in sorted(h["where"].items(), key=lambda x: x[1][0]))
        print(" | ".join([h["first"].date().isoformat(), h["last"].date().isoformat(), h["type"], h["masked"], h["fp"],
                          " + ".join(sorted(h["via"])), str(h["count"]), ", ".join(sorted(h["accounts"])), where]))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--date")
    g.add_argument("--secrets", action="store_true")
    ap.add_argument("--chat-export")
    a = ap.parse_args()
    sessions = list(all_sessions(a.chat_export))
    if a.secrets:
        cmd_secrets(sessions)
    else:
        cmd_list(sessions, date.today().isoformat() if a.date == "today" else a.date)


if __name__ == "__main__":
    main()
