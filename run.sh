#!/usr/bin/env bash
#
# Starts the ACRES intake app locally and exposes it through a Cloudflare
# quick tunnel so colleagues can reach it over the internet.
#
# Usage:
#   ./run.sh                       # open access
#   ACRES_ACCESS_CODE=team123 ./run.sh   # require an access code
#
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=".venv/bin/streamlit"
if [ ! -x "$VENV_PY" ]; then
  echo "Virtualenv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# 1. Ensure Ollama is running.
if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "Starting Ollama..."
  (ollama serve >/tmp/ollama_serve.log 2>&1 &)
  sleep 3
fi

# 2. Ensure the model is available (first run only).
if ! ollama list 2>/dev/null | grep -q "llama3.1"; then
  echo "Pulling llama3.1 (first run only, ~4.9 GB)..."
  ollama pull llama3.1
fi

# 3. Warn if OCR engine is missing (needed for scanned PDFs).
if ! command -v tesseract >/dev/null 2>&1; then
  echo "NOTE: tesseract not found. Scanned PDFs will not be readable."
  echo "      Install it with: brew install tesseract"
fi

# 4. Start Streamlit.
echo "Starting the app on http://localhost:8501 ..."
.venv/bin/streamlit run app.py --server.headless true --server.port 8501 \
  >/tmp/acres_app.log 2>&1 &
STREAMLIT_PID=$!
trap 'echo; echo "Stopping app (pid $STREAMLIT_PID)..."; kill $STREAMLIT_PID 2>/dev/null || true' EXIT
sleep 5

if ! curl -sf http://localhost:8501/ >/dev/null 2>&1; then
  echo "The app did not start. Check /tmp/acres_app.log"
  exit 1
fi

# 5. Open the public tunnel (foreground). Copy the https://*.trycloudflare.com URL.
echo "Share the https://...trycloudflare.com URL below with your colleagues."
echo "Press Ctrl+C to stop."
echo
cloudflared tunnel --url http://localhost:8501
