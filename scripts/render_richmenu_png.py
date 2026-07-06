"""Render the richmenu mockup HTMLs to production-size PNGs (dev tool, not a runtime dependency).

LINE's rich-menu upload rejects anything off the exact pixel spec, so this
forces `.menu` to the real width/height (no device_scale_factor rounding)
before screenshotting just that element — instead of hand-cropping a manual
browser screenshot of the scaled-down on-screen preview.

Requires Playwright + a Chromium binary, neither of which are runtime
dependencies of the app — install separately if re-running:
    pip install playwright && playwright install chromium
"""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

_ASSETS = Path(__file__).resolve().parent.parent / "restaurant_api" / "line_assets"

_JOBS = [
    ("richmenu_launch_mockup.html", "richmenu_launch.png", 2500, 843),
    ("richmenu_mockup.html", "richmenu_full.png", 2500, 1686),
]


def _chromium_executable() -> str | None:
    """Resolve a Chromium binary: PATH first, then a PLAYWRIGHT_BROWSERS_PATH install,
    else None (let Playwright pick its own default)."""
    if found := (shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")):
        return found
    browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_root:
        matches = sorted(glob.glob(f"{browsers_root}/chromium-*/chrome-linux/chrome"))
        if matches:
            return matches[-1]
    return None


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium_executable())
        for html_name, png_name, real_w, real_h in _JOBS:
            page = browser.new_page(viewport={"width": real_w + 100, "height": real_h + 100})
            page.goto(f"file://{_ASSETS / html_name}")
            # Override the preview's scaled-down .menu size with the exact production pixels.
            page.add_style_tag(content=f".menu {{ width:{real_w}px !important; height:{real_h}px !important; }}")
            el = page.locator(".menu")
            box = el.bounding_box()
            assert box is not None
            assert round(box["width"]) == real_w, box
            assert round(box["height"]) == real_h, box
            el.screenshot(path=str(_ASSETS / png_name))
            page.close()
            print(f"{png_name}: {round(box['width'])}x{round(box['height'])}")
        browser.close()


if __name__ == "__main__":
    main()
