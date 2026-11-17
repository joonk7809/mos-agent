#!/usr/bin/env bash
# Start MOS on a rented GPU instance: vLLM + the MOS server, reachable over Tailscale.
#
#   git clone https://github.com/joonk7809/mos-agent && cd mos-agent
#   TS_AUTHKEY=tskey-auth-... MOS_DATA=/workspace/mos ./scripts/start_gpu_server.sh
#   MOS_POLICY=guiowl TS_AUTHKEY=... ./scripts/start_gpu_server.sh     # GUI-Owl-1.5-8B instead of MAI-UI-8B
#
# Needs: an NVIDIA GPU with >= 24 GB, Python 3.10+, and ideally a persistent volume
# for MOS_DATA (model weights, run logs and the token survive restarts there).
# Stop the instance when you're done testing: rented GPUs bill by the hour.
set -euo pipefail

MOS_DATA="${MOS_DATA:-$HOME/mos-data}"
export MOS_POLICY="${MOS_POLICY:-maiui}"
if [[ "$MOS_POLICY" == "guiowl" ]]; then
  MODEL="${MOS_MODEL:-mPLUG/GUI-Owl-1.5-8B-Instruct}"; SERVED=GUI-Owl-1.5-8B-Instruct
  MAX_LEN="${MOS_MAX_LEN:-24576}"; IMAGES=5   # GUI-Owl keeps the last 5 screenshots
  # Phones and mos.remote send 720x1600 screenshots, ~1.1k tokens each, so 5 fit easily.
  # Unverified on a 24 GB card: if vLLM says the KV cache doesn't fit, lower MOS_MAX_LEN.
else
  MODEL="${MOS_MODEL:-Tongyi-MAI/MAI-UI-8B}"; SERVED=MAI-UI-8B
  MAX_LEN="${MOS_MAX_LEN:-16384}"; IMAGES=3
fi
mkdir -p "$MOS_DATA/hf" "$MOS_DATA/runs" "$MOS_DATA/logs"
export HF_HOME="$MOS_DATA/hf"   # cache weights on the persistent volume (~17 GB)

# 1. Python deps (skipped if already installed)
python3 -c "import vllm" 2>/dev/null || pip install -q "vllm==0.11.0" "transformers>=4.57.0"
python3 -c "import fastapi, uvicorn" 2>/dev/null || pip install -q -r requirements.txt

# 2. Token phones use; created once and kept
if [[ ! -f "$MOS_DATA/token" ]]; then (umask 077; openssl rand -hex 16 > "$MOS_DATA/token"); fi
chmod 600 "$MOS_DATA/token"
# vLLM gets its own key: in userspace mode Tailscale forwards every tailnet port to localhost,
# so without it any tailnet peer could use the GPU directly on :8000, skipping MOS's token.
if [[ ! -f "$MOS_DATA/vllm_key" ]]; then (umask 077; openssl rand -hex 16 > "$MOS_DATA/vllm_key"); fi
VLLM_KEY="$(cat "$MOS_DATA/vllm_key")"
export MOS_TOKEN="$(cat "$MOS_DATA/token")"

# 3. Tailscale, so phones reach this box without opening a public port.
#    Userspace networking works inside containers that have no /dev/net/tun.
if [[ -n "${TS_AUTHKEY:-}" ]]; then
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  pgrep tailscaled >/dev/null || (tailscaled --tun=userspace-networking --state="$MOS_DATA/tailscaled.state" \
    > "$MOS_DATA/logs/tailscaled.log" 2>&1 &)
  sleep 3
  tailscale up --authkey="$TS_AUTHKEY" --hostname="${TS_HOSTNAME:-mos-gpu}"
fi

# 4. The model
if ! curl -sf localhost:8000/v1/models >/dev/null; then
  nohup python3 -m vllm.entrypoints.openai.api_server --model "$MODEL" \
    --served-model-name "$SERVED" --host 127.0.0.1 --port 8000 --api-key "$VLLM_KEY" \
    --max-model-len "$MAX_LEN" --limit-mm-per-prompt "{\"image\": $IMAGES}" \
    --max-num-seqs 4 \
    --gpu-memory-utilization 0.90 --trust-remote-code \
    > "$MOS_DATA/logs/vllm.log" 2>&1 &
  echo "Loading the model (first run downloads ~17 GB)..."
  until curl -sf -H "Authorization: Bearer $VLLM_KEY" localhost:8000/v1/models >/dev/null; do sleep 5; done
fi

# 5. The MOS server. It listens on localhost only, so it is never reachable from the public
#    internet. Userspace Tailscale delivers tailnet connections to localhost ports, which is how
#    phones reach it at http://mos-gpu:8080.
if ! tailscale status >/dev/null 2>&1; then
  echo "WARNING: Tailscale isn't up, so phones can't reach this server (it only listens on localhost)."
fi
echo
echo "Phones: server http://$(tailscale ip -4 2>/dev/null | head -1 || echo '<this-host>'):8080"
echo "        token  in $MOS_DATA/token  (run: cat $MOS_DATA/token)"
echo
MOS_LLM_URL=http://127.0.0.1:8000/v1 MOS_LLM_MODEL="$SERVED" MOS_LLM_KEY="$VLLM_KEY" MOS_RUNS="$MOS_DATA/runs" \
  exec python3 -m uvicorn mos.server:app --host 127.0.0.1 --port 8080
