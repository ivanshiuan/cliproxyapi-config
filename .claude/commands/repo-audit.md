---
description: Security-audit a third-party repo (clone → 4-dimension parallel audit → adversarial verify → go/no-go). Use before forking or depending on external AI-agent/server code.
allowed-tools: Bash, Read, Grep, Glob, Agent, Write, TaskCreate, TaskUpdate
---

You are running the project's third-party security audit playbook — the same
flow used on `opensquilla` (see `docs/opensquilla/`). Target repo/URL is in
`$ARGUMENTS` (a GitHub URL, or a path to an already-cloned repo).

Goal: a **go/no-go** verdict on whether it's safe to fork or depend on, backed
by code-level evidence, not vibes.

## 1. Get the code

- If `$ARGUMENTS` is a URL: `git clone --depth 1 <url>` into the scratchpad dir
  (never into the user's repo). If `add_repo` is available and same-owner, that
  works too; otherwise plain clone through the proxy.
- If it's a path: use it directly.
- Map the surface: `find src -maxdepth 2 -type d`, count LOC, note the language
  split. If it's > ~50k LOC, do NOT read it all — fan out (step 3).

## 2. Read the load-bearing small files yourself

Before fanning out, read the obvious crux files directly (auth, secrets,
tool-boundary, install script, network bind config). Cheap, and it calibrates
the agent prompts.

## 3. Fan out 4 parallel audit agents (general-purpose)

Launch in ONE message so they run concurrently. Each gets the clone's absolute
path and reports: findings (file:line, severity, evidence lines), a verdict for
its dimension, and "is this better/worse than the incumbent it's copying?".

1. **Network exposure** — bind address (loopback vs 0.0.0.0), WebSocket/HTTP
   auth, CORS, Origin checks, CSRF/token handling, token leakage to logs/query.
   (This is where drive-by CSWSH / ClawJacked-class bugs live — check it hard.)
2. **Sandbox & command execution** — is isolation real or decorative? bwrap/
   seatbelt params, fail-open vs fail-closed when the sandbox binary is absent,
   command-injection surface, path traversal.
3. **Secrets handling** — at-rest storage (keychain vs plaintext + chmod), any
   migrate/import secret flows, log/diagnostic redaction, secrets in system
   prompt or memory.
4. **Supply chain & install** — curl|sh, checksum/signature verification of
   downloaded artifacts, dependency pinning, CI workflow risks
   (pull_request_target, fork secret leakage, unpinned actions), Dockerfile
   (root?), service units (privilege).

## 4. Adversarially verify every critical/high

For each critical or high finding, spawn TWO more agents in parallel:

- **Red team** — default stance "this is a false positive"; try hard to refute
  it with code evidence. Only concede when refutation fails on every angle.
- **Blast radius** — precisely bound real exploitability: what stops it (token
  mode? sandbox? redaction?), what it can actually reach, and whether the
  severity is right. Don't over- or under-state.

A finding survives only if red team fails to refute it. Verify the load-bearing
claim yourself by reading the exact lines — never ship a critical on an agent's
word alone.

## 5. Synthesize

Write the report to `docs/<repo-name>/01_security_audit.md` (create the dir):

- Four-dimension severity table.
- Each surviving critical/high: attack chain, code pointers, what's mitigated,
  what isn't, and the concrete fix.
- A pre-use hardening checklist (what MUST be set before real credentials touch
  it).
- One-line comparison vs whatever incumbent it's derived from.
- Explicit **go / no-go** with conditions.

Track the phases with TaskCreate/TaskUpdate so progress is visible. Keep every
finding anchored to `file:line` — no claim without evidence.
