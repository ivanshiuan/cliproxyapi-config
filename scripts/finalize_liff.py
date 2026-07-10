"""OBSOLETE — kept only as a record of the pre-automation manual step.

The rich-menu button URLs (POST /admin/line/richmenu) and the auto-welcome
Flex message (routers/line_webhook.py) now look up the live LIFF id and
inject ``?liff=<id>`` at request time, so this script's output is no longer
read by anything: ``richmenu_launch.final.json`` isn't loaded by any running
code at all, and ``flex_welcome_launch.final.json``'s baked-in query param
(if any) is overwritten on every push regardless of what's on disk. Running
this script is harmless but has no effect on production behavior.

Original usage (pre-automation):
    python scripts/finalize_liff.py <LIFF_ID>

Appended ``?liff=<LIFF_ID>`` to every wheel-page URI in the two ``*_launch.final.json``
assets (rich menu + welcome flex) so they could be hand-uploaded to LINE's
console with the correct link already baked in.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse

_ASSETS = Path(__file__).resolve().parent.parent / "restaurant_api" / "line_assets"
_FILES = ["richmenu_launch.final.json", "flex_welcome_launch.final.json"]
_WHEEL_URL_RE = re.compile(r"https://[^\"]+/demo/campaign/grand-open(?:\?[^\"]*)?")


def _add_liff_param(url: str, liff_id: str) -> str:
    parsed = urlparse(url)
    if "liff=" in parsed.query:
        return url  # already wired — idempotent
    sep = "&" if parsed.query else "?"
    return f"{url}{sep}{urlencode({'liff': liff_id})}"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python scripts/finalize_liff.py <LIFF_ID>", file=sys.stderr)
        raise SystemExit(1)
    liff_id = sys.argv[1]

    for name in _FILES:
        path = _ASSETS / name
        text = path.read_text(encoding="utf-8")
        updated = _WHEEL_URL_RE.sub(lambda m: _add_liff_param(m.group(0), liff_id), text)
        json.loads(updated)  # fail loudly on malformed output before writing
        path.write_text(updated, encoding="utf-8")
        n = len(_WHEEL_URL_RE.findall(updated))
        print(f"{name}: {n} wheel link(s) now carry ?liff={liff_id}")


if __name__ == "__main__":
    main()
