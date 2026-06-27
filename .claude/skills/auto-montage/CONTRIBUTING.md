# Contributing

Thanks for your interest! A few ground rules keep this project clean and trustworthy.

## The clean-room rule (non-negotiable)

This project is **MIT** and deliberately **does not vendor** upstream code.

- ❌ Do **not** copy code or text from [OpenMontage](https://github.com/calesthio/OpenMontage)
  (AGPLv3) or any other AGPL/GPL source into this repo. It would force a license change.
- ✅ Interact with OpenMontage only through its **public CLI / documented contract**.
- ✅ New methodology content must be **original writing**, not copied from other projects.

See [`ATTRIBUTION.md`](./ATTRIBUTION.md) for the full boundary.

## Quality bar

- Scripts must be runnable and, where they make a pass/fail decision, **demonstrably tested**
  (include a sample input + expected verdict in the PR description).
- `subtitle_align_check.py` changes: show before/after on a good and a bad `.srt`.
- Keep dependencies minimal — the zero-API-key baseline must keep working.

## How to propose a change

1. Open an issue describing the problem/idea first for anything non-trivial.
2. Keep PRs focused; one concern per PR.
3. Update `README.md` / `SKILL.md` if behavior changes.
