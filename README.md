# MOS: a phone agent for you and your friends

An open-weight model (MAI-UI-8B) on a rented GPU drives Android phones. Each phone
runs the MOS app, which uses Android's accessibility APIs to send its screen to the
server and carry out the actions the server sends back: taps, swipes, typing,
opening apps. The agent asks before anything that looks like sending, paying or
deleting, and the overlay has a Stop button.

```
 phone (MOS app)                         GPU server (rented)
 ───────────────                         ───────────
 screenshot + UI tree  ── Tailscale ──▶  mos.server (FastAPI) ──▶ vLLM: MAI-UI-8B
 tap / swipe / type    ◀──────────────   next action + "ask first?" check
```

| Part | Where | What it does |
|---|---|---|
| `mos/server.py` | GPU server | Sessions, MAI-UI prompting, maps 0-999 coords to pixels, safety check, logs every run |
| `mos/maiui.py` | GPU server | MAI-UI prompt and output format, matching the official agent |
| `mos/safety.py` | GPU server | Flags taps on Send/Pay/Delete-like buttons, typing into password fields, card numbers |
| `mos/loopguard.py` | GPU server | Notices actions that didn't change the screen and ends runs that are stuck repeating them |
| `android/` | phones | The app: accessibility service (screen, UI tree, gestures, text, keys), overlay, settings |
| `mos/remote.py` | your laptop | Same protocol as the app, but over ADB. Use it to test on the emulator |
| `mos/export_sft.py` | GPU server | Turns successful runs into fine-tuning data (Tier 1) |
| `mos/agent.py` | anywhere | The original Claude-driven agent, kept as a baseline |

## 1. Model server (rented GPU)

Run it on a rented cloud GPU and stop the instance when you're not testing. Owning
hardware only pays off once usage is steady (see the plan doc). You need an NVIDIA
GPU with 24 GB or more (L4, A10, RTX 4090, L40S, A100...). MAI-UI-8B in bf16 is about 17 GB.

One command on the instance, ideally with a persistent volume at `MOS_DATA`:

```bash
git clone https://github.com/joonk7809/mos-agent && cd mos-agent
TS_AUTHKEY=tskey-auth-... MOS_DATA=/workspace/mos ./scripts/start_gpu_server.sh
```

[`scripts/start_gpu_server.sh`](scripts/start_gpu_server.sh) installs vLLM (pinned to
0.11.0 per MAI-UI's instructions), joins your tailnet, starts MAI-UI-8B and then the
MOS server, and prints the URL and token for phones. Model weights, run logs and the
token live in `MOS_DATA`, so a restarted instance comes back without re-downloading.
Get the auth key from the Tailscale admin console (Settings → Keys), and share the
`mos-gpu` machine with friends there. Don't expose port 8080 publicly.

The same thing by hand:

```bash
pip install vllm==0.11.0 "transformers>=4.57.0" && pip install -r requirements.txt
python -m vllm.entrypoints.openai.api_server --model Tongyi-MAI/MAI-UI-8B \
  --served-model-name MAI-UI-8B --host 127.0.0.1 --port 8000 \
  --max-model-len 16384 --gpu-memory-utilization 0.90 --trust-remote-code &
export MOS_TOKEN=$(openssl rand -hex 16)     # give this to each phone
MOS_LLM_URL=http://127.0.0.1:8000/v1 uvicorn mos.server:app --host 0.0.0.0 --port 8080
```

Settings (env vars): `MOS_TOKEN` (required), `MOS_LLM_URL`, `MOS_LLM_MODEL`,
`MOS_RUNS` (log folder, default `runs/server`), `MOS_MAX_STEPS` (default 40),
`MOS_NO_EFFECT_HINT` (`1` tells the model when its last action changed nothing; off
until the evals show it helps).

A run ends with `success` or `fail` (the model's own verdict), `stopped` (you said no),
`stuck` (the same action did nothing three times in a row, or it kept waiting on a
frozen screen), `max_steps`, or `error`.

## 2. Try it on the emulator first

With the emulator running (`adb devices` shows it), the GPU server up, and your laptop on the same tailnet:

```bash
python -m mos.remote "Turn on dark theme" --server http://mos-gpu:8080 --token $MOS_TOKEN
python -m evals.run_evals --server http://mos-gpu:8080 --token $MOS_TOKEN   # scored by device state
```

`evals/run_evals.py` loads the emulator snapshot named `clean` before each task and
judges each run by device state (`adb shell` checks in `evals/tasks.jsonl`). If a task
already looks done on the fresh snapshot, the run is marked invalid instead of counted.
Results land in `runs/evals/<time>/` with a summary: success rate with a 95% interval,
pass^k (every repeat of a task succeeded), and the false-success rate (the agent said
done but the device disagrees). With ~10 tasks, use `--repeats 3` and only trust
differences between configs whose intervals don't overlap much. `--only <ids>` runs a
subset, `--label` notes what you changed.

## 3. The phone app

Build it, or download `mos-debug-apk` from the latest CI run on GitHub (Actions tab):

```bash
cd android && ./gradlew :app:assembleDebug     # needs Android Studio / the Android SDK
adb install app/build/outputs/apk/debug/app-debug.apk
```

On each phone (Android 11 or newer):

1. Install the APK. Allow installing from your file manager or browser when asked.
2. Open **MOS**, tap **Open accessibility settings**, and turn on **MOS agent**.
   On Android 13+ a sideloaded app's accessibility toggle is greyed out at first. Go to
   Settings → Apps → MOS → ⋮ → **Allow restricted settings**, then turn it on.
3. Back in MOS, enter the server URL and token, type a task, and tap **Run**.
   MOS moves to the background and a panel at the top shows each step,
   with **Allow / Deny** when the agent wants to send, pay or delete.

## 4. Getting better on your apps (Tier 1)

Every run is saved under `runs/server/<session>/`. When a task fails, fix the bad
step's `args` in `steps.jsonl` (or redo the task and keep the good run), mark the
session successful in `final.json`, then:

```bash
python -m mos.export_sft runs/server --out data/sft.jsonl
```

That writes MAI-UI-format samples (`{"messages", "images"}`, the ms-swift /
LLaMA-Factory multimodal layout) for a QLoRA fine-tune on the same rented GPU (a few hours).

## Tests

```bash
pip install -r requirements.txt && python -m pytest                 # Python tests
cd android && ./gradlew -Pmos.coreOnly :core:test                   # protocol tests, no Android SDK needed
```

CI (`.github/workflows/ci.yml`) runs both and builds the APK.

## Known limits

- Accessibility can't capture `FLAG_SECURE` screens (banking, some password fields).
  They come through black, so the agent can't work inside those apps.
- `type` replaces the focused field's text with its current text plus the new text.
  Apps that ignore `ACTION_SET_TEXT` need a keyboard (IME) fallback, which isn't built yet.
- `menu` maps to Recents, since there is no menu key on modern Android.
- The safety check is a keyword heuristic on the tapped element. It will miss some
  risky taps and flag some harmless ones.
- Only one task runs at a time per phone. The server keeps sessions in memory, so
  restarting it ends running tasks (their logs stay).
