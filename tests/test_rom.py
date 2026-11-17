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


def test_bridge_module_has_the_powers_it_needs_and_lands_where_its_allowlist_does() -> None:
    bp = (BRIDGE / "Android.bp").read_text()
    app = bp.split("android_app {", 1)[1].split("\n}", 1)[0]
    for prop in ['certificate: "platform"', "privileged: true", "platform_apis: true", "system_ext_specific: true"]:
        assert prop in app, prop
    etc = bp.split("prebuilt_etc {", 1)[1].split("\n}", 1)[0]
    assert 'sub_dir: "permissions"' in etc and "system_ext_specific: true" in etc


def test_bridge_refuses_permission_and_install_screens() -> None:
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    blocked = java.split("BLOCKED_FOREGROUND = Arrays.asList(", 1)[1].split(");", 1)[0]
    for pkg in ["com.android.permissioncontroller", "com.google.android.permissioncontroller",
                "com.android.packageinstaller", "com.google.android.packageinstaller"]:
        assert f'"{pkg}"' in blocked
    # every injecting path goes through the gate
    for fn in ["doSwipe", "doKey", "doText"]:
        body = java.split(f"private int {fn}(", 1)[1].split("\n    }\n", 1)[0]
        assert "gate()" in body, fn


def test_app_falls_back_only_when_nothing_was_injected() -> None:
    kt = (APP / "kotlin" / "dev" / "mos" / "agent" / "MosAccessibilityService.kt").read_text()
    when = kt.split("when (val r = bridge.call(f))", 1)[1].split("\n        }\n", 1)[0]
    lines = [ln.strip() for ln in when.strip().strip("{").splitlines() if "->" in ln]
    assert lines[0] == "null, IMosBridge.REFUSED -> false"
    assert lines[1] == "IMosBridge.OK -> true"
    assert all("throw" in ln for ln in lines[2:]) and lines[-1].startswith("else ->")


def test_app_never_enables_accessibility_on_its_own() -> None:
    kt = (APP / "kotlin" / "dev" / "mos" / "agent" / "MainActivity.kt").read_text()
    calls = [m.start() for m in re.finditer(r"enableAgentAccessibility\(\)", kt)]
    assert len(calls) == 1
    fn = kt.rfind("private fun ", 0, calls[0])
    assert kt[fn:].startswith("private fun turnOnAgent()")
    assert "turnOnAgent() }" in kt  # only reached from the button's click listener


def test_bridge_only_drives_labelled_targets_and_checks_version() -> None:
    kt = (APP / "kotlin" / "dev" / "mos" / "agent" / "MosAccessibilityService.kt").read_text()
    assert 'labelledTargetAt(x1, y1) && viaBridge("touch")' in kt
    client = (APP / "kotlin" / "dev" / "mos" / "agent" / "BridgeClient.kt").read_text()
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    bridge_version = int(re.search(r"static final int VERSION = (\d+);", java).group(1))  # type: ignore[union-attr]
    min_version = int(re.search(r"const val MIN_VERSION = (\d+)", client).group(1))  # type: ignore[union-attr]
    assert bridge_version >= min_version >= 2
    assert "if (version < MIN_VERSION)" in client


# ---- emulator paths (rom/emulator/) -------------------------------------------------------


def test_gradle_bridge_manifest_matches_the_rom_manifest() -> None:
    rom = ET.parse(BRIDGE / "AndroidManifest.xml").getroot()
    gradle = ET.parse(ROOT / "android" / "bridge" / "src" / "main" / "AndroidManifest.xml").getroot()
    assert gradle.get("package") is None  # AGP takes the namespace from build.gradle.kts
    rom.attrib.pop("package")
    assert ET.tostring(rom) == ET.tostring(gradle)
    gradle_build = (ROOT / "android" / "bridge" / "build.gradle.kts").read_text()
    assert 'namespace = "dev.mos.bridge"' in gradle_build
    assert "../../rom/vendor-extra/MosBridge/src" in gradle_build  # one Java source for both builds


