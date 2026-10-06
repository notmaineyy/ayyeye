# ACRES Clearance Automated Intake

A local Streamlit intake form that uses a local LLM (Ollama + `llama3.1`) to
help raise ACRES security clearance requests. No cloud AI services are used.

## What it does

- **AI Intake Assistant**: paste an email/message/notes and the local AI fills
  the whole form; you review and correct.
- Collects the ACRES template fields (applicant, project, clearance, MSD and
  nature-of-involvement details).
- Auto-fills project-derived fields (location, end date, ops manager,
  clearance level) from `projects.csv`.
- Reads attached PDF/image documents and extracts religion, gender, mobile
  number, appointment/designation and MSD numbers/dates using the local model.
- Expands a short description of work into a professional sentence.
- Produces a copy-ready row and saves it as `<Name>_ACRES_ready.csv`.
- Shows who to send the completed form to for feedback.

## Prerequisites

- macOS with Python 3.11+
- [Ollama](https://ollama.com) installed (`brew install --cask ollama`)
- `cloudflared` for sharing (`brew install cloudflared`)
- `tesseract` for OCR of scanned PDFs (`brew install tesseract`)

## First-time setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run locally

```bash
.venv/bin/streamlit run app.py
```

Then open http://localhost:8501.

## Deploy to Streamlit Community Cloud (public URL)

1. Push this repo to GitHub.
2. Go to https://share.streamlit.io and click **New app**.
3. Pick the repo/branch and set the main file to `app.py`.
4. Open **Advanced settings → Secrets** and add:

   ```toml
   OPENAI_API_KEY = "sk-your-key-here"
   # OPENAI_BASE_URL = "https://api.openai.com/v1"   # optional
   # OPENAI_MODEL = "gpt-4o-mini"
   ACRES_ACCESS_CODE = "choose-a-code"
   ```

5. Deploy. `packages.txt` installs Tesseract (OCR) automatically; the app uses
   the cloud model when `OPENAI_API_KEY` is set, otherwise local Ollama.

> Note: on the cloud there is no Ollama, so an OpenAI-compatible key is
> required for the AI features. Clearance data sent to that provider leaves
> your machine — only use dummy data while testing.

## Share with colleagues (local tunnel)

```bash
./run.sh
```

This starts Ollama (if needed), pulls `llama3.1` on first run, launches the app,
and opens a Cloudflare quick tunnel. Copy the printed
`https://<random>.trycloudflare.com` URL and send it to your colleagues.

To require an access code (recommended for a public link):

```bash
ACRES_ACCESS_CODE=choose-a-code ./run.sh
```

Share the URL and the code separately.

## Notes and limitations

- **Your Mac must stay awake and online** while colleagues are testing, because
  the app and the model run on your machine.
- Submissions are saved as CSV files in this project folder.
- The local model is occasionally inconsistent and may miss a field; the form
  will flag anything missing so it can be filled in manually.
- Scanned/image-only PDFs and image files (PNG/JPG) are supported via OCR
  (Tesseract); this is slower than reading text-based PDFs.
- Quick tunnels are temporary — the URL changes each time you restart `run.sh`.

## Feedback for testers

Ask testers to note: which fields were unclear, whether the AI-extracted values
were correct, and any missing columns for the real ACRES template.
