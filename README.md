# MOS: a phone agent for you and your friends

An open-weight model (MAI-UI-8B) on a home GPU drives Android phones. Each phone
runs the MOS app, which uses Android's accessibility APIs to send its screen to the
server and carry out the actions the server sends back: taps, swipes, typing,
opening apps. The agent asks before anything that looks like sending, paying or
deleting, and the overlay has a Stop button.

```
 phone (MOS app)                         home server
 ───────────────                         ───────────
 screenshot + UI tree  ── Tailscale ──▶  mos.server (FastAPI) ──▶ vLLM: MAI-UI-8B
 tap / swipe / type    ◀──────────────   next action + "ask first?" check
```

| Part | Where | What it does |
|---|---|---|
| `mos/server.py` | home server | Sessions, MAI-UI prompting, maps 0-999 coords to pixels, safety check, logs every run |
| `mos/maiui.py` | home server | MAI-UI prompt and output format, matching the official agent |
| `mos/safety.py` | home server | Flags taps on Send/Pay/Delete-like buttons, typing into password fields, card numbers |
| `android/` | phones | The app: accessibility service (screen, UI tree, gestures, text, keys), overlay, settings |
| `mos/remote.py` | your laptop | Same protocol as the app, but over ADB. Use it to test on the emulator |
| `mos/export_sft.py` | home server | Turns successful runs into fine-tuning data (Tier 1) |
| `mos/agent.py` | anywhere | The original Claude-driven agent, kept as a baseline |

## 1. Home server (GPU box)

You need an NVIDIA GPU with 24 GB (e.g. a used RTX 3090). MAI-UI-8B in bf16 is about 17 GB.

```bash
# the model, served with an OpenAI-compatible API (MAI-UI's instructions pin vLLM 0.11.0)
pip install vllm==0.11.0 "transformers>=4.57.0"
python -m vllm.entrypoints.openai.api_server --model Tongyi-MAI/MAI-UI-8B \
  --served-model-name MAI-UI-8B --host 127.0.0.1 --port 8000 \
  --max-model-len 16384 --gpu-memory-utilization 0.90 --trust-remote-code

# the MOS server, in this repo
pip install -r requirements.txt
export MOS_TOKEN=$(openssl rand -hex 16)     # give this to each phone
MOS_LLM_URL=http://127.0.0.1:8000/v1 uvicorn mos.server:app --host 0.0.0.0 --port 8080
```

Install [Tailscale](https://tailscale.com) on the server and every phone, and share the
server with friends from the Tailscale admin page. Phones then use
`http://<server's 100.x tailscale IP>:8080`. Don't expose port 8080 to the internet.

Settings (env vars): `MOS_TOKEN` (required), `MOS_LLM_URL`, `MOS_LLM_MODEL`,
`MOS_RUNS` (log folder, default `runs/server`), `MOS_MAX_STEPS` (default 40).

## 2. Try it on the emulator first

With the emulator running (`adb devices` shows it) and the server up:

```bash
python -m mos.remote "Turn on dark theme" --server http://localhost:8080 --token $MOS_TOKEN
python -m evals.run_evals --server http://localhost:8080 --token $MOS_TOKEN   # scored by device state
```

`evals/run_evals.py` loads the emulator snapshot named `clean` before each task.

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
LLaMA-Factory multimodal layout) for a QLoRA fine-tune on the same 3090.

## Tests

```bash
pip install -r requirements.txt && pytest                           # 20 Python tests
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