def test_bridge_source_builds_against_the_public_sdk() -> None:
    java = (BRIDGE / "src" / "dev" / "mos" / "bridge" / "BridgeService.java").read_text()
    code = "\n".join(ln for ln in java.splitlines() if not ln.strip().startswith(("*", "/*", "//")))
    assert "im.injectInputEvent(" not in code and "InputManager.INJECT_INPUT_EVENT_MODE" not in code
    assert 'getMethod("injectInputEvent"' in java
    gate = java.split("private int gate() {", 1)[1].split("\n    }\n", 1)[0]
    assert "INJECT == null) return IMosBridge.REFUSED" in gate  # missing API: fall back, nothing injected


def test_install_bridge_script_only_touches_emulators_with_test_keys() -> None:
    sh = ROM / "emulator" / "install_bridge.sh"
    subprocess.run(["bash", "-n", str(sh)], check=True)
    sc = shutil.which("shellcheck")
    assert sc
    subprocess.run([sc, "-S", "warning", str(sh)], check=True)
    text = sh.read_text()
    assert "ro.kernel.qemu" in text and "never touches real phones" in text
    assert "framework-res.apk" in text and 'image_digest" != "$testkey_digest"' in text


def test_build_script_supports_emulator_images() -> None:
    sh = (ROM / "build.sh").read_text()
    assert "sdk_phone_x86_64|sdk_phone_arm64) EMULATOR=1" in sh
    assert "emu_img_zip" in sh and "sdk-repo-*system-images*.zip" in sh
    assert "[[ $EMULATOR == 1 ]] && APP_VARIANT=debug" in sh


def test_eval_receiver_is_locked_down() -> None:
    main_manifest = ET.parse(APP / "AndroidManifest.xml").getroot()
    assert not main_manifest.findall("application/receiver")  # release APKs carry no eval driver
    assert not (APP / "kotlin" / "dev" / "mos" / "agent" / "EvalReceiver.kt").exists()
    debug = ROOT / "android" / "app" / "src" / "debug"
    m = ET.parse(debug / "AndroidManifest.xml").getroot()
    recv = [r for r in m.findall("application/receiver") if r.get(f"{A}name") == ".EvalReceiver"]
    assert len(recv) == 1 and recv[0].get(f"{A}permission") == "android.permission.DUMP"
    kt = (debug / "kotlin" / "dev" / "mos" / "agent" / "EvalReceiver.kt").read_text()
    assert "return debuggable && isEmulator()" in kt
    assert 'prop("ro.kernel.qemu") == "1" || prop("ro.boot.qemu") == "1"' in kt
    assert "FINGERPRINT" not in kt.split("fun isEmulator", 1)[1]
    assert "if (!allowed(context))" in kt.split("override fun onReceive", 1)[1].split("when", 1)[0]
    assert 'getStringExtra("token")' not in kt and 'getStringExtra("server")' not in kt
    loop = (APP / "kotlin" / "dev" / "mos" / "agent" / "AgentLoop.kt").read_text()
    assert "if (!eval) return service.overlay.confirm(reason)" in loop
    assert "private val eval: Boolean = false" in loop
    main = (APP / "kotlin" / "dev" / "mos" / "agent" / "MainActivity.kt").read_text()
    assert "eval = true" not in main


def test_debug_apps_never_get_the_phone_key() -> None:
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text()
    debug = gradle.split("        debug {", 1)[1].split("}", 1)[0]
    assert 'findByName("mosDebug")' in debug and 'findByName("mos")' not in debug
    sh = (ROM / "build.sh").read_text()
    assert "[[ $EMULATOR == 1 ]] && APP_KEY_NAME=mos-app-emulator" in sh
    emu = sh.split("if [[ $EMULATOR == 1 ]]; then\n    ANDROID_HOME", 1)[1].split("else", 1)[0]
    assert "MOS_DEBUG_KEYSTORE=$APP_KS" in emu and "assembleDebug" in emu
