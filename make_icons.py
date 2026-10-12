"""
make_icons.py  —  Generates the Morning Briefing app icon set.

The mark: a sun rising over a masthead rule, with two text lines beneath —
"morning" + "newspaper", in the launcher's own palette. Pure geometry, so it
renders identically everywhere and needs no font to be installed.

    py make_icons.py

Writes logo.svg (source of truth) and icon-180/192/512.png next to itself.
Re-run after changing PALETTE or the geometry below.
"""
from pathlib import Path

INK    = "#1a1a1a"   # --ink      launcher masthead
PAPER  = "#f5f2eb"   # --paper    newsprint cream
ACCENT = "#c0392b"   # --accent   the red rule under the masthead

HERE = Path(__file__).parent

# Geometry as fractions of the canvas, so every size is identical.
SUN_CX, SUN_CY, SUN_R = 0.500, 0.395, 0.170
RULE_Y, RULE_H        = 0.520, 0.046
RULE_X0, RULE_X1      = 0.180, 0.820
LINES = [  # (x0, x1, centre_y)
    (0.235, 0.765, 0.655),
    (0.235, 0.620, 0.775),
]
LINE_H = 0.056

SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"
     width="512" height="512" role="img" aria-label="Morning Briefing">
  <rect width="512" height="512" fill="{INK}"/>
  <circle cx="{SUN_CX*512:.0f}" cy="{SUN_CY*512:.0f}" r="{SUN_R*512:.0f}" fill="{PAPER}"/>
  <rect x="{RULE_X0*512:.0f}" y="{(RULE_Y-RULE_H/2)*512:.0f}"
        width="{(RULE_X1-RULE_X0)*512:.0f}" height="{RULE_H*512:.0f}"
        rx="{RULE_H*512/2:.0f}" fill="{ACCENT}"/>
""" + "".join(
    f'  <rect x="{x0*512:.0f}" y="{(cy-LINE_H/2)*512:.0f}"'
    f' width="{(x1-x0)*512:.0f}" height="{LINE_H*512:.0f}"'
    f' rx="{LINE_H*512/2:.0f}" fill="{PAPER}"/>\n'
    for x0, x1, cy in LINES
) + "</svg>\n"


def _bar(draw, size, x0, x1, cy, h, fill):
    y0, y1 = (cy - h / 2) * size, (cy + h / 2) * size
    draw.rounded_rectangle([x0 * size, y0, x1 * size, y1],
                           radius=(y1 - y0) / 2, fill=fill)


def png(size: int, path: Path, ss: int = 4):
    """Render at ss× then downsample — cheap, reliable antialiasing."""
    from PIL import Image, ImageDraw
    s   = size * ss
    img = Image.new("RGB", (s, s), INK)
    d   = ImageDraw.Draw(img)
    r   = SUN_R * s
    d.ellipse([SUN_CX * s - r, SUN_CY * s - r,
               SUN_CX * s + r, SUN_CY * s + r], fill=PAPER)
    _bar(d, s, RULE_X0, RULE_X1, RULE_Y, RULE_H, ACCENT)
    for x0, x1, cy in LINES:
        _bar(d, s, x0, x1, cy, LINE_H, PAPER)
    img.resize((size, size), Image.LANCZOS).save(path, "PNG", optimize=True)
    return path


if __name__ == "__main__":
    (HERE / "logo.svg").write_text(SVG, encoding="utf-8")
    print(f"  wrote {HERE / 'logo.svg'}")
    for n in (180, 192, 512):
        p = png(n, HERE / f"icon-{n}.png")
        print(f"  wrote {p}  ({p.stat().st_size:,} bytes)")
