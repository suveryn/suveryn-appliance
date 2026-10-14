"""Signed bundles (tracker #36): made and signed by suveryn-bundle-release, checked and installed by the
appliance's bundle tool. Any Linux machine with openssl 3:

    python3 -m pytest bundles/tests
"""

import base64
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parent / "suveryn-bundle-release"
TOOL = HERE.parent.parent / "image" / "mkosi" / "mkosi.extra" / "usr" / "lib" / "suveryn" / "bundle"

_loader = importlib.machinery.SourceFileLoader("bundle_under_test", str(TOOL))
bundle = importlib.util.module_from_spec(importlib.util.spec_from_loader("bundle_under_test", _loader))
sys.dont_write_bytecode = True  # never a __pycache__ in the image's tree
_loader.exec_module(bundle)


def release(*args, env=None, check=True):
    r = subprocess.run([sys.executable, str(RELEASE), *map(str, args)], capture_output=True, text=True, env=env)
    if check:
        assert r.returncode == 0, r.stderr
    return r


@pytest.fixture
def world(tmp_path):
    """Two trusted keys (current and spare), one untrusted key, a signed model bundle."""
    keys = tmp_path / "trusted"
    keys.mkdir()
    for name in ("current", "spare", "other"):
        release("test-key", tmp_path / f"{name}.key")
    for name in ("current", "spare"):
        (keys / f"{name}.pem").write_text(release("public-key", "--key-file", tmp_path / f"{name}.key").stdout)
    b = tmp_path / "bundle"
    (b / "tokenizer").mkdir(parents=True)
    (b / "model.gguf").write_bytes(os.urandom(3 * 2**20 + 17))
    (b / "tokenizer" / "vocab.json").write_text('{"a": 1}\n')
    release("make", b, "--kind", "model", "--name", "test-model", "--version", "2026.10.1")
    release("sign", b, "--key-file", tmp_path / "current.key")
    env = {**os.environ, "SUVERYN_BUNDLE_KEYS": str(keys), "SUVERYN_BUNDLE_ROOT": str(tmp_path / "installed")}
    return {"tmp": tmp_path, "keys": keys, "bundle": b, "env": env}


def tool(world, *args):
    r = subprocess.run([sys.executable, str(TOOL), *map(str, args), "--json"], capture_output=True, text=True,
                       env=world["env"])
    return r.returncode, json.loads(r.stdout) if r.stdout.strip() else None


def resign(world, manifest: dict, key="current"):
    """Sign a manifest as given, past the release tool's own checks (what an attacker with the key could do)."""
    b = world["bundle"]
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (b / "manifest.json").write_bytes(raw)
    text = world["tmp"] / "text"
    text.write_bytes(bundle.signed_text(raw))
    sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(world["tmp"] / f"{key}.key"),
                          "-in", str(text)], capture_output=True, check=True).stdout
    pem = subprocess.run(["openssl", "pkey", "-in", str(world["tmp"] / f"{key}.key"), "-pubout"],
                         capture_output=True, check=True).stdout
    (b / "manifest.sig").write_text(json.dumps({"format": bundle.SIG_FORMAT, "key": bundle.fingerprint(pem),
                                                "signature": base64.b64encode(sig).decode()}))


def manifest(world) -> dict:
    return json.loads((world["bundle"] / "manifest.json").read_text())


# ---------------------------------------------------------------------------- the format
def test_the_manifest_lists_every_file(world):
    m = manifest(world)
    assert (m["format"], m["kind"], m["name"], m["version"]) == ("suveryn-bundle/1", "model", "test-model", "2026.10.1")
    assert [f["path"] for f in m["files"]] == ["model.gguf", "tokenizer/vocab.json"]
    assert m["files"][0]["size"] == 3 * 2**20 + 17


def test_the_signed_text_is_short_and_fixed():
    """Under KMS's 4096-byte limit for a raw message, whatever the bundle's size."""
    text = bundle.signed_text(b"{}")
    assert text == b"suveryn-bundle-signature/1\nmanifest-sha256 " \
                   b"44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a\n"
    assert len(text) == 108


def test_a_valid_bundle(world):
    code, out = tool(world, "verify", world["bundle"])
    assert code == 0 and out["valid"] and out["files"] == 2 and out["ignored"] == []
    assert out["key"] == bundle.fingerprint((world["keys"] / "current.pem").read_bytes())


def test_the_spare_key_is_trusted_too(world):
    release("sign", world["bundle"], "--key-file", world["tmp"] / "spare.key")
    assert tool(world, "verify", world["bundle"])[0] == 0


def test_a_retired_key_isnt(world):
    """Retiring a key: the next system update ships without it."""
    (world["keys"] / "current.pem").unlink()
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and "doesn't trust" in out["error"]


