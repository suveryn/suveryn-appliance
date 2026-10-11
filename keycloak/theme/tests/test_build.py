"""build_theme.py: the jar is reproducible, and the brand repository can't smuggle in git options.

Needs fontTools, like the build: uv run --with pytest --with fonttools pytest keycloak/theme/tests
"""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fontTools")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_theme  # noqa: E402


def test_the_jar_is_byte_for_byte_reproducible(tmp_path):
    theme = tmp_path / "theme"
    (theme / "login" / "resources").mkdir(parents=True)
    (theme / "login" / "theme.properties").write_text("parent=keycloak.v2\n")
    (theme / "login" / "resources" / "a.css").write_text("body{}\n")
    build_theme.write_jar(theme, tmp_path / "1.jar")
    (theme / "login" / "theme.properties").touch()  # a later build: same content, newer file dates
    build_theme.write_jar(theme, tmp_path / "2.jar")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    assert digest(tmp_path / "1.jar") == digest(tmp_path / "2.jar")
    with zipfile.ZipFile(tmp_path / "1.jar") as z:
        assert z.namelist() == ["META-INF/keycloak-themes.json", "theme/suveryn/login/resources/a.css",
                                "theme/suveryn/login/theme.properties"]


@pytest.mark.parametrize("repo", ["--upload-pack=touch /tmp/owned", "-c core.sshCommand=x"])
def test_a_brand_repository_that_looks_like_an_option_is_refused(tmp_path, repo):
    with pytest.raises(SystemExit, match="not a repository"):
        build_theme.fetch_brand(repo, "main", tmp_path)
