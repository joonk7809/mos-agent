# Dot: a local hotkey agent for the Mac (v0, unbound)

A small black dot sits in the top-right corner. **Control+Space** opens a prompt bar: ask anything, or tell it
to do something on the Mac. On Enter the bar collapses and focus goes back to your app while the dot (grey)
works; the answer pops up when it's done. The agent runs locally against Inkling 2 Mini (or any OpenAI-compatible server)
and acts through shell, files, AppleScript, `open`, web fetch and screen control (accessibility elements first,
screenshots + click/type/scroll/drag as the fallback). Research and design notes:
https://claude.ai/artifact/952W6ZsVNQ6Q1ro4oSNdkG. Why v0 has no permission gates: `docs/decisions/0011-dot-unbound-v0.md`.

**v0 is unbound.** It can run any shell command, change or delete any file you can, send messages, and use the
web, without asking. Only **Stop** (halts before the next action: **Option+Esc** anywhere, Control+Space or a click
on the grey dot while it runs) and a few loop limits stand in the way. (Dot claims Option+Esc globally while
it's open.)
Run it on a Mac you're comfortable letting it change.

## Run it (macOS 14+, Apple silicon)
```bash
# 1. Model (local). Inkling 2 Mini is ~20 GB natively; on a 24 GB Mac try the 4-bit GGUF:
ollama pull inkling-2-mini            # or a 4-bit tag / ggml-org/Inkling-2-Mini-GGUF:IQ4_NL via llama-server
# 2. Agent deps (screen control)
python3 -m venv ~/.venvs/dot && ~/.venvs/dot/bin/pip install pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
# 3. Try the agent in a terminal first
cd mos-agent && ~/.venvs/dot/bin/python -m dot "what's in my Downloads folder?"
# 4. Build and open the dot
dot/mac/build.sh ~/.venvs/dot/bin/python && open dot/mac/build/Dot.app
```
**Close and quit.** The hotkey again, Esc, or the ✕ closes the bar back to the dot. To quit Dot, right-click
(or Control-click) the dot and choose **Quit Dot**, or run `pkill -x Dot`. If something fails, the bar shows
the reason (for example "No model server answering at http://127.0.0.1:11434/v1 ... Start it"), and the
agent's own log is `~/Library/Logs/Dot/daemon.err`.

**Hotkeys.** Defaults: Control+Space opens/closes (and stops while running), Option+Esc stops. macOS uses
Control+Space to switch input sources, and that wins: turn it off in System Settings > Keyboard > Keyboard
Shortcuts > Input Sources (Dot warns on launch if it's on and you have more than one input source). macOS
doesn't report clashes with other apps' hotkeys, so if a combo does nothing, pick another. To use other keys, no rebuild needed:
```bash
defaults write dev.mos.dot hotkey "cmd+shift+space"   # open/close
defaults write dev.mos.dot stopHotkey "ctrl+esc"      # stop
```
then quit and reopen Dot. Combos: ctrl/opt/cmd/shift + a letter, digit, space, esc, return, tab, F1-F12 or
`` ` / . , ; ' - = ``. `DOT_HOTKEY` / `DOT_STOP_HOTKEY` at build or launch time also work.

Grant **Dot** Accessibility and Screen Recording in System Settings > Privacy & Security (macOS prompts on first
use; Automation prompts appear per app the first time AppleScript drives it).

Settings (environment, or edit `dot/model.py` defaults): `DOT_MODEL_URL` (default Ollama
`http://127.0.0.1:11434/v1`), `DOT_MODEL` (`inkling-2-mini`), `DOT_API_KEY` (hosted fallback),
`DOT_REASONING` (`low`), `DOT_MAX_TOKENS` (2048), `DOT_MAX_SIDE` (screenshot long side, 1280),
`DOT_COORDS` (`pixels` or `norm1000` if the model emits 0..999), `DOT_LOG_DIR` (`~/Library/Logs/Dot`).

## Speed
Built in: accessibility elements as text before pixels, front-window screenshots downscaled to 1280 px,
several actions per model reply, an append-only history so the server's prefix cache is reused (old
screenshots are pruned in batches), low reasoning effort and a 2048-token cap per step.
Measure: every step's model and tool time is in the action log; `python -m dot.stats` prints medians
(seconds per step, per screenshot, prompt tokens).

## Layout
`model.py` OpenAI-compatible client with tool calls · `tools.py` the tools · `desktop.py` macOS screen
control (CGEvent, screencapture, AX tree) · `loop.py` agent loop, Stop, limits, action log ·
`daemon.py` JSON-lines protocol the app speaks · `mac/Dot.swift` the dot, hotkey and prompt bar ·
`stats.py` timing summary. Tests: `tests/test_dot.py` (fakes; no Mac needed).
