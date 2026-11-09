# Claude Work Log plugin

Builds a spreadsheet-style table of everything you have worked on with Claude (Claude Code, Cowork, and optionally a claude.ai Chat export). The table is published as a private claude.ai artifact that you can filter by date, category, source, account and keyword. A security panel flags API keys, tokens, private keys and passwords that ever reached the AI. Values are masked.

It can update itself every day, and it can send your log to a shared team log so a team lead reads everyone's work through one link.

## Install (once)

Needs Claude Code (desktop app Code tab or the `claude` CLI) and Python 3. Any Claude plan works.

In Claude Code, run:

```
/plugin marketplace add __REPO__
```

```
/plugin install work-log@work-log
```

Restart Claude Code, then say:

> Make a log of my work with Claude

If you installed the older single-file version, delete the folder `~/.claude/skills/claude-work-log/` first so the two do not clash.

## Get updates automatically

Run `/plugin`, open **Marketplaces**, select **work-log** and turn on auto-update. Without it, run:

```
/plugin marketplace update work-log
```

## Join a team log

1. Ask your team lead for the team log link and **Contributor** access.
2. Open the link once in claude.ai while signed in.
3. Tell Claude: `Send my log to the team log <link>`

The team lead sees your table rows, masked secret warnings, your Claude account email, and when you last sent. Your chat history never leaves your computer. Personal-category rows are not sent unless you allow it.

## Daily update

Tell Claude: `Update my work log every day at 5 pm`. The Claude app must be open at that time.

## What's inside

```
plugins/work-log/skills/work-log/
  SKILL.md           instructions Claude follows
  work_sources.py    reads local history and scans for secrets (prints masked values only)
  template.html      the empty table page
```

Your own data stays in `~/.claude/claude-work-log/` and is never part of this repo.
