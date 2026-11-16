# MOS agent: a minimal mobile computer-use agent

Claude drives an Android phone or emulator over ADB. Each step it sees the screen,
picks one action (tap, swipe, type, key, open app), and then sees the result.
It is small on purpose (about 500 lines) so it's easy to change and measure.

## Setup (about 15 minutes)

1. Install Android Studio, then in **Device Manager** create a Pixel emulator
   (API 34 or newer, a "Google APIs" image). Start it.
2. Check that ADB sees it: `adb devices` should list `emulator-5554`.
3. Install the Python deps and set your key:
   ```bash
   pip install -r requirements.txt
   export ANTHROPIC_API_KEY=...     # or `ant auth login`
   ```
4. Optional but recommended: save a clean emulator snapshot named `clean`
   (Extended controls → Snapshots) so eval runs start from the same state.

A real phone works too: turn on Developer options → USB debugging, plug it in,
and accept the prompt. Use an emulator until you trust it.

## Run

```bash
python -m mos "Create a calendar event tomorrow at 3 PM titled Dentist"
python -m mos "Turn on dark theme" --mode screenshot   # pixels only
python -m mos "Set an alarm for 7:30" --mode som       # boxes + ids drawn on the screenshot
```

Each run saves every screenshot and a `trajectory.jsonl` (action, reason, latency,
result, token usage) under `runs/<timestamp>/`.

Actions the model marks as irreversible (send, pay, delete, post) stop and ask you
first. `--yes` skips that, so only use it on an emulator.

## Perception modes (the main research knob)

| mode | model gets | target by |
|---|---|---|
| `screenshot` | resized screenshot | image pixel coordinates (mapped back to the device) |
| `tree` | accessibility element list only | element id |
| `both` (default) | screenshot + element list | element id, or device coordinates |
| `som` | screenshot with numbered boxes + list | element id |

Compare them with the eval runner:

```bash
python -m evals.run_evals --modes screenshot tree both som --repeats 3
```

Tasks live in `evals/tasks.jsonl`. Each one is scored by an `adb shell` check on
the device's real state, not by what the agent says it did. Add your own tasks there.

## Layout

```
mos/device.py    ADB wrapper (screencap, uiautomator dump, input tap/swipe/text/keyevent)
mos/uitree.py    accessibility XML -> compact numbered element list
mos/observe.py   builds one observation; resizing, coordinate mapping, set-of-marks
mos/agent.py     tools, system prompt, the loop, approval gate, logging
mos/fake.py      fake device for offline tests
evals/           task suite + runner
tests/           offline tests (pytest -q), using a mocked API
```

## Known limits (good next things to work on)

- Full history is resent every step, so cost grows with task length. A summary of
  older steps would fix that.
- `input text` can't type emoji or most non-ASCII text. An ADB keyboard IME fixes that.
- Some apps (games, Flutter, WebViews) expose little or no accessibility tree. Those
  fall back to the screenshot.
- `uiautomator dump` takes about 1-2 s and fails on screens that are animating.
  An accessibility-service app on the device would be much faster.
- Android only. iOS would need XCUITest/Appium, or WebDriverAgent on a simulator.
