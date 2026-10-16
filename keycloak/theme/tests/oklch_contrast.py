"""Contrast the way the Sūveryn Design System's own audit measures it: from OKLCH lightness.

Relative luminance is taken as L³, where L is the colour's OKLCH (OKLab) lightness, and the ratio
is WCAG's (Y1 + 0.05) / (Y2 + 0.05). This reproduces the audit's white-on-accent figure (2.88:1
with L = 0.68). Thresholds are WCAG AA: 4.5:1 for text, 3:1 for large text and UI components.

Also converts OKLCH back to sRGB, to darken a colour by lowering L while keeping its chroma and hue
(how the Design System derived teal-ink from teal).
"""

import math
import re


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def parse(color: str, over: str = "#FFFFFF", vars: dict[str, str] | None = None) -> tuple[float, float, float]:
    """sRGB 0..1 of "#RRGGBB", "rgba(r, g, b, a)", "oklch(L C h)", "oklch(L C h / a)" or
    "oklch(from var(--x) l c h / a)" (``vars`` resolving the variable), alpha-blended over ``over``."""
    color = color.strip()
    if color.startswith("#"):
        return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    if color.startswith("oklch("):
        inner = color[6:-1].strip()
        alpha = 1.0
        if "/" in inner:
            inner, a = inner.rsplit("/", 1)
            alpha = float(a)
        if inner.startswith("from "):
            m = re.fullmatch(r"from\s+var\((--[\w-]+)\)\s+l\s+c\s+h\s*", inner)
            if not m or not vars or m.group(1) not in vars:
                raise ValueError(f"can't resolve {color}")
            rgb = parse(vars[m.group(1)], over, vars)
        else:
            L, C, h = (float(x) for x in inner.split())
            hexed = to_hex(L, C, h)
            if hexed is None:
                raise ValueError(f"{color} is outside sRGB")
            rgb = parse(hexed)
        base = parse(over)
        return tuple(alpha * c + (1 - alpha) * bc for c, bc in zip(rgb, base, strict=True))
    r, g, b, a = (float(x) for x in color[color.index("(") + 1:-1].split(","))
    base = parse(over)
    return tuple(a * c / 255 + (1 - a) * bc for c, bc in zip((r, g, b), base, strict=True))


def oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (_lin(c) for c in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklch(color: str, over: str = "#FFFFFF") -> tuple[float, float, float]:
    L, a, b = oklab(parse(color, over))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def ratio(fg: str, bg: str, page: str = "#FFFFFF", vars: dict[str, str] | None = None) -> float:
    """Contrast of text ``fg`` on ``bg`` (both may be translucent; bg sits on ``page``)."""
    bg_rgb = parse(bg, page, vars)
    bg_hex = "#" + "".join(f"{round(c * 255):02X}" for c in bg_rgb)
    y1, y2 = (oklab(parse(c, bg_hex, vars))[0] ** 3 for c in (fg, bg_hex))
    hi, lo = max(y1, y2), min(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


def to_hex(L: float, C: float, h: float) -> str | None:
    """sRGB hex of an OKLCH colour, or None if it is outside the sRGB gamut."""
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_, m_, s_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3, (L - 0.1055613458 * a - 0.0638541728 * b) ** 3, \
        (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)
    if any(c < -1e-4 or c > 1 + 1e-4 for c in rgb):
        return None
    return "#" + "".join(f"{round(min(1, max(0, _gamma(c))) * 255):02X}" for c in rgb)


def in_gamut(L: float, C: float, h: float) -> str:
    """The sRGB colour with lightness L and hue h, keeping as much of chroma C as sRGB can show."""
    if (hexed := to_hex(L, C, h)) is not None:
        return hexed
    lo, hi = 0.0, C
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if to_hex(L, mid, h) is not None else (lo, mid)
    return to_hex(L, lo, h)


def darken_to(color: str, bg: str, target: float = 4.5) -> str:
    """The lightest colour with ``color``'s hue (and as much of its chroma as fits) reaching ``target`` on ``bg``."""
    L, C, h = oklch(color)
    while L > 0:
        hexed = in_gamut(L, C, h)
        if ratio(hexed, bg) >= target:
            return hexed
        L -= 0.001
    raise ValueError("no such colour")


def alpha_for(ink: str, bg: str, target: float = 4.5) -> float:
    """The smallest opacity of ``ink`` (an rgb triple as "r, g, b") that reaches ``target`` on ``bg``."""
    a = 0.0
    while a <= 1:
        if ratio(f"rgba({ink}, {a:.2f})", bg) >= target:
            return round(a, 2)
        a += 0.01
    raise ValueError("unreachable")
