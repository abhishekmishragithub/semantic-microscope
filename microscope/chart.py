"""Calibration chart: predicted vs observed per bucket, as SVG (stdlib) and PNG.

SVG is written with string formatting so the chart needs no dependency. PNG comes from
whichever rasteriser is on the machine (rsvg-convert, ImageMagick, or macOS qlmanage); if none
is found the SVG is still written and the caller is told.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from microscope.validate import BUCKETS

W, H = 720, 720  # square: qlmanage thumbnails are square, and a calibration plot should be too
PAD_L, PAD_R, PAD_T, PAD_B = 72, 28, 96, 64
INK, INK2, INK3, LINE, ACCENT, BG = "#1b1b1f", "#5a5a63", "#9a9aa3", "#d9d9d5", "#1f5fa8", "#ffffff"
FONT = "ui-monospace, Menlo, Consolas, monospace"


def bucket_points(doc: dict[str, Any]) -> list[dict[str, float | int]]:
    rows = [(v["p"], bool(v["human"])) for v in doc["labels"].values() if v["human"] is not None]
    pts = []
    for lo, hi in BUCKETS:
        b = [(p, y) for p, y in rows if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not b:
            continue
        n = len(b)
        pred = sum(p for p, _ in b) / n
        obs = sum(1 for _, y in b if y) / n
        # Wilson 95% interval on the observed rate; honest about small n.
        z = 1.96
        centre = (obs + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(obs * (1 - obs) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        pts.append(
            {
                "lo": lo,
                "hi": hi,
                "n": n,
                "pred": pred,
                "obs": obs,
                "ci_lo": max(0.0, centre - half),
                "ci_hi": min(1.0, centre + half),
            }
        )
    return pts


def brier(doc: dict[str, Any]) -> tuple[float, int]:
    rows = [
        (v["p"], 1.0 if v["human"] else 0.0)
        for v in doc["labels"].values()
        if v["human"] is not None
    ]
    return (sum((p - y) ** 2 for p, y in rows) / len(rows) if rows else float("nan")), len(rows)


def render_svg(doc: dict[str, Any], title: str | None = None) -> str:
    pts = bucket_points(doc)
    b, n_lab = brier(doc)
    x0, x1, y0, y1 = PAD_L, W - PAD_R, H - PAD_B, PAD_T
    sx = lambda v: x0 + v * (x1 - x0)
    sy = lambda v: y0 - v * (y0 - y1)
    o: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="{BG}"/>',
    ]
    ttl = title or f"{doc.get('dimension', '?')} · {doc.get('run_id', '?')}"
    o.append(
        f'<text x="{PAD_L}" y="30" font-size="15" font-weight="600" fill="{INK}">{esc(ttl)}</text>'
    )
    o.append(
        f'<text x="{PAD_L}" y="48" fill="{INK2}">n = {n_lab} human labels · Brier {b:.3f} (0.25 = always guessing 0.5)</text>'
    )
    o.append(
        f'<text x="{PAD_L}" y="66" fill="{INK3}" font-size="11">bars: 95% Wilson interval on the observed rate · predictions hidden while labelling</text>'
    )
    # grid, axes
    for i in range(6):
        v = i / 5
        o.append(
            f'<line x1="{sx(v):.1f}" y1="{y0}" x2="{sx(v):.1f}" y2="{y1}" stroke="{LINE}" stroke-width="1"/>'
        )
        o.append(
            f'<line x1="{x0}" y1="{sy(v):.1f}" x2="{x1}" y2="{sy(v):.1f}" stroke="{LINE}" stroke-width="1"/>'
        )
        o.append(
            f'<text x="{sx(v):.1f}" y="{y0 + 18}" text-anchor="middle" fill="{INK2}">{v:.1f}</text>'
        )
        o.append(
            f'<text x="{x0 - 10}" y="{sy(v) + 4:.1f}" text-anchor="end" fill="{INK2}">{v:.1f}</text>'
        )
    o.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" stroke="{INK3}"/>')
    o.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="{INK3}"/>')
    o.append(
        f'<text x="{(x0 + x1) / 2:.1f}" y="{H - 22}" text-anchor="middle" fill="{INK}">mean predicted probability (Jev)</text>'
    )
    o.append(
        f'<text transform="translate(22 {(y0 + y1) / 2:.1f}) rotate(-90)" text-anchor="middle" fill="{INK}">observed rate (human yes)</text>'
    )
    # diagonal
    o.append(
        f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" stroke="{INK3}" stroke-dasharray="5 5"/>'
    )
    o.append(
        f'<text x="{sx(0.93):.1f}" y="{sy(0.97):.1f}" text-anchor="end" fill="{INK3}">perfect calibration</text>'
    )
    # bucket shading labels along the top of the plot
    for lo, hi in BUCKETS:
        o.append(
            f'<text x="{sx((lo + hi) / 2):.1f}" y="{y1 - 8}" text-anchor="middle" fill="{INK3}" font-size="10">{lo:.1f}–{hi:.1f}</text>'
        )
    if pts:
        path = " ".join(
            f"{'M' if i == 0 else 'L'}{sx(p['pred']):.1f},{sy(p['obs']):.1f}"
            for i, p in enumerate(pts)
        )
        o.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="2"/>')
        for p in pts:
            x, y = sx(p["pred"]), sy(p["obs"])
            o.append(
                f'<line x1="{x:.1f}" y1="{sy(p["ci_lo"]):.1f}" x2="{x:.1f}" y2="{sy(p["ci_hi"]):.1f}" stroke="{ACCENT}" stroke-width="1" opacity="0.6"/>'
            )
            o.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{BG}" stroke="{ACCENT}" stroke-width="2"/>'
            )
            o.append(
                f'<text x="{x + 9:.1f}" y="{y - 8:.1f}" fill="{INK2}" font-size="11">n={p["n"]}</text>'
            )
    else:
        o.append(
            f'<text x="{(x0 + x1) / 2:.1f}" y="{(y0 + y1) / 2:.1f}" text-anchor="middle" fill="{INK3}">no labels yet</text>'
        )
    o.append("</svg>")
    return "\n".join(o)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rasterise(svg_path: Path, png_path: Path, scale: int = 2) -> str | None:
    """Returns the name of the tool used, or None if no rasteriser is available."""
    width = W * scale
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-w", str(width), "-o", str(png_path), str(svg_path)], check=True
        )
        return "rsvg-convert"
    for tool in ("magick", "convert"):
        if shutil.which(tool):
            r = subprocess.run(
                [
                    tool,
                    "-density",
                    str(96 * scale),
                    "-background",
                    "white",
                    str(svg_path),
                    str(png_path),
                ],
                capture_output=True,
                check=False,
            )
            if r.returncode == 0 and png_path.exists():
                return tool
    if shutil.which("qlmanage"):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                ["qlmanage", "-t", "-s", str(width), "-o", tmp, str(svg_path)],
                capture_output=True,
                check=False,
            )
            produced = Path(tmp) / (svg_path.name + ".png")
            if r.returncode == 0 and produced.exists():
                shutil.copy(produced, png_path)
                return "qlmanage"
    return None
