# Auto-Montage

**A rigorous, license-clean video-automation Skill for AI coding agents.**
Tell Claude Code (or any agent) what you want — it runs research → script → assets →
voiceover → edit → **bilingual subtitles** → render, all **headless from the CLI**.

> 一個**嚴謹、授權乾淨**的影片自動化 Skill。對 AI agent 說一句需求，它就無頭（headless）
> 跑完整條產線並過**雙語字幕品質閘**。**不需要 CapCut、不需要 Computer Use、不需要桌面 GUI。**

[![CI](https://github.com/ivanshiuan/auto-montage/actions/workflows/ci.yml/badge.svg)](https://github.com/ivanshiuan/auto-montage/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE) · zero-API-key baseline · headless · clean-room

---

## Why this exists

Most "auto-editing" projects are either (a) glued to a desktop GUI you have to babysit, or
(b) a pile of scripts with no quality control. Auto-Montage is the opposite:

- **Headless & agent-native** — drives the [OpenMontage](https://github.com/calesthio/OpenMontage)
  engine entirely through its CLI. No CapCut, no Computer Use, no screen-poking.
- **Quality is enforced, not hoped for** — ships a **tested** bilingual-subtitle gate
  (reading-speed / line-length / overlap / cross-language alignment) that *blocks* a bad cut.
- **License-clean by design (clean-room)** — does **not** vendor upstream code. OpenMontage
  (AGPLv3) is called as an external dependency, so you can adopt this Skill under **MIT** without
  pulling copyleft into your product. See [`ATTRIBUTION.md`](./ATTRIBUTION.md).

## What's original here vs. what's upstream

Being honest about this up front, because it matters:

| Part | Origin |
|---|---|
| Bilingual-subtitle QA gate (`scripts/subtitle_align_check.py`) | **Original** to this repo |
| Preflight / capability detection (`scripts/preflight.sh`) | **Original** |
| Methodology — pitfalls, retention/hooks, restaurant templates (`knowledge/`, `templates/`) | **Original writing**, *inspired by* the philosophy of [video-autopilot-kit](https://github.com/Hao0321/video-autopilot-kit) (MIT). No text/code copied. |
| The clean-room integration pattern (`SKILL.md`) | **Original** |
| The actual video-generation engine (TTS, stock, render, providers) | [OpenMontage](https://github.com/calesthio/OpenMontage) (**AGPLv3**), called as an external dependency — **not** included in this repo |

Full credit and the AGPL boundary: [`ATTRIBUTION.md`](./ATTRIBUTION.md).

## Quickstart

```bash
# 1. Install the engine (one-time) — needs Python 3.10+, Node 18+, ffmpeg
git clone https://github.com/calesthio/OpenMontage.git
cd OpenMontage && make setup && export OPENMONTAGE_HOME="$PWD" && cd -

# 2. Drop this folder into your agent's skills dir
#    Claude Code:  .claude/skills/auto-montage/

# 3. Verify
bash .claude/skills/auto-montage/scripts/preflight.sh
```

Then just ask your agent:

> *"Make a 30s vertical short about our signature beef noodle soup, bilingual subtitles, zero-API-key."*

It will: preflight → shape the brief with the methodology → drive OpenMontage stage-by-stage →
pass the subtitle gate → report cost & wait for approval → deliver `final.mp4`.

## How it works (7 steps)

`preflight → brief intake → methodology shaping → drive engine (per OpenMontage's contract)
→ bilingual-subtitle gate → cost/approval gate → render + self-review + deliver`

Details in [`SKILL.md`](./SKILL.md).

## Try the subtitle gate right now (no engine needed)

```bash
python scripts/subtitle_align_check.py your.srt --lang zh
python scripts/subtitle_align_check.py zh.srt --pair en.srt   # bilingual alignment
```
Exits non-zero on any critical finding — drop it straight into CI.

## Repo layout
```
auto-montage/
├── SKILL.md                       # agent instructions (the orchestration brain)
├── knowledge/                     # original methodology → per-video QA checklist
├── templates/                     # restaurant-vlog flagship example + brief schema
└── scripts/
    ├── preflight.sh               # engine/tool detection  (tested)
    └── subtitle_align_check.py    # bilingual subtitle gate (tested)
```

## Status & honest limitations

- Early but functional. The two scripts are tested; the end-to-end render depends on your
  OpenMontage install and chosen providers.
- This Skill is an **orchestrator + methodology layer**, not a rendering engine. The heavy lifting
  is OpenMontage's.
- Restaurant templates are the worked example; the framework is domain-agnostic.

## License & credits

MIT — see [`LICENSE`](./LICENSE). Built to stand on the shoulders of, and fully credit,
[OpenMontage](https://github.com/calesthio/OpenMontage) (AGPLv3, external dependency) and
[video-autopilot-kit](https://github.com/Hao0321/video-autopilot-kit) (MIT, methodology inspiration).
See [`ATTRIBUTION.md`](./ATTRIBUTION.md).
