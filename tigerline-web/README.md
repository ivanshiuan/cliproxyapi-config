# tigerline-web

Client-side TypeScript-ish port of the TIGER LINE PRIME decision engine.

- **Nothing runs on a server.** The whole classifier / corridor / harness /
  stake / recommender / review pipeline is vanilla ES modules that execute
  in the browser tab.
- **Deploys to Cloudflare Pages as pure static.** No build step, no Docker,
  no Fly.io. See `docs/DEPLOY_CLOUDFLARE.md`.
- **17 parity tests** in `tests/` prove the JS output matches Python for
  the 3 canonical reproductions (Belgium NZ, Egypt Iran, rotation trap).

## Structure

```
tigerline-web/
├── index.html            # Single-page shell
├── css/app.css           # Dark, mobile-first theme
├── js/
│   ├── decimal.js        # tiny decimal helper (epsilon-aware compare)
│   ├── types.js          # MatchInput validation (StrictDecimal-style)
│   ├── classifier.js     # 7-scenario decision tree (rules inlined)
│   ├── corridor.js       # score corridor templates (inlined)
│   ├── harness.js        # risk gate: upgrade / normal / downgrade / skip
│   ├── stake.js          # A+/A/B/C/D → bankroll %
│   ├── recommender.js    # composes classification → BetPlan
│   ├── review.js         # post-match scorecard 0-100
│   ├── compliance.js     # bilingual disclaimer + forbidden-phrase guard
│   ├── examples.js       # 3 canonical example inputs
│   └── app.js            # DOM wiring
├── tests/canonical.test.js  # node:test parity vs Python engine
└── docs/DEPLOY_CLOUDFLARE.md
```

## Local dev

```bash
cd tigerline-web
python3 -m http.server 8080       # or any static server
open http://localhost:8080
```

## Run tests

```bash
cd tigerline-web
node --test tests/*.test.js
```

## Deploy

```bash
cd tigerline-web
npx wrangler pages deploy . --project-name tigerline
```

See `docs/DEPLOY_CLOUDFLARE.md` for the dashboard-only path (no CLI needed).

## Why the port

The Python CLI (`tigerline/`) is the source of truth — 181 pytest cases,
YAML-driven rules, SQLite persistence, snapshot / CLV / movement engines.

The web port is a **decision-engine subset**: everything a mobile user needs
for pre-match analyze + post-match review, running as a static app on
Cloudflare Pages so it costs $0 and needs no server.

The Python port stays authoritative. Any rule change starts in
`tigerline/config/*.yaml`, then mirrors here in `js/classifier.js` /
`js/corridor.js` / `js/stake.js`. Parity is enforced by the tests.
