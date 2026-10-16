"""Rules the sūveryn Keycloak theme must keep. Run: uv run --with pytest pytest keycloak/theme/tests

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


def test_air_gapped_badge_shows_only_when_the_network_mode_is_air_gapped():
    """The mode comes from Keycloak's environment (its unit loads /etc/suveryn/network-mode.env); a missing
    value reads as unknown, never as air-gapped. Checked against Keycloak 26.8: theme properties take
    ${env.NAME:default}."""
    assert props(SRC / "login" / "theme.properties")["suverynNetworkMode"] == "${env.SUVERYN_NETWORK_MODE:unknown}"
    template = (SRC / "login" / "template.ftl").read_text()
    guarded = re.search(r'<#if \(properties\.suverynNetworkMode!"unknown"\) == "air-gapped">(.*?)</#if>', template, re.S)
    assert guarded and "suv-badge-airgapped" in guarded.group(1)
    assert template.count("suv-badge-airgapped") == 1  # nowhere outside the guard
    assert "suverynShowAirGappedBadge" not in template
    unit = (SRC.parents[2].parent / "provision" / "systemd" / "keycloak.service").read_text()
    assert "EnvironmentFile=-/etc/suveryn/network-mode.env" in unit


def test_badge_matches_the_website_component():
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    rule = re.search(r"\.suv-badge-airgapped \{(.*?)\}", css, re.S).group(1)
    for decl in ("gap: 5px", "font-size: 10px", "font-weight: 600", "color: var(--suv-teal-ink)",
                 "background: oklch(from var(--suv-teal) l c h / 0.10)", "border: 1px solid oklch(from var(--suv-teal) l c h / 0.35)",
                 "border-radius: 20px", "padding: 3px 8px", "display: inline-flex"):
        assert decl in rule, decl
    svg = re.search(r'<span class="suv-badge-airgapped">\s*(<svg.*?</svg>)', (SRC / "login" / "template.ftl").read_text(), re.S).group(1)
    # the brand's teal-ink (tokens v2), which holds 4.5:1 on the wash; see test_text_contrast_by_oklch
    assert 'width="9" height="9"' in svg and 'stroke="currentColor"' in svg  # the badge's teal-ink, from CSS and 'stroke-width="2.4"' in svg
    assert svg.count("<path") == 7  # Lucide wifi-off


def test_no_white_text_on_the_accent():
    for path in STYLES:
        css = path.read_text()
        assert not re.search(r"(?<![-\w])color:\s*(#fff\b|#ffffff\b|white\b)", css, re.I), path
    login = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    primary = re.search(r"\.suv-card \.pf-v5-c-button\.pf-m-primary \{(.*?)\}", login, re.S).group(1)
    assert "--pf-v5-c-button--m-primary--Color: var(--suv-on-accent)" in primary
    ink = "oklch(0.0885 0.0139 283.18)"  # #020205, the brand's ink, as tokens v3 writes it
    assert f"--suv-on-accent: {ink}" in login
    account = (SRC / "account" / "resources" / "css" / "account.css").read_text()
    assert f"--pf-v5-c-button--m-primary--Color: {ink}" in account


def test_teal_focus_on_inputs():
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    focus = re.search(r"\.pf-v5-c-form-control:focus-within::before \{(.*?)\}", css, re.S).group(1)
    assert "border-color: var(--suv-teal-focus)" in focus and "box-shadow: 0 0 0 3px oklch(from var(--suv-teal) l c h / 0.15)" in focus
    assert "--suv-teal-focus: oklch(0.5719 0.0969 187.53)" in css  # #0F8A82


def test_nothing_is_loaded_from_the_internet():
    """No stylesheet, script, font, image or import from another host (links in comments don't load)."""
    loads = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//|@import\s+(?:url\()?["']?\s*(?:https?:)?//""", re.I)
    for path in [*STYLES, *TEMPLATES]:
        text = path.read_text()
        assert not loads.search(text), (path, loads.search(text))
        assert "googleapis" not in text and "gstatic" not in text


def test_ibm_plex_mono_only_for_the_wordmark_and_drawn_as_outlines():
    """suveryn-tracker#13: the wordmark as live text flashed in a fallback font on every page load.
    It is outlined at build time, so the login page loads no IBM Plex Mono at all."""
    css = re.sub(r"/\*.*?\*/", "", (SRC / "login" / "resources" / "css" / "suveryn.css").read_text(), flags=re.S)
    assert "IBM Plex Mono" not in css and "--suv-font-wordmark" not in css
    assert "IBM Plex Mono" not in (SRC / "account" / "resources" / "css" / "account.css").read_text()


INFORMAL = re.compile(r"(^|[ =(>'\"])([Jj]e|[Jj]ouw|[Jj]ij)(?=[ ,.?!:;)<]|$)")


@pytest.mark.parametrize("path", sorted(SRC.glob("*/messages/messages_nl.properties")), ids=str)
def test_dutch_is_formal(path):
    """Sūveryn's Dutch copy uses "u"/"uw", like Keycloak's own Dutch strings."""
    assert {k: v for k, v in props(path).items() if INFORMAL.search(v)} == {}


@pytest.mark.parametrize("theme", ["login", "account", "email"])
def test_every_theme_has_english_dutch_and_french(theme):
    """suveryn-tracker#9: the account theme had English only; each theme ships all three languages."""
    assert {p.name for p in (SRC / theme / "messages").glob("messages_*.properties")} >= {
        "messages_en.properties", "messages_nl.properties", "messages_fr.properties"}
    assert "locales=en,nl,fr" in (SRC / theme / "theme.properties").read_text()


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
    assert "<text" not in logo and logo.count("<path") >= 7 and "c2pa" not in logo  # outlined: no font, no flash
    outlined = (out / "account" / "resources" / "suveryn-logotype.svg").read_text()
    assert "<text" not in outlined and outlined.count("<path") >= 7 and 'height="48"' in outlined
    assert outlined.count("<rect") == 3  # the icon is part of the account console logo (suveryn-tracker#1)
    for font in ("Inter-Variable.woff2", "SpaceGrotesk-Variable.woff2"):
        assert (out / "login" / "resources" / "fonts" / font).stat().st_size > 10_000
    assert not (out / "login" / "resources" / "fonts" / "IBMPlexMono-Medium.woff2").exists()  # not needed any more
    notices = (out / "THIRD-PARTY-NOTICES.txt").read_text()
    assert "SIL Open Font" in notices and "Lucide" in notices and "TRADEMARK_POLICY" in notices


def css_vars(path: Path) -> dict[str, str]:
    return dict(re.findall(r"(--suv-[\w-]+):\s*([^;]+);", path.read_text()))


def test_text_contrast_by_oklch():
    """Every text colour meets 4.5:1 (and UI boundaries 3:1), measured from OKLCH lightness."""
    v = css_vars(SRC / "login" / "resources" / "css" / "suveryn.css")
    card, page = v["--suv-card"], v["--suv-bg"]
    wash = "oklch(from var(--suv-teal) l c h / 0.10)"  # the badge's background
    pairs = [
        (v["--suv-text"], card, 4.5), (v["--suv-text-dim"], card, 4.5), (v["--suv-text-dim"], page, 4.5),
        (v["--suv-text-dimmer"], card, 4.5),
        (v["--suv-accent-ink"], card, 4.5), (v["--suv-accent-ink"], page, 4.5),
        (v["--suv-accent-ink-hover"], card, 4.5),
        (v["--suv-on-accent"], v["--suv-accent"], 4.5), (v["--suv-on-accent"], v["--suv-accent-hover"], 4.5),
        (v["--suv-teal-ink"], wash, 4.5), (v["--suv-teal-focus"], page, 3.0),
    ]
    failing = [(fg, bg, round(ratio(fg, bg, vars=v), 2)) for fg, bg, need in pairs if ratio(fg, bg, vars=v) < need]
    assert failing == []
    # the reference point: white on the accent fails, which is why buttons use ink
    assert round(ratio("#FFFFFF", v["--suv-accent"]), 1) == 2.9


def test_no_text_in_the_plain_accent_or_tertiary_colour():
    login = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    assert not re.search(r"(?<![-\w])color:\s*var\(--suv-(accent|accent-hover)\)", login)
    account = (SRC / "account" / "resources" / "css" / "account.css").read_text()
    assert "--pf-v5-global--link--Color: oklch(0.5382 0.1909 34.55)" in account  # #C42F00
    # the email keeps hex: email clients don't draw oklch()
    assert ".suv-body a { color: #C42F00" in (SRC / "email" / "html" / "template.ftl").read_text()


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


def test_notices_are_neutral_with_a_brand_signal():
    """suveryn-tracker#2: no PatternFly amber/red/blue/green banners; warnings and errors use accent-ink."""
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    alert = re.search(r"\.suv-card \.pf-v5-c-alert \{(.*?)\}", css, re.S).group(1)
    assert "var(--suv-surface-alt)" in alert and "color: var(--suv-text)" in alert
    signal = re.search(r"\.pf-m-warning, \.suv-card \.pf-v5-c-alert\.pf-m-danger \{(.*?)\}", css, re.S).group(1)
    assert "var(--suv-accent-ink)" in signal
    props_ = props(SRC / "login" / "theme.properties")
    for kind in ("Error", "Warning", "Success", "Info"):
        assert props_[f"kcFeedback{kind}Icon"].startswith("suv-icon suv-icon-")  # Lucide, not Font Awesome
    v = css_vars(SRC / "login" / "resources" / "css" / "suveryn.css")
    assert ratio(v["--suv-text"], v["--suv-surface-alt"], vars=v) >= 4.5
    assert ratio(v["--suv-accent-ink"], v["--suv-surface-alt"], vars=v) >= 4.5  # icon and bar
    assert ratio(v["--suv-accent"], v["--suv-surface-alt"], vars=v) < 3  # why the plain accent isn't used here


def test_field_errors_use_the_brand_not_a_red():
    """No colour outside the brand: field errors use accent-ink, with an icon and message as the signal."""
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    assert "#C9190B" not in css and not re.search(r"#[cC][0-9a-fA-F]19|danger--Color", css)
    assert ".pf-v5-c-form-control.pf-m-error::before { border-color: var(--suv-accent-ink); }" in css
    assert "pf-m-error .pf-v5-c-helper-text__item-text { color: var(--suv-accent-ink); }" in css
    assert props(SRC / "login" / "theme.properties")["kcInputErrorIconClass"] == "suv-icon suv-icon-alert-circle"


@pytest.mark.parametrize("lang, link", [("nl", "Terug naar de applicatie"), ("fr", "Retour à l''application")])
def test_error_pages_name_the_back_link_as_the_link_reads(lang, link):
    """Keycloak's Dutch and French error texts named "Back to application" differently from the link itself."""
    msgs = props(SRC / "login" / "messages" / f"messages_{lang}.properties")
    assert msgs["backToApplication"] == f"« {link}"
    assert f"Klik op {link}" in msgs["cookieNotFoundMessage"] or f"Cliquez sur {link}" in msgs["cookieNotFoundMessage"]

def test_language_menu_has_the_lucide_languages_icon_as_in_the_chat_ui():
    """The chat UI shows Lucide "languages" before its language menu; the login page does too."""
    template = (SRC / "login" / "template.ftl").read_text()
    utilities = template[template.index("suv-language"):template.index('id="login-select-toggle"')]
    svg = utilities[utilities.index("<svg"):utilities.index("</svg>")]
    assert 'aria-hidden="true"' in svg and 'stroke="currentColor"' in svg and svg.count("<path") == 6
    assert 'd="m22 22-5-10-5 10"' in svg  # the "A" of Lucide's languages glyph


def test_language_menu_names_each_language_in_itself_with_a_fixed_width():
    """Switching nl/fr made the menu jump: Keycloak's labels change length with the page language."""
    template = (SRC / "login" / "template.ftl").read_text()
    assert '"en": "English", "nl": "Nederlands", "fr": "Français"' in template and '["en", "nl", "fr"]' in template
    assert "${l.label}" not in template.split("suvNames[l.languageTag]??")[0].split("suvNames =")[1]  # en/nl/fr: own names only
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    assert ".suv-card .suv-language .pf-v5-c-form-control { width: 9.5rem; }" in css


def test_login_page_is_anchored_to_the_top_so_nothing_moves_when_the_language_changes():
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    page = css[css.index(".suv-page {"):css.index("}", css.index(".suv-page {"))]
    assert "justify-content: flex-start" in page and "justify-content: center" not in page


def test_password_field_and_eye_button_are_one_box_with_a_centred_icon():
    """The eye sat 3 px high (inline on the text baseline), and focus turned only the text part teal."""
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    assert "display: inline-flex; align-items: center; justify-content: center;" in css  # icon centred in the button
    assert ".pf-v5-c-input-group:has(.pf-m-control):focus-within .pf-v5-c-button.pf-m-control::after { border-color: var(--suv-teal-focus); }" \
        in css.replace(",\n.suv-card .pf-v5-c-input-group:has(.pf-m-control):focus-within .pf-v5-c-button", "\n.suv-card .pf-v5-c-input-group:has(.pf-m-control):focus-within .pf-v5-c-button") \
        or "focus-within .pf-v5-c-button.pf-m-control::after { border-color: var(--suv-teal-focus); }" in css
    assert ".pf-v5-c-button.pf-m-control::after { border-left-width: 0; }" in css  # no line between field and button

def test_language_menu_has_a_fixed_spot_whatever_the_title():
    """suveryn-tracker#13: next to the title the menu wrapped under longer titles ("Connexion") or with a
    larger browser font, so it moved on a language switch. It is pinned to the card's top right."""
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    header = css[css.index(".suv-card .pf-v5-c-login__main-header {"):]
    header = header[:header.index("}")]
    assert "position: relative" in header and "display: block" in header and "9.5rem" in header
    assert ".suv-card .pf-v5-c-login__main-header .suv-language { position: absolute; top: 26px; right: 28px; }" in css


def test_fonts_are_preloaded_and_never_swapped_in_late():
    """suveryn-tracker#13: with font-display swap the tagline flashed (and re-wrapped) on every page load."""
    css = (SRC / "login" / "resources" / "css" / "suveryn.css").read_text()
    faces = re.findall(r"@font-face \{[^}]*\}", css)
    assert faces and all("font-display: block" in f for f in faces)
    template = (SRC / "login" / "template.ftl").read_text()
    for font in ("Inter-Variable.woff2", "SpaceGrotesk-Variable.woff2"):
        assert f'<link rel="preload" href="${{url.resourcesPath}}/fonts/{font}" as="font" type="font/woff2" crossorigin>' in template
        assert f'url("../fonts/{font}")' in css  # the same file the stylesheet uses (css/ is one level down)