def test_files_the_manifest_doesnt_list_are_ignored(world):
    (world["bundle"] / ".DS_Store").write_bytes(b"x")
    (world["bundle"] / "tokenizer" / "extra.txt").write_text("x")
    code, out = tool(world, "verify", world["bundle"])
    assert code == 0 and out["ignored"] == [".DS_Store", "tokenizer/extra.txt"]


# ---------------------------------------------------------------------------- what is refused
def flip_a_byte(path: Path, offset: int = 1000):
    data = bytearray(path.read_bytes())
    data[offset] ^= 1
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("change, message", [
    (lambda w: flip_a_byte(w["bundle"] / "model.gguf"), "model.gguf: its SHA-256 doesn't match"),
    (lambda w: (w["bundle"] / "tokenizer" / "vocab.json").write_text('{"a": 2, "b": 3}\n'), "the manifest says"),
    (lambda w: (w["bundle"] / "tokenizer" / "vocab.json").unlink(), "tokenizer/vocab.json is missing"),
    (lambda w: (w["bundle"] / "manifest.sig").unlink(), "isn't signed"),
    (lambda w: (w["bundle"] / "manifest.json").unlink(), "not a bundle"),
    (lambda w: (w["bundle"] / "manifest.sig").write_text("{"), "damaged"),
    (lambda w: (w["bundle"] / "manifest.json").write_text(
        (w["bundle"] / "manifest.json").read_text().replace("2026.10.1", "2026.10.2")), "signature doesn't match"),
    (lambda w: release("sign", w["bundle"], "--key-file", w["tmp"] / "other.key"), "doesn't trust"),
    (lambda w: [p.unlink() for p in w["keys"].glob("*.pem")], "trusts no bundle key"),
])
def test_refused(world, change, message):
    change(world)
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and not out["valid"] and message in out["error"], out


def test_the_signature_must_match_the_named_key(world):
    sig = json.loads((world["bundle"] / "manifest.sig").read_text())
    sig["key"] = bundle.fingerprint((world["keys"] / "spare.pem").read_bytes())
    (world["bundle"] / "manifest.sig").write_text(json.dumps(sig))
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and "signature doesn't match" in out["error"]


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "a/../../b", "./model.gguf", "a//b", "manifest.sig",
                                  "a\\b", ""])
def test_a_signed_manifest_still_cant_reach_outside_the_bundle(world, path):
    m = manifest(world)
    m["files"].append({"path": path, "size": 0, "sha256": "0" * 64})
    resign(world, m)
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and not out["valid"], path


@pytest.mark.parametrize("edit, message", [
    (lambda m: m["files"].append(dict(m["files"][0], path="MODEL.gguf")), "twice"),
    (lambda m: m["files"].append({"path": "model.gguf/x", "size": 0, "sha256": "0" * 64}), "file and as a folder"),
    (lambda m: m.update(kind="plugin"), "unknown kind"),
    (lambda m: m.update(name="../x"), "unusable name"),
    (lambda m: m.update(files=[]), "no files"),
    (lambda m: m["files"][0].update(size=-1), "unusable size"),
    (lambda m: m["files"][0].update(size=True), "unusable size"),
    (lambda m: m["files"][0].update(sha256="ABC"), "unusable SHA-256"),
    (lambda m: m.update(format="suveryn-bundle/2"), "not a suveryn-bundle/1"),
    (lambda m: m.update(title="x" * 65), "unusable title"),
    (lambda m: m.update(description="line\x1b[31m"), "unusable description"),
    (lambda m: m.update(default="yes"), "unusable default"),
])
def test_a_signed_but_malformed_manifest_is_refused(world, edit, message):
    m = manifest(world)
    edit(m)
    resign(world, m)
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and message in out["error"], out


def test_a_link_is_refused(world, tmp_path):
    outside = tmp_path / "secret"
    outside.write_text('{"a": 1}\n')
    target = world["bundle"] / "tokenizer" / "vocab.json"
    target.unlink()
    target.symlink_to(outside)
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and "outside the bundle" in out["error"]


def test_a_linked_folder_is_refused(world, tmp_path):
    shutil.move(world["bundle"] / "tokenizer", tmp_path / "elsewhere")
    (world["bundle"] / "tokenizer").symlink_to(tmp_path / "elsewhere")
    code, out = tool(world, "verify", world["bundle"])
    assert code == 1 and "outside the bundle" in out["error"]


# ---------------------------------------------------------------------------- installing
def test_install_copies_exactly_the_listed_files(world):
    (world["bundle"] / ".DS_Store").write_bytes(b"x")
    code, out = tool(world, "install", world["bundle"])
    dest = world["tmp"] / "installed" / "model" / "test-model" / "2026.10.1"
    assert code == 0 and out["installed"] == str(dest) and out["already"] is False
    assert sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()) == \
        ["manifest.json", "manifest.sig", "model.gguf", "tokenizer/vocab.json"]
    assert (dest / "model.gguf").read_bytes() == (world["bundle"] / "model.gguf").read_bytes()
    # and the installed copy is itself a valid bundle
    assert tool(world, "verify", dest)[0] == 0


