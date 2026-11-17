"""Static checks for the MOS ROM layer (rom/). The ROM itself only builds on a build box."""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rom.tools import pin_cert

ROOT = Path(__file__).resolve().parent.parent
ROM = ROOT / "rom"
EXTRA = ROM / "vendor-extra"
BRIDGE = EXTRA / "MosBridge"
APP = ROOT / "android" / "app" / "src" / "main"
A = "{http://schemas.android.com/apk/res/android}"
PERMISSION = "dev.mos.permission.BRIDGE"


# ---- pin_cert ------------------------------------------------------------------------


@given(st.binary(min_size=32, max_size=32))
def test_pin_cert_accepts_every_spelling_of_a_digest(raw: bytes) -> None:
    hexd = raw.hex()
    if hexd == "0" * 64:
        return
    spellings = [hexd, hexd.upper(), ":".join(f"{b:02X}" for b in raw), f"SHA256: {hexd}"]
    for s in spellings:
        assert pin_cert.normalize_digest(s) == hexd
    xml = pin_cert.known_certs_xml(spellings[2])
    items = [i.text for i in ET.fromstring(xml).iter("item")]
    assert items == [hexd]


@pytest.mark.parametrize("bad", ["", "abc", "0" * 64, "g" * 64, "a" * 63, "a" * 65, "sha1:" + "a" * 40])
def test_pin_cert_rejects_non_digests(bad: str) -> None:
    with pytest.raises(ValueError):
        pin_cert.normalize_digest(bad)


def test_pin_cert_cli_writes_resource(tmp_path: Path) -> None:
    out = tmp_path / "known_certs.xml"
    assert pin_cert.main(["pin_cert", "ab" * 32, str(out)]) == 0
    assert "ab" * 32 in out.read_text()
    assert pin_cert.main(["pin_cert", "nope", str(out)]) == 1


def test_checked_in_known_certs_is_the_refuse_everyone_placeholder() -> None:
    root = ET.parse(BRIDGE / "res" / "values" / "known_certs.xml").getroot()
    assert [i.text for i in root.iter("item")] == ["0" * 64]


# ---- manifests and wiring ----------------------------------------------------------------


def test_all_rom_xml_is_well_formed() -> None:
    files = list(ROM.rglob("*.xml"))
    assert len(files) >= 4
    for f in files:
        ET.parse(f)


def test_aidl_copies_are_identical() -> None:
    rom_aidl = BRIDGE / "aidl" / "dev" / "mos" / "bridge" / "IMosBridge.aidl"
    app_aidl = APP / "aidl" / "dev" / "mos" / "bridge" / "IMosBridge.aidl"
    assert rom_aidl.read_bytes() == app_aidl.read_bytes()


def test_bridge_service_is_locked_to_the_pinned_app_key() -> None:
    m = ET.parse(BRIDGE / "AndroidManifest.xml").getroot()
    assert m.get("package") == "dev.mos.bridge"
    assert m.get(f"{A}sharedUserId") is None
    perm = m.find("permission")
    assert perm is not None and perm.get(f"{A}name") == PERMISSION
    assert perm.get(f"{A}protectionLevel") == "signature|knownSigner"
    assert perm.get(f"{A}knownCerts") == "@array/mos_known_certs"
    services = m.findall("application/service")
    assert services, "bridge has no service"
    for s in services:
        if s.get(f"{A}exported") == "true":
            assert s.get(f"{A}permission") == PERMISSION, s.get(f"{A}name")
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    # every binder method re-checks the caller (covers a leaked binder)
    methods = re.findall(r"public \w+ (\w+)\([^)]*\) \{\n\s+enforceCaller\(\);", java)
    aidl = (BRIDGE / "aidl" / "dev" / "mos" / "bridge" / "IMosBridge.aidl").read_text()
    declared = re.findall(r"^\s+\w+ (\w+)\(", aidl, re.M)
    assert declared and sorted(methods) == sorted(declared)


def test_bridge_never_allows_power_or_call_keys() -> None:
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    allow = java.split("ALLOWED_KEYS = {", 1)[1].split("};", 1)[0]
    for k in ["POWER", "SLEEP", "WAKEUP", "CALL", "ENDCALL", "CAMERA", "VOLUME", "ASSIST", "VOICE"]:
        assert f"KEYCODE_{k}" not in allow


def test_app_requests_bridge_and_can_see_it() -> None:
    m = ET.parse(APP / "AndroidManifest.xml").getroot()
    perms = [p.get(f"{A}name") for p in m.findall("uses-permission")]
    assert PERMISSION in perms
    assert "dev.mos.bridge" in [p.get(f"{A}name") for p in m.findall("queries/package")]


def test_bridge_enables_the_apps_real_accessibility_service() -> None:
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    svc = re.search(r'AGENT_SERVICE = "([\w.]+)"', java)
    assert svc is not None
    m = ET.parse(APP / "AndroidManifest.xml").getroot()
    names = [s.get(f"{A}name") for s in m.findall("application/service")]
    assert svc.group(1) == "dev.mos.agent" + next(n for n in names if n and n.endswith("AccessibilityService"))


def test_product_packages_are_defined_modules() -> None:
    mk = (EXTRA / "product.mk").read_text()
    block = mk.split("PRODUCT_PACKAGES +=", 1)[1].split("\n\n", 1)[0]
    wanted = set(re.findall(r"^\s+(\w+)", block, re.M))
    defined = {n for bp in EXTRA.rglob("Android.bp") for n in re.findall(r'name: "([^"]+)"', bp.read_text())}
    assert wanted and wanted <= defined
    for src, _dst in re.findall(r"(vendor/extra/\S+):(\S+)", mk):
        assert (EXTRA / src.removeprefix("vendor/extra/")).is_file(), src


def test_privapp_allowlist_matches_bridge_permissions() -> None:
    m = ET.parse(BRIDGE / "AndroidManifest.xml").getroot()
    requested = {p.get(f"{A}name") for p in m.findall("uses-permission")}
    allow = ET.parse(BRIDGE / "privapp-permissions-mos.xml").getroot()
    entry = allow.find("privapp-permissions")
    assert entry is not None and entry.get("package") == "dev.mos.bridge"
    assert {p.get("name") for p in entry.findall("permission")} == requested


# ---- build script ------------------------------------------------------------------------


def test_build_script_parses() -> None:
    subprocess.run(["bash", "-n", str(ROM / "build.sh")], check=True)


def test_build_script_passes_shellcheck() -> None:
    sc = shutil.which("shellcheck")
    assert sc, "pip install -r requirements-dev.txt (shellcheck-py)"
    subprocess.run([sc, "-S", "warning", str(ROM / "build.sh")], check=True)


def test_build_script_never_uses_public_test_keys() -> None:
    sh = (ROM / "build.sh").read_text()
    assert "PRODUCT_DEFAULT_DEV_CERTIFICATE := vendor/lineage-priv/keys/releasekey" in sh
    assert "pin_cert.py" in sh
