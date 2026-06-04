"""Export the FastAPI app's OpenAPI 3 schema to a file.

Useful for:
  - Generating typed client SDKs (TypeScript / Swift / Kotlin)
  - Posting to API docs portals (Stoplight, Redocly)
  - Diffing schema changes in PRs (`make openapi && git diff openapi.json`)

Usage:
    python scripts/export_openapi.py [--out openapi.json] [--format json|yaml]

The default output is `openapi.json` at the repo root. The file is gitignored
by default; commit it intentionally if you want PR diffs to surface API changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from restaurant_api.main import app

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "openapi.json",
        help="Output path (default: repo-root/openapi.json)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "yaml"),
        default="json",
        help="Output format (default: json)",
    )
    args = parser.parse_args()

    schema = app.openapi()

    if args.format == "yaml":
        try:
            import yaml
        except ImportError:
            print("PyYAML not installed; falling back to JSON. `pip install pyyaml` for YAML.", file=sys.stderr)
            args.format = "json"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "yaml":
        import yaml

        args.out.write_text(
            yaml.safe_dump(schema, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    else:
        args.out.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False),
            encoding="utf-8",
        )

    n_paths = len(schema.get("paths", {}))
    n_schemas = len(schema.get("components", {}).get("schemas", {}))
    print(f"✅ OpenAPI 3 schema written to {args.out.relative_to(REPO_ROOT)}")
    print(f"   {n_paths} paths · {n_schemas} schemas · {schema.get('info', {}).get('version', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
