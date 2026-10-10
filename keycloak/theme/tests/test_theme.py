"""Rules the Sūveryn Keycloak theme must keep. Run: uv run --with pytest pytest keycloak/theme/tests

These check the sources in src/ (no Keycloak needed). The visual check against a running Keycloak
is described in ../README.md.
"""

import re
from pathlib import Path

import pytest

from oklch_contrast import ratio

THEME = Path(__file__).resolve().parents[1]
SRC = THEME / "src" / "suveryn"
TAGLINE = "AI for work that can''t leave the premises."  # '' is one apostrophe in Keycloak messages

MESSAGES = sorted(SRC.glob("*/messages/messages_*.properties"))
STYLES = sorted(SRC.glob("*/resources/css/*.css"))
TEMPLATES = sorted(SRC.rglob("*.ftl"))


def props(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_theme_extends_keycloaks_own_themes_and_skips_the_admin_console():
    assert props(SRC / "login" / "theme.properties")["parent"] == "keycloak.v2"
    assert props(SRC / "account" / "theme.properties")["parent"] == "keycloak.v3"
    assert props(SRC / "email" / "theme.properties")["parent"] == "keycloak"
    assert not (SRC / "admin").exists()
    for kind in ("login", "account"):
        assert props(SRC / kind / "theme.properties")["darkMode"] == "false"  # light only


@pytest.mark.parametrize("path", [p for p in MESSAGES if "suverynTagline" in p.read_text(encoding="utf-8")], ids=str)
def test_tagline_is_verbatim_in_every_language(path):
    assert props(path)["suverynTagline"] == TAGLINE


def test_never_private_ai():
    for path in [*MESSAGES, *TEMPLATES]:
        assert "Private AI" not in path.read_text(encoding="utf-8"), path


def test_air_gapped_badge_is_off_by_default_and_only_shown_when_enabled():
    assert props(SRC / "login" / "theme.properties")["suverynShowAirGappedBadge"] == "false"
    template = (SRC / "login" / "template.ftl").read_text()
    guarded = re.search(r'<#if \(properties\.suverynShowAirGappedBadge!"false"\) == "true">(.*?)</#if>', template, re.S)
    assert guarded and "suv-badge-airgapped" in guarded.group(1)
    assert template.count("suv-badge-airgapped") == 1  # nowhere outside the guard


def test_badge_matches_the_website_component():
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    rule = re.search(r"\.suv-badge-airgapped \{(.*?)\}", css, re.S).group(1)
    for decl in ("gap: 5px", "font-size: 10px", "font-weight: 600", "color: var(--suv-teal-ink)",
                 "background: rgba(58, 212, 202, 0.10)", "border: 1px solid rgba(58, 212, 202, 0.35)",
                 "border-radius: 20px", "padding: 3px 8px", "display: inline-flex"):
        assert decl in rule, decl
    svg = re.search(r'<span class="suv-badge-airgapped">\s*(<svg.*?</svg>)', (SRC / "login" / "template.ftl").read_text(), re.S).group(1)
    # the website's #0F8A82 darkened to #00837C (same hue) for 4.5:1, see test_text_contrast_by_oklch
    assert 'width="9" height="9"' in svg and 'stroke="#00837C"' in svg and 'stroke-width="2.4"' in svg
    assert svg.count("<path") == 7  # Lucide wifi-off


def test_no_white_text_on_the_accent():
    for path in STYLES:
        css = path.read_text()
        assert not re.search(r"(?<![-\w])color:\s*(#fff\b|#ffffff\b|white\b)", css, re.I), path
    login = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    primary = re.search(r"\.suv-card \.pf-v5-c-button\.pf-m-primary \{(.*?)\}", login, re.S).group(1)
    assert "--pf-v5-c-button--m-primary--Color: var(--suv-on-accent)" in primary
    assert "--suv-on-accent: #020205" in login
    account = (SRC / "account" / "resources" / "css" / "account.css").read_text()
    assert "--pf-v5-c-button--m-primary--Color: #020205" in account


def test_teal_focus_on_inputs():
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    focus = re.search(r"\.pf-v5-c-form-control:focus-within::before \{(.*?)\}", css, re.S).group(1)
    assert "border-color: var(--suv-teal-focus)" in focus and "box-shadow: 0 0 0 3px rgba(58, 212, 202, 0.15)" in focus
    assert "--suv-teal-focus: #0F8A82" in css


def test_nothing_is_loaded_from_the_internet():
    """No stylesheet, script, font, image or import from another host (links in comments don't load)."""
    loads = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//|@import\s+(?:url\()?["']?\s*(?:https?:)?//""", re.I)
    for path in [*STYLES, *TEMPLATES]:
        text = path.read_text()
        assert not loads.search(text), (path, loads.search(text))
        assert "googleapis" not in text and "gstatic" not in text


def test_ibm_plex_mono_only_for_the_wordmark():
    css = re.sub(r"/\*.*?\*/", "", (SRC / "login" / "resources" / "css" / "suveryn.css").read_text(), flags=re.S)
    uses = [line.strip() for line in css.splitlines() if "IBM Plex Mono" in line or "--suv-font-wordmark" in line]
    allowed = ("@font-face", "--suv-font-wordmark:", ".suv-logo text")
    assert uses and all(u.startswith(allowed) for u in uses), uses
    assert "IBM Plex Mono" not in (SRC / "account" / "resources" / "css" / "account.css").read_text()


INFORMAL = re.compile(r"(^|[ =(>'\"])([Jj]e|[Jj]ouw|[Jj]ij)(?=[ ,.?!:;)<]|$)")


@pytest.mark.parametrize("path", sorted(SRC.glob("*/messages/messages_nl.properties")), ids=str)
def test_dutch_is_formal(path):
    """Sūveryn's Dutch copy uses "u"/"uw", like Keycloak's own Dutch strings."""
    assert {k: v for k, v in props(path).items() if INFORMAL.search(v)} == {}


US = re.compile(r"(organiz|authoriz|recogniz|customiz|initializ|realiz|synchroniz|categoriz|utiliz|"
                r"\bcolor|\bcenter\b|\blicense\b|cancel(ed|ing)\b|favorite|behavior)", re.I)


@pytest.mark.parametrize("path", sorted(SRC.glob("*/messages/messages_en.properties")), ids=str)
def test_english_is_uk_english(path):
    assert {k: v for k, v in props(path).items() if US.search(v)} == {}


def test_directory_note_is_under_the_password_field_not_a_button():
    login = (SRC / "login" / "login.ftl").read_text()
    assert login.count('msg("suverynDirectoryHint")') == 2  # both password-field variants
    assert "suverynDirectoryHint" not in re.sub(r"<@field\.password.*?</@field\.password>", "", login, flags=re.S)


def test_built_theme_if_present():
    """After build_theme.py: the logotype is cropped, inlined for login, outlined for the account console."""
    out = THEME / "build" / "theme" / "suveryn"
    if not out.exists():
        pytest.skip("run build_theme.py first")
    logo = (out / "login" / "suveryn-logo.ftl").read_text()
    view = [float(v) for v in re.search(r'viewBox="([^"]+)"', logo).group(1).split()]
    assert view[0] >= 0 and view[2] < 130 and 20 < view[3] < 26  # the original canvas was 0 0 220 40
    assert "<text" in logo and "c2pa" not in logo
    outlined = (out / "account" / "resources" / "suveryn-logotype.svg").read_text()
    assert "<text" not in outlined and outlined.count("<path") >= 7 and 'height="48"' in outlined
    assert outlined.count("<rect") == 3  # the icon is part of the account console logo (suveryn-tracker#1)
    for font in ("Inter-Variable.woff2", "SpaceGrotesk-Variable.woff2", "IBMPlexMono-Medium.woff2"):
        assert (out / "login" / "resources" / "fonts" / font).stat().st_size > 10_000
    notices = (out / "THIRD-PARTY-NOTICES.txt").read_text()
    assert "SIL Open Font" in notices and "Lucide" in notices and "TRADEMARK_POLICY" in notices


def css_vars(path: Path) -> dict[str, str]:
    return dict(re.findall(r"(--suv-[\w-]+):\s*([^;]+);", path.read_text()))


def test_text_contrast_by_oklch():
    """Every text colour meets 4.5:1 (and UI boundaries 3:1), measured from OKLCH lightness."""
    v = css_vars(SRC / "login" / "resources" / "css" / "suveryn.css")
    card, page = v["--suv-card"], v["--suv-bg"]
    wash = "rgba(58, 212, 202, 0.10)"
    pairs = [
        (v["--suv-text"], card, 4.5), (v["--suv-text-dim"], card, 4.5), (v["--suv-text-dim"], page, 4.5),
        (v["--suv-accent-ink"], card, 4.5), (v["--suv-accent-ink"], page, 4.5),
        (v["--suv-accent-ink-hover"], card, 4.5),
        (v["--suv-on-accent"], v["--suv-accent"], 4.5), (v["--suv-on-accent"], v["--suv-accent-hover"], 4.5),
        (v["--suv-teal-ink"], wash, 4.5), (v["--suv-teal-focus"], page, 3.0),
    ]
    failing = [(fg, bg, round(ratio(fg, bg), 2)) for fg, bg, need in pairs if ratio(fg, bg) < need]
    assert failing == []
    # the reference point: white on the accent fails, which is why buttons use ink
    assert round(ratio("#FFFFFF", v["--suv-accent"]), 1) == 2.9


def test_no_text_in_the_plain_accent_or_tertiary_colour():
    login = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    assert not re.search(r"(?<![-\w])color:\s*var\(--suv-(accent|accent-hover|text-dimmer)\)", login)
    account = (SRC / "account" / "resources" / "css" / "account.css").read_text()
    assert "--pf-v5-global--link--Color: #CD3200" in account
    assert ".suv-body a { color: #CD3200" in (SRC / "email" / "html" / "template.ftl").read_text()


MOJIBAKE = re.compile("Ã|Å|â€|Â")  # UTF-8 read as Latin-1/Windows-1252, e.g. "SÅ«veryn" for "Sūveryn"


def test_sources_are_utf8_without_mojibake():
    """suveryn-tracker#1: every theme file is valid UTF-8 and the macron is never double-encoded."""
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and path.suffix in {".properties", ".ftl", ".css"}:
            text = path.read_bytes().decode("utf-8")  # raises if not UTF-8
            assert not MOJIBAKE.search(text), path
    assert "Sūveryn" in (SRC / "email" / "messages" / "messages_nl.properties").read_text(encoding="utf-8")


def test_email_header_is_the_lockup_icon_and_wordmark():
    """suveryn-tracker#1: the email header shows the brand icon next to the wordmark."""
    template = (SRC / "email" / "html" / "template.ftl").read_text()
    assert '<#include "suveryn-icon.ftl">' in template and "s&#363;veryn" in template
    assert '<meta charset="utf-8">' in template
    built = THEME / "build" / "theme" / "suveryn" / "email" / "html" / "suveryn-icon.ftl"
    if built.exists():
        icon = built.read_text()
        assert 'width="30" height="30"' in icon and icon.count("<rect") == 3 and "c2pa" not in icon


def test_product_name_is_lower_case_mid_sentence():
    """"sūveryn" takes a capital only for the company or at the start of a sentence (or a subject)."""
    for path in MESSAGES:
        for key, value in props(path).items():
            for m in re.finditer("Sūveryn", value):
                before = re.sub(r"<[^>]+>", "", value[:m.start()]).strip()
                assert before == "" or before[-1] in ".!?:", (path.name, key, value)
