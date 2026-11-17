"""The app-driven eval path (evals/run_evals.py --driver app) without a device."""

from __future__ import annotations

import pytest

from evals.run_evals import run_app, set_bridge


class FakeShell:
    def __init__(self, logs: list[str]) -> None:
        self.cmds: list[str] = []
        self.logs = logs

    def __call__(self, cmd: str) -> str:
        self.cmds.append(cmd)
        if cmd.startswith("logcat -d"):
            return self.logs.pop(0) if self.logs else ""
        return ""


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_run_app_waits_for_the_end_line_and_quotes_the_task() -> None:
    sh = FakeShell(["", "I MOS: step\n", "I MOS: MOS_TASK_END status=success steps=7 eval=true\n"])
    c = Clock()
    r = run_app(sh, "Turn on 'dark' mode; then reboot", sleep=c.sleep, clock=c)
    assert r == {"status": "success", "self_report": True, "steps": 7}
    assert sh.cmds[0] == "logcat -c"
    bc = sh.cmds[1]
    assert "-n dev.mos.agent/.EvalReceiver" in bc and "-a dev.mos.agent.EVAL_RUN" in bc
    # quoted for the device shell: the ; and ' can't break out of the argument
    assert bc.endswith("--es task 'Turn on '\"'\"'dark'\"'\"' mode; then reboot'")
    assert not any("EVAL_STOP" in x for x in sh.cmds)


def test_run_app_reports_failure_statuses_as_not_self_reported_success() -> None:
    c = Clock()
    r = run_app(FakeShell(["MOS_TASK_END status=no_server_settings steps=0 eval=true"]), "x", sleep=c.sleep, clock=c)
    assert r["status"] == "no_server_settings" and r["self_report"] is False


def test_run_app_times_out_and_stops_the_task() -> None:
    sh = FakeShell([])
    c = Clock()
    r = run_app(sh, "x", timeout_s=10, poll_s=2, sleep=c.sleep, clock=c)
    assert r == {"status": "timeout", "self_report": False, "steps": None}
    assert "EVAL_STOP" in sh.cmds[-1]


def test_set_bridge_rejects_unknown_states() -> None:
    with pytest.raises(ValueError):
        set_bridge(FakeShell([]), "maybe")
    sh = FakeShell([])
    set_bridge(sh, "keep")
    assert sh.cmds == []


def test_is_emulator_reads_the_qemu_props() -> None:
    from evals.run_evals import is_emulator

    def shell_for(props: dict[str, str]):
        return lambda cmd: props.get(cmd.split()[-1], "") + "\n"

    assert is_emulator(shell_for({"ro.kernel.qemu": "1"}))
    assert is_emulator(shell_for({"ro.boot.qemu": "1"}))
    assert not is_emulator(shell_for({"ro.product.name": "sdk_phone64"}))


class PmShell:
    """Fakes `pm` for one package state; records the commands."""

    def __init__(self, state: str, sticky: bool = False) -> None:
        self.state, self.sticky, self.cmds = state, sticky, []

    def __call__(self, cmd: str) -> str:
        self.cmds.append(cmd)
        if cmd == "pm list packages dev.mos.bridge":
            return "" if self.state == "missing" else "package:dev.mos.bridge\n"
        if cmd == "pm list packages -d dev.mos.bridge":
            return "package:dev.mos.bridge\n" if self.state == "disabled" else ""
        if not self.sticky and cmd.startswith("pm enable"):
            self.state = "enabled"
        if not self.sticky and cmd.startswith("pm disable-user"):
            self.state = "disabled"
        return ""


def test_set_bridge_changes_state_only_when_needed_and_verifies_it() -> None:
    from evals.run_evals import set_bridge

    sh = PmShell("enabled")
    set_bridge(sh, "on", sleep=lambda _s: None)
    assert not any(c.startswith("pm enable") for c in sh.cmds)  # no restart of a running bridge
    set_bridge(sh, "off", sleep=lambda _s: None)
    assert sh.state == "disabled"
    with pytest.raises(RuntimeError, match="-writable-system"):
        set_bridge(PmShell("missing"), "on", sleep=lambda _s: None)
    with pytest.raises(RuntimeError, match="couldn't switch"):
        set_bridge(PmShell("enabled", sticky=True), "off", sleep=lambda _s: None)


def test_setup_failures_and_wrong_bridge_state_make_runs_invalid() -> None:
    from evals.run_evals import run_invalid_reason

    assert run_invalid_reason({"status": "no_server_settings"}, "keep")
    assert run_invalid_reason({"status": "fail", "bridge_connected": False}, "on")
    assert run_invalid_reason({"status": "success", "bridge_connected": True}, "off")
    assert run_invalid_reason({"status": "fail", "bridge_connected": True}, "on") is None
    assert run_invalid_reason({"status": "fail"}, "off") is None  # adb driver: no bridge info


def test_run_app_reads_the_bridge_flag() -> None:
    c = Clock()
    r = run_app(FakeShell(["MOS_TASK_END status=fail steps=3 eval=true bridge=true"]), "x", sleep=c.sleep, clock=c)
    assert r["bridge_connected"] is True and r["steps"] == 3
