"""The reverse proxy's nginx configuration: the rules that are easy to lose in an edit. Any machine:

    python3 -m pytest reverse-proxy/tests

The running proxy (TLS, headers on real answers, sign-in) is checked by ``suveryn-provision verify``.
"""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SITE = (HERE / "suveryn.conf.template").read_text()
CSP = (HERE / "suveryn-csp.conf").read_text()
HEADERS = (HERE / "suveryn-headers.conf").read_text()


def locations() -> dict[str, str]:
    """Each location block of the https server: {pattern: body}."""
    return {m.group(1).strip(): m.group(2) for m in re.finditer(r"location\s+([^{]+)\{(.*?)\n    \}", SITE, re.S)}


def test_every_location_with_its_own_headers_keeps_the_security_headers():
    """nginx drops the server's add_header lines in a location that has any of its own."""
    for pattern, body in locations().items():
        if "add_header" in body or "include /etc/nginx/snippets/suveryn-csp.conf" in body:
            assert "include /etc/nginx/snippets/suveryn-headers.conf;" in body, pattern


def test_the_ui_and_the_api_get_the_strict_csp_and_keycloak_keeps_its_own():
    for pattern, body in locations().items():
        uses_csp = "suveryn-csp.conf" in body
        if pattern.startswith("/idp") or "idp" in pattern:
            assert not uses_csp, pattern
        elif "proxy_pass http://127.0.0.1:8000" in body or "try_files" in body:
            assert uses_csp, pattern
    value = re.search(r'Content-Security-Policy "([^"]+)"', CSP).group(1)
    assert "'unsafe-inline'" not in value and "'unsafe-eval'" not in value and "http" not in value and "data:" not in value


def test_the_csp_verify_expects_is_the_one_nginx_sends():
    loader = importlib.machinery.SourceFileLoader("provision", str(HERE.parent / "provision" / "suveryn-provision"))
    spec = importlib.util.spec_from_loader("provision", loader)
    p = importlib.util.module_from_spec(spec)
    loader.exec_module(p)
    assert re.search(r'Content-Security-Policy "([^"]+)"', CSP).group(1) == p.UI_CSP
    for name, value in p.COMMON_HEADERS.items():
        assert re.search(rf'add_header {name} "{re.escape(value)}" always;', HEADERS, re.I), name


def test_api_docs_are_never_routed():
    assert re.search(r"location ~ \^/\(docs\|redoc\|openapi\\\.json\)\$ \{\s*return 404;", SITE)
    gateway_routes = [p for p, b in locations().items() if "127.0.0.1:8000" in b]
    assert sorted(gateway_routes) == sorted(["~ ^/(v1|auth)/", "= /health", "= /v1/chat", "= /v1/documents"])


def test_no_document_or_sign_in_code_is_written_to_disk():
    assert "proxy_max_temp_file_size 0;" in SITE                         # no proxied answer in nginx's temp files
    assert "proxy_request_buffering off;" in locations()["= /v1/documents"]  # uploads stream, never spooled
    fmt = re.search(r"log_format suveryn '([^;]+)';", SITE).group(1)
    assert "$uri" in fmt and not re.search(r"\$(request|request_uri|args|query_string|http_referer)\b", fmt)
    assert all("suveryn.access.log suveryn;" in line for line in SITE.splitlines() if "access_log" in line)


def test_forwarded_headers_are_set_not_appended():
    """The gateway and Keycloak trust them from 127.0.0.1: a client's own X-Forwarded-For must not pass."""
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in SITE
    assert "$proxy_add_x_forwarded_for" not in SITE
    assert "proxy_set_header X-Forwarded-Proto https;" in SITE


def test_tls_is_modern_only_and_http_redirects():
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in SITE and "ssl_session_tickets off;" in SITE
    assert "return 301 https://@HOST@$request_uri;" in SITE and "server_tokens off;" in SITE


def test_the_admin_console_is_for_the_office_network_only():
    admin = locations()["/idp/admin/"]
    assert "deny all;" in admin and "allow 0.0.0.0/0" not in admin


def test_directives_work_on_ubuntu_24_04_nginx_1_24():
    """Ubuntu 24.04 ships nginx 1.24: `http2 on;` (1.25.1+) fails `nginx -t` there (found on the test pod)."""
    assert not re.search(r"^\s*http2\s+on;", SITE, re.M)
    assert "listen 443 ssl http2;" in SITE
