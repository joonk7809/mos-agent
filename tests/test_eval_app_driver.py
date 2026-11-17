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


def test_set_bridge() -> None:
    sh = FakeShell([])
    set_bridge(sh, "on")
    set_bridge(sh, "off")
    set_bridge(sh, "keep")
    assert sh.cmds == ["pm enable dev.mos.bridge", "pm disable-user --user 0 dev.mos.bridge"]
    with pytest.raises(ValueError):
        set_bridge(sh, "maybe")


def test_is_emulator_reads_the_qemu_props() -> None:
    from evals.run_evals import is_emulator

    def shell_for(props: dict[str, str]):
        return lambda cmd: props.get(cmd.split()[-1], "") + "\n"

    assert is_emulator(shell_for({"ro.kernel.qemu": "1"}))
    assert is_emulator(shell_for({"ro.boot.qemu": "1"}))
    assert not is_emulator(shell_for({"ro.product.name": "sdk_phone64"}))
