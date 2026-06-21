"""QR-code helper — render a payload as a scalable SVG (no raster/Pillow dep).

Used by the campaign router to hand the operator a print-ready QR that points
at the wheel-spin demo page. SVG so it scales to any 門口海報 size without
pixelation.
"""

from __future__ import annotations

import io

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_M


def make_qr_svg(data: str, *, box_size: int = 10, border: int = 4) -> bytes:
    """Return ``data`` encoded as an SVG QR code (UTF-8 bytes).

    Uses the single-``<path>`` SVG factory — one crisp vector path, no embedded
    raster, no Pillow dependency. ``border`` is the quiet-zone in modules (the
    spec minimum is 4).
    """
    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue()


__all__ = ["make_qr_svg"]