def test_installing_again_is_harmless(world):
    tool(world, "install", world["bundle"])
    code, out = tool(world, "install", world["bundle"])
    assert code == 0 and out["already"] is True


def test_the_same_version_with_other_contents_isnt_installed_over(world):
    tool(world, "install", world["bundle"])
    (world["bundle"] / "model.gguf").write_bytes(b"other")
    release("make", world["bundle"], "--kind", "model", "--name", "test-model", "--version", "2026.10.1")
    release("sign", world["bundle"], "--key-file", world["tmp"] / "current.key")
    code, out = tool(world, "install", world["bundle"])
    assert code == 3 and "exists with another manifest" in out["error"]


def test_a_failed_install_leaves_nothing(world):
    flip_a_byte(world["bundle"] / "tokenizer" / "vocab.json", 3)
    code, out = tool(world, "install", world["bundle"])
    assert code == 1 and "SHA-256" in out["error"]
    parent = world["tmp"] / "installed" / "model" / "test-model"
    assert list(parent.iterdir()) == []  # no version folder, no .partial


def test_a_system_bundle_isnt_installed_as_a_model(world):
    release("make", world["bundle"], "--kind", "system", "--name", "suveryn", "--version", "0.1.0")
    release("sign", world["bundle"], "--key-file", world["tmp"] / "current.key")
    assert tool(world, "verify", world["bundle"])[0] == 0  # the same check as a model
    code, out = tool(world, "install", world["bundle"])
    assert code == 1 and "#40" in out["error"]


# ---------------------------------------------------------------------------- the release side
def test_make_refuses_a_link(world, tmp_path):
    (world["bundle"] / "link").symlink_to(tmp_path / "current.key")
    r = release("make", world["bundle"], "--kind", "model", "--name", "x", "--version", "1", check=False)
    assert r.returncode == 1 and "isn't a plain file" in r.stderr


def test_a_new_manifest_drops_the_old_signature(world):
    release("make", world["bundle"], "--kind", "model", "--name", "test-model", "--version", "2026.10.2")
    assert not (world["bundle"] / "manifest.sig").exists()


def test_signing_with_aws_kms(world, tmp_path):
    """A stand-in for the AWS CLI that answers as KMS does (base64 DER public key, base64 signature)."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "aws").write_text(f"""#!{sys.executable}
import base64, json, subprocess, sys
args = sys.argv[1:]
key = {str(world["tmp"] / "spare.key")!r}
assert args[:2] == ["kms", args[1]] and "--key-id" in args and args[args.index("--key-id") + 1] == "alias/suveryn-bundles"
if args[1] == "get-public-key":
    der = subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-outform", "DER"], capture_output=True).stdout
    print(json.dumps({{"KeySpec": "ECC_NIST_EDWARDS25519", "KeyUsage": "SIGN_VERIFY",
                      "PublicKey": base64.b64encode(der).decode()}}))
elif args[1] == "sign":
    assert args[args.index("--signing-algorithm") + 1] == "ED25519_SHA_512"
    assert args[args.index("--message-type") + 1] == "RAW"
    msg = args[args.index("--message") + 1].removeprefix("fileb://")
    assert len(open(msg, "rb").read()) < 4096
    sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", key, "-in", msg], capture_output=True).stdout
    print(json.dumps({{"Signature": base64.b64encode(sig).decode(), "SigningAlgorithm": "ED25519_SHA_512"}}))
""")
    (fake / "aws").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    r = release("sign", world["bundle"], "--aws-kms", "alias/suveryn-bundles", "--region", "eu-west-1", env=env)
    assert "signed with" in r.stdout
    pem = release("public-key", "--aws-kms", "alias/suveryn-bundles", env=env).stdout
    assert pem == (world["keys"] / "spare.pem").read_text()
    code, out = tool(world, "verify", world["bundle"])
    assert code == 0 and out["key"] == bundle.fingerprint(pem.encode())


def test_a_wrong_kms_key_type_is_refused(world, tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "aws").write_text('#!/bin/sh\necho \'{"KeySpec": "ECC_NIST_P256", "KeyUsage": "SIGN_VERIFY", "PublicKey": ""}\'\n')
    (fake / "aws").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"}
    r = release("sign", world["bundle"], "--aws-kms", "k", env=env, check=False)
    assert r.returncode == 1 and "isn't an Ed25519 signing key" in r.stderr


def test_title_description_and_default_for_the_installer(world):
    release("make", world["bundle"], "--kind", "model", "--name", "test-model", "--version", "2026.10.1",
            "--title", "Test Model 7B", "--description", "Small and fast. For tests.", "--default")
    release("sign", world["bundle"], "--key-file", world["tmp"] / "current.key")
    code, out = tool(world, "verify", world["bundle"])
    assert code == 0 and (out["title"], out["description"], out["default"]) == \
        ("Test Model 7B", "Small and fast. For tests.", True)
