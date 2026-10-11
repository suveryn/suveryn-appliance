# /// script
# requires-python = ">=3.11"
# dependencies = ["fonttools==4.66.1"]
# ///
"""Build the Sūveryn Keycloak theme: sources from src/, brand assets from suveryn-brand.

    uv run keycloak/theme/build_theme.py            # build at the commit in brand-lock.json
    uv run keycloak/theme/build_theme.py --update <commit-or-tag>   # move to another brand commit

Output (build/, not committed):
    build/theme/suveryn/                 the theme folder (login, account, email)
    build/suveryn-keycloak-theme.jar     the same, packaged for Keycloak's providers/ folder

Brand assets are fetched at build time, never committed here: the Sūveryn mark is all rights
reserved (suveryn-brand's TRADEMARK_POLICY.md), while this repository is Apache-2.0. The fonts are
SIL Open Font Licence and travel with their licence texts. Every fetched file must match the
SHA-256 in brand-lock.json, so a build is reproducible and a changed asset is noticed.

The logotype (logo/suveryn-logotype-light-bg.svg) has blank canvas to the right of the wordmark.
Its viewBox is cropped to the drawn content, computed from the shapes and from IBM Plex Mono's own
glyph metrics; the shapes and text are not changed. Two forms are written:
  - login: the SVG inlined in the page, with the wordmark converted to outlines (paths) from IBM Plex
    Mono, so it needs no web font: as live <text> it flashed in a fallback font on every page load,
    e.g. each language switch (suveryn-tracker#13);
  - account console: the same outlined SVG as a file, because it shows its logo as an <img>.
The email header uses logo/suveryn-icon.svg (inlined, 30px) next to the wordmark set as text.
The C2PA provenance metadata is dropped: it describes the original file, which cropping changes.

Set SUVERYN_BRAND_REPO to a local mirror to build without internet access.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import defusedxml.ElementTree as DefusedET

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).resolve().parent
SRC = HERE / "src" / "suveryn"
LOCK = HERE / "brand-lock.json"
BUILD = HERE / "build"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# suveryn-brand path -> where it goes in the theme (relative to build/theme/suveryn)
ASSETS = {
    "logo/suveryn-logotype-light-bg.svg": None,  # processed, see logotype()
    "logo/suveryn-icon.svg": None,  # processed, see email_icon()
    "logo/favicon.svg": ["login/resources/img/favicon.svg", "account/resources/favicon.svg"],
    "fonts/Inter/Inter-Variable.woff2": ["login/resources/fonts/Inter-Variable.woff2", "account/resources/fonts/Inter-Variable.woff2"],
    "fonts/SpaceGrotesk/SpaceGrotesk-Variable.woff2": ["login/resources/fonts/SpaceGrotesk-Variable.woff2", "account/resources/fonts/SpaceGrotesk-Variable.woff2"],
    "fonts/IBMPlexMono/IBMPlexMono-Medium.woff2": None,  # not shipped: the wordmark is outlined
    "fonts/IBMPlexMono/IBMPlexMono-Medium.ttf": None,  # glyph metrics and outlines, not shipped
    "fonts/Inter/OFL.txt": None,
    "fonts/SpaceGrotesk/OFL.txt": None,
    "fonts/IBMPlexMono/OFL.txt": None,
}


def fetch_brand(repo: str, ref: str, into: Path) -> str:
    """Shallow-fetch suveryn-brand at ``ref`` into ``into``; return the commit id."""
    if not re.fullmatch(r"[\w][\w./-]*", ref):
        raise SystemExit(f"not a commit, tag or branch name: {ref}")
    if repo.startswith("-"):  # git would read it as an option (e.g. --upload-pack=<command>)
        raise SystemExit(f"not a repository: {repo}")
    git = lambda *a: subprocess.run(["git", "-C", str(into), *a], check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    git("init", "-q")
    git("fetch", "-q", "--depth", "1", repo, ref)
    git("checkout", "-q", "FETCH_HEAD")
    return git("rev-parse", "HEAD")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_layout(font: TTFont, text: str, x: float, y: float, size: float):
    """Glyph names with their pen positions for ``text`` set at (x, baseline y), plus its bounds."""
    cmap, hmtx, glyphs = font.getBestCmap(), font["hmtx"], font.getGlyphSet()
    scale = size / font["head"].unitsPerEm
    placed, pen_x, box = [], x, [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for ch in text:
        name = cmap[ord(ch)]
        bp = BoundsPen(glyphs)
        glyphs[name].draw(bp)
        if bp.bounds:
            x0, y0, x1, y1 = bp.bounds
            box = [min(box[0], pen_x + x0 * scale), min(box[1], y - y1 * scale),
                   max(box[2], pen_x + x1 * scale), max(box[3], y - y0 * scale)]
        placed.append((name, pen_x))
        pen_x += hmtx[name][0] * scale
    return placed, scale, box


def logotype(svg_path: Path, font: TTFont) -> tuple[str, str, tuple]:
    """(inline SVG for the login page, SVG file for the account console, viewBox); both outlined."""
    root = ET.parse(svg_path).getroot()
    for meta in root.findall(f"{{{SVG_NS}}}metadata"):
        root.remove(meta)
    box = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    texts = []
    for el in root:
        tag = el.tag.split("}")[1]
        if tag == "rect":
            half = float(el.get("stroke-width", 0)) / 2 if el.get("stroke", "none") != "none" else 0
            x, y, w, h = (float(el.get(k)) for k in ("x", "y", "width", "height"))
            box = [min(box[0], x - half), min(box[1], y - half), max(box[2], x + w + half), max(box[3], y + h + half)]
        elif tag == "text":
            if "IBM Plex Mono" not in el.get("font-family", "") or el.get("font-weight") != "500":
                raise SystemExit("the logotype's wordmark is no longer IBM Plex Mono 500; update build_theme.py")
            placed, scale, tb = text_layout(font, el.text, float(el.get("x")), float(el.get("y")), float(el.get("font-size")))
            box = [min(box[0], tb[0]), min(box[1], tb[1]), max(box[2], tb[2]), max(box[3], tb[3])]
            texts.append((el, placed, scale))
        else:
            raise SystemExit(f"unexpected <{tag}> in the logotype; update build_theme.py")
    x0, y0 = (round(v - 0.005, 2) for v in box[:2])
    x1, y1 = (round(v + 0.005, 2) for v in box[2:])
    view = (x0, y0, round(x1 - x0, 2), round(y1 - y0, 2))
    root.set("viewBox", " ".join(f"{v:g}" for v in view))
    for attr in ("width", "height"):
        root.attrib.pop(attr, None)
    root.attrib.pop("xmlns:c2pa", None)
    root.set("role", "img")
    root.set("aria-label", "Sūveryn")

    glyphs = font.getGlyphSet()
    for el, placed, scale in texts:  # outline the wordmark: no web font needed, so nothing can flash
        g = ET.Element(f"{{{SVG_NS}}}g", {"fill": el.get("fill", "#020205")})
        for name, pen_x in placed:
            pen = SVGPathPen(glyphs)
            glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, pen_x, float(el.get("y")))))
            if pen.getCommands():
                ET.SubElement(g, f"{{{SVG_NS}}}path", {"d": pen.getCommands()})
        index = list(root).index(el)
        root.remove(el)
        root.insert(index, g)
    inline = root.__copy__()
    inline.set("class", "suv-logo")
    inline_svg = ET.tostring(inline, encoding="unicode")
    root.set("height", "48")
    root.set("width", f"{48 * view[2] / view[3]:.1f}")
    outlined_svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
    return inline_svg, outlined_svg, view


def email_icon(svg_path: Path) -> str:
    """The brand icon for the email header: the asset's own shapes, at the lockup's 30px.

    Inline SVG, so nothing is fetched. Clients that strip SVG (Gmail, Outlook for Windows) show the
    text wordmark next to it on its own, which is the design without the icon.
    """
    root = DefusedET.parse(svg_path).getroot()
    for meta in root.findall(f"{{{SVG_NS}}}metadata"):
        root.remove(meta)
    root.attrib.pop("xmlns:c2pa", None)
    root.set("width", "30")
    root.set("height", "30")
    root.set("role", "img")
    root.set("aria-label", "Sūveryn")
    root.set("style", "display:block")
    return ET.tostring(root, encoding="unicode")


def notices(brand: Path) -> str:
    parts = ["Third-party material in the Sūveryn Keycloak theme.\n",
             ("The Sūveryn mark (logotype, favicon) is not open source: all rights reserved, see\n"
              "https://github.com/suveryn/suveryn-brand/blob/main/TRADEMARK_POLICY.md\n")]
    for family, folder in (("Inter", "Inter"), ("Space Grotesk", "SpaceGrotesk"), ("IBM Plex Mono", "IBMPlexMono")):
        parts.append(f"{'=' * 72}\n{family} font (SIL Open Font Licence 1.1)\n{'=' * 72}\n{(brand / 'fonts' / folder / 'OFL.txt').read_text().strip()}\n")
    parts.append(f"{'=' * 72}\nLucide icons: eye, eye-off, wifi-off, languages (ISC; Feather-derived icons MIT)\n{'=' * 72}\n{(HERE / 'LUCIDE-LICENSE.txt').read_text().strip()}\n")
    return "\n".join(parts)


JAR_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest zip date: entries carry no build time


def write_jar(theme: Path, jar: Path) -> None:
    """Package ``theme`` (build/theme/suveryn) for Keycloak's providers/ folder.

    Byte-for-byte reproducible: fixed entry order, dates and permissions, so the same sources and
    brand commit give the same jar, and an installed theme can be checked against a build.
    """
    def add(z: zipfile.ZipFile, name: str, data: bytes) -> None:
        info = zipfile.ZipInfo(name, JAR_TIME)
        info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
        z.writestr(info, data)

    with zipfile.ZipFile(jar, "w") as z:
        add(z, "META-INF/keycloak-themes.json", json.dumps(
            {"themes": [{"name": "suveryn", "types": ["login", "account", "email"]}]}, indent=2).encode())
        for f in sorted(theme.rglob("*")):
            if f.is_file():
                add(z, f"theme/suveryn/{f.relative_to(theme).as_posix()}", f.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", metavar="REF", help="move brand-lock.json to this suveryn-brand commit, tag or branch")
    a = ap.parse_args()
    lock = json.loads(LOCK.read_text())
    repo = os.environ.get("SUVERYN_BRAND_REPO", lock["repo"])
    with tempfile.TemporaryDirectory(prefix="suveryn-brand-") as tmp:  # public brand assets only
        brand = Path(tmp)
        commit = fetch_brand(repo, a.update or lock["commit"], brand)
        hashes = {p: sha256(brand / p) for p in ASSETS}
        if a.update:
            lock = {"repo": lock["repo"], "commit": commit, "files": hashes}
            LOCK.write_text(json.dumps(lock, indent=2) + "\n")
        elif hashes != lock["files"] or commit != lock["commit"]:
            changed = [p for p in ASSETS if hashes[p] != lock["files"].get(p)]
            raise SystemExit(f"brand assets differ from brand-lock.json: {changed or commit}")

        out = BUILD / "theme" / "suveryn"
        shutil.rmtree(BUILD, ignore_errors=True)
        shutil.copytree(SRC, out)
        for src, dests in ASSETS.items():
            for dest in dests or []:
                (out / dest).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(brand / src, out / dest)
        font = TTFont(brand / "fonts/IBMPlexMono/IBMPlexMono-Medium.ttf")
        inline, outlined, view = logotype(brand / "logo/suveryn-logotype-light-bg.svg", font)
        (out / "login" / "suveryn-logo.ftl").write_text(
            "<#-- Generated by build_theme.py from suveryn-brand logo/suveryn-logotype-light-bg.svg "
            f"({commit[:7]}); viewBox cropped to the drawn content. Not open source: see TRADEMARK_POLICY.md. -->\n"
            f"<#noparse>{inline}</#noparse>\n")
        (out / "account" / "resources" / "suveryn-logotype.svg").write_text(outlined)
        (out / "email" / "html" / "suveryn-icon.ftl").write_text(
            "<#-- Generated by build_theme.py from suveryn-brand logo/suveryn-icon.svg "
            f"({commit[:7]}); shapes untouched. Not open source: see TRADEMARK_POLICY.md. -->\n"
            f"<#noparse>{email_icon(brand / 'logo/suveryn-icon.svg')}</#noparse>\n")
        (out / "THIRD-PARTY-NOTICES.txt").write_text(notices(brand))

    jar = BUILD / "suveryn-keycloak-theme.jar"
    write_jar(out, jar)
    print(f"theme built from suveryn-brand@{commit[:7]}: logotype viewBox {view}, {jar.relative_to(HERE)}")


if __name__ == "__main__":
    main()
