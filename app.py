"""ACRES Clearance Automated Intake.

A local-only Streamlit intake form for ACRES clearance. Users enter the
template fields below; a locally hosted Ollama model expands their brief
description of work into a professional sentence. Project-derived fields
(location, end date, ops manager, clearance level) are filled automatically
from ``projects.csv`` and compiled into a row matching the ACRES layout.

Conditional fields are shown only when applicable:
  * NRIC / HP & email      - local (Singapore) applicants only.
  * Country ID number      - foreign applicants only.
  * Foreigners visit ref.  - applicants from the listed countries only.

No cloud services are contacted.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import os
import re
from pathlib import Path

import ollama
import openai
import pandas as pd
import pymupdf
import pytesseract
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Locally hosted model used to draft the nature of involvement.
OLLAMA_MODEL = "llama3.1"

#: Local database of static project details.
PROJECTS_CSV = Path(__file__).with_name("projects.csv")

#: Selectable values for the purpose of clearance.
PURPOSE_OPTIONS = ["Tender Participation", "Project Involvement", "OPS/ONPS/Temp"]

#: Selectable values for the type of service.
TYPE_OF_SVC_OPTIONS = [
    "Civilian (DSO NL)",
    "Civilian (DSTA)",
    "Civilian (Pte Co)",
    "Civilian (ST)",
    "Civilian (Seconded Personnel)",
]

#: Countries whose applicants must provide a foreigners visit reference.
FOREIGN_VISIT_COUNTRIES = {
    "Australia": ("australia", "australian"),
    "France": ("france", "french"),
    "Germany": ("germany", "german"),
    "Italy": ("italy", "italian"),
    "Netherlands": ("netherlands", "dutch", "holland"),
    "New Zealand": ("new zealand", "zealander"),
    "Norway": ("norway", "norwegian"),
    "Spain": ("spain", "spanish"),
    "Sweden": ("sweden", "swedish"),
    "United Kingdom": ("united kingdom", "uk", "british", "britain", "england"),
    "United States": ("united states", "usa", "american"),
    "Switzerland": ("switzerland", "swiss"),
    "NATO": ("nato",),
    "Israel": ("israel", "israeli"),
}

#: Selectable values for gender.
GENDER_OPTIONS = ["Male", "Female", "Other"]

#: Example phrases shown as a hint for the free-text job description.
JOB_EXAMPLE = (
    "e.g. calibrate radar equipment; run field tests; liaise with vendor; "
    "prepare test reports"
)

#: ACRES output columns, in template order.
ACRES_COLUMNS = [
    "NAME",
    "NRIC",
    "PURPOSE OF CLEARANCE",
    "PROJECT NAME",
    "START DATE",
    "END DATE",
    "LOCATION OF WORK IN DETAIL",
    "OPS MANAGER ENDORSEMENT",
    "FOREIGNERS VISIT REFERENCE",
    "COMPANY NAME",
    "TYPE OF SVC",
    "NATIONALITY",
    "COUNTRY OF BIRTH",
    "DATE OF BIRTH",
    "RELIGION",
    "GENDER",
    "MOBILE NUMBER",
    "CLEARANCE LEVEL",
    "START DATE OF EMPLOYMENT",
    "CAT 1 MSD NO",
    "CAT 1 MSD DATE",
    "PROJECT CLEARANCE MSD NO",
    "PROJECT CLEARANCE MSD DATE",
    "HP AND EMAIL ADDRESS",
    "COMPANY APPT",
    "NATURE OF INVOLVEMENT",
    "COUNTRY IDENTIFICATION NO",
    "REMARKS",
]

#: Default company name and address / UEN.
COMPANY_NAME = "DSTA (1 Depot Road S109679)"

#: Who completed forms should be sent to for now (the current middle man).
MIDDLE_MAN_NAME = "the ACRES clearance coordinator (current middle man)"
MIDDLE_MAN_EMAIL = "acres-coordinator@example.sg"


# ---------------------------------------------------------------------------
# Model provider
#
# Locally the app uses Ollama. In the cloud (e.g. Streamlit Community Cloud)
# there is no Ollama, so if an API key is configured the app uses a cloud model
# instead. Precedence (first key found wins):
#   * OpenCode Go          - OPENCODE_API_KEY            (OpenAI-compatible)
#   * Gemini (free tier)   - GEMINI_API_KEY / GOOGLE_API_KEY
#   * OpenAI-compatible    - OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
#                            (also works for Groq / OpenRouter / Together)
# ---------------------------------------------------------------------------

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_DEFAULT_MODEL = "deepseek-v4-flash"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"


def get_secret(name: str) -> str:
    """Read a setting from Streamlit secrets, then the environment."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001 - no secrets file configured
        pass
    return os.environ.get(name, "")


def cloud_model_config() -> dict | None:
    """Return cloud model settings when an API key is configured.

    OpenCode Go takes precedence, then Gemini, then any OpenAI-compatible key.
    """
    opencode_key = (
        get_secret("OPENCODE_API_KEY").strip()
        or get_secret("OPENCODE_GO_API_KEY").strip()
    )
    if opencode_key:
        return {
            "provider": "OpenCode Go",
            "api_key": opencode_key,
            "base_url": get_secret("OPENCODE_BASE_URL").strip() or OPENCODE_GO_BASE_URL,
            "model": get_secret("OPENCODE_MODEL").strip()
            or OPENCODE_GO_DEFAULT_MODEL,
        }

    gemini_key = (
        get_secret("GEMINI_API_KEY").strip() or get_secret("GOOGLE_API_KEY").strip()
    )
    if gemini_key:
        return {
            "provider": "Gemini",
            "api_key": gemini_key,
            "base_url": get_secret("OPENAI_BASE_URL").strip() or GEMINI_BASE_URL,
            "model": get_secret("OPENAI_MODEL").strip()
            or get_secret("GEMINI_MODEL").strip()
            or GEMINI_DEFAULT_MODEL,
        }

    api_key = get_secret("OPENAI_API_KEY").strip()
    if api_key:
        return {
            "provider": "OpenAI-compatible",
            "api_key": api_key,
            "base_url": get_secret("OPENAI_BASE_URL").strip() or None,
            "model": get_secret("OPENAI_MODEL").strip() or "gpt-4o-mini",
        }
    return None


def active_provider() -> str:
    """Human-readable name of the active model provider."""
    config = cloud_model_config()
    if config:
        return f"{config['provider']} ({config['model']})"
    return f"local Ollama ({OLLAMA_MODEL})"


def _extract_json(text: str) -> str:
    """Pull a JSON object out of a model reply (strips markdown fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def chat(messages: list[dict], schema: dict | None = None) -> str:
    """Send a chat request to the active provider and return the text reply.

    When ``schema`` is given, the provider is asked to return JSON matching it.
    """
    config = cloud_model_config()
    if config:
        client = openai.OpenAI(api_key=config["api_key"], base_url=config["base_url"])
        kwargs: dict = {"model": config["model"], "messages": messages}
        if schema is not None:
            messages = list(messages)
            messages[0] = {
                "role": "system",
                "content": (
                    messages[0]["content"]
                    + "\nReturn ONLY a valid JSON object matching this JSON "
                    "schema (no markdown): " + json.dumps(schema)
                ),
            }
            kwargs["messages"] = messages
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception:
            # Some providers reject response_format; retry without it.
            kwargs.pop("response_format", None)
            response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    kwargs = {"model": OLLAMA_MODEL, "messages": messages}
    if schema is not None:
        kwargs["format"] = schema
    response = ollama.chat(**kwargs)
    return response["message"]["content"]

#: Validation patterns.
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,}$")
NRIC_PATTERN = re.compile(r"^[STFGM]\d{7}[A-Z]$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MOBILE_PATTERN = re.compile(r"^[+]?[0-9][0-9 \-]{6,19}$")

# ---------------------------------------------------------------------------
# Brand styling (Deep Navy #18418A, Vibrant Orange #F26522, Teal #009E73)
# ---------------------------------------------------------------------------

BRAND_CSS = """
<style>
:root {
    --acres-navy: #18418A;
    --acres-orange: #F26522;
    --acres-orange-dark: #D9541A;
    --acres-teal: #009E73;
    --acres-gray: #F4F6F8;
}

/* Force a clean, light canvas regardless of the viewer's OS theme. */
html, body, .stApp,
[data-testid="stAppViewContainer"],
[data-testid="stHeader"],
[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    color: #1A1A1A !important;
}

/* Brand headers. */
h1, h2, h3, h4 {
    color: var(--acres-navy) !important;
    font-weight: 700 !important;
    letter-spacing: 0.2px;
}

/* Title block with a navy rule underneath. */
.acres-title {
    border-bottom: 3px solid var(--acres-navy);
    padding-bottom: 0.5rem;
    margin-bottom: 1rem;
}
.acres-title h1 {
    margin-bottom: 0.15rem;
}
.acres-subtitle {
    color: #5A6472;
    font-size: 0.95rem;
}

/* Instruction panel. */
.acres-instructions {
    background-color: var(--acres-gray);
    border-left: 6px solid var(--acres-navy);
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin-bottom: 1.25rem;
}
.acres-instructions p {
    margin: 0 0 0.5rem 0;
    font-weight: 600;
    color: var(--acres-navy);
}
.acres-instructions ul {
    margin: 0;
    padding-left: 1.15rem;
}
.acres-instructions li {
    margin-bottom: 0.2rem;
}

/* Vibrant orange primary buttons. */
.stButton > button,
div[data-testid="stButton"] > button {
    background-color: var(--acres-orange) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 6px !important;
    padding: 0.6rem 1.8rem !important;
    font-weight: 600 !important;
    font-size: 1rem !important;
    min-width: 220px;
}
.stButton > button:hover,
div[data-testid="stButton"] > button:hover {
    background-color: var(--acres-orange-dark) !important;
    color: #FFFFFF !important;
}
.stButton > button:focus,
div[data-testid="stButton"] > button:focus {
    box-shadow: 0 0 0 0.2rem rgba(242, 101, 34, 0.35) !important;
}

/* Teal success highlight. */
.acres-success {
    background-color: #E6F5F0;
    border-left: 6px solid var(--acres-teal);
    border-radius: 6px;
    padding: 1rem 1.25rem;
    color: #00614A;
    font-weight: 600;
    margin-top: 0.75rem;
}

/* Section captions and inputs. */
.stTextArea textarea, .stTextInput input {
    border-radius: 6px !important;
}

/* AI assistant panel. */
.acres-ai {
    background: linear-gradient(135deg, #EAF0FB 0%, #FFF3EC 100%);
    border: 1px solid var(--acres-navy);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
}
.acres-ai h3 {
    margin: 0 0 0.25rem 0;
}
.acres-ai p {
    margin: 0;
    color: #4A5464;
}
.acres-badge {
    display: inline-block;
    background-color: var(--acres-navy);
    color: #FFFFFF;
    border-radius: 999px;
    padding: 0.1rem 0.6rem;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.4px;
    margin-right: 0.4rem;
    vertical-align: middle;
}

/* Footer / next steps. */
.acres-footer {
    background-color: var(--acres-gray);
    border-left: 6px solid var(--acres-teal);
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin-top: 1.5rem;
}
.acres-footer h4 {
    margin: 0 0 0.4rem 0;
}
.acres-footer p, .acres-footer li {
    margin: 0.2rem 0;
    color: #33404F;
}
</style>
"""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def is_local(nationality: str) -> bool:
    """Return True for Singapore applicants."""
    return "singapore" in nationality.lower()


def needs_foreign_visit_reference(nationality: str) -> bool:
    """Return True if the nationality is on the visit-reference country list."""
    text = nationality.lower()
    return any(
        term in text
        for terms in FOREIGN_VISIT_COUNTRIES.values()
        for term in terms
    )


def is_valid_date(value: str) -> bool:
    """Validate a YYYY-MM-DD date string."""
    if not DATE_PATTERN.match(value):
        return False
    try:
        _dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def normalize_date(value: str | None) -> str:
    """Best-effort conversion of a date string to canonical YYYY-MM-DD.

    Returns an empty string when the value cannot be parsed.
    """
    if not value:
        return ""
    # ISO 8601 (including datetimes and timezones, e.g. 2025-02-01T00:00:00Z).
    try:
        parsed = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
        return parsed.isoformat() if 1900 <= parsed.year <= 2100 else ""
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y",
    ):
        try:
            parsed = _dt.datetime.strptime(value.strip(), fmt).date()
            return parsed.isoformat() if 1900 <= parsed.year <= 2100 else ""
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(value).date()
        return parsed.isoformat() if 1900 <= parsed.year <= 2100 else ""
    except Exception:  # noqa: BLE001 - fall through to empty
        return ""


#: Fields the model attempts to read out of uploaded documents.
class DocumentDetails(BaseModel):
    """Structured details extracted from an uploaded PDF/email document."""

    name: str | None = Field(default=None, description="Full name")
    nric: str | None = Field(default=None, description="NRIC or FIN")
    nationality: str | None = Field(default=None, description="Nationality")
    country_of_birth: str | None = Field(default=None, description="Country of birth")
    date_of_birth: str | None = Field(default=None, description="Date of birth")
    religion: str | None = Field(default=None, description="Religion")
    gender: str | None = Field(default=None, description="Gender")
    mobile_number: str | None = Field(default=None, description="Mobile number")
    appointment_designation: str | None = Field(
        default=None, description="Appointment or designation"
    )
    start_date_of_employment: str | None = Field(
        default=None, description="Start date of employment"
    )
    cat1_msd_no: str | None = Field(default=None, description="CAT 1 MSD number")
    cat1_msd_date: str | None = Field(default=None, description="CAT 1 MSD date")
    project_clearance_msd_no: str | None = Field(
        default=None, description="Project clearance MSD number"
    )
    project_clearance_msd_date: str | None = Field(
        default=None, description="Project clearance MSD date"
    )
    clearance_start_date: str | None = Field(
        default=None, description="Start date of the clearance"
    )
    clearance_end_date: str | None = Field(
        default=None, description="End date of the clearance"
    )


def ocr_available() -> bool:
    """Return True when the Tesseract OCR engine is installed."""
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001 - any failure means OCR is unavailable
        return False


def extract_text_from_pdf(uploaded_file) -> str:
    """Return the text of an uploaded PDF, OCR-ing scanned pages if needed.

    Text-based PDFs are read directly. Image-only (scanned) PDFs are rendered
    to images at 300 dpi and passed through Tesseract OCR.
    """
    document = pymupdf.open(stream=uploaded_file.read(), filetype="pdf")

    text = "\n".join(page.get_text() for page in document).strip()
    if len(text) >= 40:
        return text

    # Little or no embedded text: treat as scanned and run OCR.
    if not ocr_available():
        raise RuntimeError(
            "This PDF appears to be scanned and needs OCR, but Tesseract is "
            "not installed. Install it with: brew install tesseract"
        )

    ocr_parts = []
    for page in document:
        pixmap = page.get_pixmap(dpi=300)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        ocr_parts.append(pytesseract.image_to_string(image))
    return "\n".join(ocr_parts).strip()


def extract_text_from_image(uploaded_file) -> str:
    """Return the text of an uploaded image (PNG/JPG/etc.) via OCR."""
    if not ocr_available():
        raise RuntimeError(
            "Reading images needs OCR, but Tesseract is not installed. "
            "Install it with: brew install tesseract"
        )
    image = Image.open(uploaded_file)
    return pytesseract.image_to_string(image)


def extract_text_from_document(uploaded_file) -> str:
    """Dispatch to the right reader based on the uploaded file type."""
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if name.endswith(".pdf"):
        return extract_text_from_pdf(uploaded_file)
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")):
        return extract_text_from_image(uploaded_file)
    # Unknown extension: try PDF first, then fall back to image OCR.
    try:
        return extract_text_from_pdf(uploaded_file)
    except Exception:  # noqa: BLE001 - not a PDF, try as an image
        return extract_text_from_image(uploaded_file)


def _msd_bucket(category: str) -> str | None:
    """Map a clearance category value to its MSD field prefix."""
    upper = category.upper()
    if "PROJECT" in upper or "PROJ" in upper:
        return "project_clearance"
    if "CAT" in upper:
        return "cat1"
    return None


#: Column labels used in clearance tables/forms, matched against upper-cased text.
_MSD_COLUMN_PATTERNS = [
    ("category", re.compile(r"CLEARANCE\s+CATEGORY")),
    ("msd_no", re.compile(r"MSD\s+REF(?:ERENCE)?\s*(?:NUMBER|NO\.?)")),
    ("msd_date", re.compile(r"MSD\s+REF(?:ERENCE)?\s*DATE")),
    ("start", re.compile(r"START\s+DATE")),
    ("end", re.compile(r"END\s+DATE")),
]


def _parse_msd_table(lines: list[str]) -> dict[str, str]:
    """Parse a clearance table whose columns are the known MSD labels."""
    result: dict[str, str] = {}

    for header_index, header in enumerate(lines):
        found = []
        for key, pattern in _MSD_COLUMN_PATTERNS:
            match = pattern.search(header.upper())
            if match:
                found.append((match.start(), key))
        if len(found) < 2:
            continue
        found.sort()
        column_keys = [key for _, key in found]

        # Read the data rows that follow the header.
        for row in lines[header_index + 1 :]:
            stripped = row.strip()
            if not stripped:
                continue
            if any(pattern.search(stripped.upper()) for _, pattern in _MSD_COLUMN_PATTERNS):
                break  # reached another header / label section
            cells = re.split(r"\s{2,}|\t+|\s*\|\s*", stripped)
            if len(cells) != len(column_keys):
                continue
            values = dict(zip(column_keys, [cell.strip() for cell in cells]))

            category = values.get("category", "")
            prefix = _msd_bucket(category)
            if not prefix:
                continue
            if values.get("msd_no"):
                result[f"{prefix}_msd_no"] = values["msd_no"].strip(":;#-")
            if values.get("msd_date"):
                result[f"{prefix}_msd_date"] = (
                    normalize_date(values["msd_date"]) or values["msd_date"]
                )
            if values.get("start"):
                result.setdefault(
                    "clearance_start_date",
                    normalize_date(values["start"]) or values["start"],
                )
            if values.get("end"):
                result.setdefault(
                    "clearance_end_date",
                    normalize_date(values["end"]) or values["end"],
                )
        if result:
            return result
    return result


def _label_value(line: str, label_pattern: str) -> str:
    """Return the value following a label on a line (after ``:``/``-``)."""
    match = re.match(
        rf"(?i)\s*{label_pattern}\s*[:\-]?\s*(.*)", line
    )
    if match:
        return match.group(1).strip().strip(",;").strip()
    if ":" in line:
        return line.split(":", 1)[1].strip().strip(",;").strip()
    return ""


#: Matches any known clearance label so prose can be split onto separate lines.
_LABEL_BOUNDARY = re.compile(
    r"(?i)(?=(?:clearance\s+category"
    r"|msd\s+ref(?:erence)?\s*(?:number|no\.?|date)"
    r"|start\s+date(?:\s+of\s+employment)?"
    r"|end\s+date)\b)"
)

#: A bare label with no value (used to reject accidental "values").
_LABEL_ONLY = re.compile(
    r"(?i)^(clearance\s+category"
    r"|msd\s+ref(?:erence)?\s*(?:number|no\.?|date)"
    r"|start\s+date(\s+of\s+employment)?"
    r"|end\s+date)\s*$"
)


def _segment_labels(text: str) -> list[str]:
    """Split prose/tables so each known label starts on its own line."""
    text = _LABEL_BOUNDARY.sub("\n", text)
    return [line.strip() for line in text.splitlines()]


def _parse_msd_labels(lines: list[str]) -> dict[str, str]:
    """Parse MSD / clearance fields from labelled lines."""
    result: dict[str, str] = {}
    current_bucket: str | None = None

    for index, line in enumerate(lines):
        if not line:
            continue
        upper = line.upper()

        if "CATEGORY" in upper:
            value = _label_value(line, r"clearance\s+category")
            current_bucket = _msd_bucket(value) or current_bucket

        # Employment start date (must be checked before generic start date).
        if "EMPLOYMENT" in upper and "START DATE" in upper:
            value = _label_value(line, r"start\s+date\s+of\s+employment")
            if value:
                result.setdefault(
                    "start_date_of_employment", normalize_date(value) or value
                )
            continue

        is_msd_ref = "MSD" in upper and (
            "REF" in upper or "NUMBER" in upper or re.search(r"\bNO\.?\b", upper)
        )
        is_msd_date = "MSD" in upper and "DATE" in upper
        if is_msd_ref or is_msd_date:
            value = _label_value(line, r"msd\s+ref(?:erence)?\s*(?:number|no\.?|date)")
            if not value and index + 1 < len(lines):
                nxt = lines[index + 1].strip()
                if nxt and "MSD" not in nxt.upper() and not _LABEL_ONLY.match(nxt):
                    value = nxt
            if not value or _LABEL_ONLY.match(value):
                continue
            bucket = _msd_bucket(upper) or current_bucket
            if not bucket:
                bucket = "cat1" if "cat1_msd_no" not in result else "project_clearance"
            if is_msd_date:
                result.setdefault(f"{bucket}_msd_date", normalize_date(value) or value)
            else:
                result.setdefault(f"{bucket}_msd_no", value)
            continue

        if "START DATE" in upper and "EMPLOYMENT" not in upper:
            value = _label_value(line, r"start\s+date")
            if value and not _LABEL_ONLY.match(value):
                result.setdefault(
                    "clearance_start_date", normalize_date(value) or value
                )
            continue
        if "END DATE" in upper and "EMPLOYMENT" not in upper:
            value = _label_value(line, r"end\s+date")
            if value and not _LABEL_ONLY.match(value):
                result.setdefault(
                    "clearance_end_date", normalize_date(value) or value
                )
            continue

    return result


#: Date-like tokens (e.g. "30 June 2024", "2024-06-30", "05/03/2025").
_MSD_DATE_TOKEN = re.compile(
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}"
)
#: Reference-like tokens (letters + digits joined by - or /), e.g. MSD-2024-00123.
_MSD_REF_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)+")


def _parse_msd_row(line: str) -> dict[str, str]:
    """Parse a single table row even when OCR collapsed column spacing.

    Identifies the clearance category, the MSD reference number and the first
    date on the row (the MSD reference date).
    """
    upper = line.upper()
    if "PROJECT" in upper:
        prefix = "project_clearance"
    elif re.search(r"\bCAT\s*\d", upper):
        prefix = "cat1"
    else:
        return {}

    refs = [
        match.group(0)
        for match in _MSD_REF_TOKEN.finditer(line)
        if any(ch.isdigit() for ch in match.group(0))
    ]
    dates = _MSD_DATE_TOKEN.findall(line)

    result: dict[str, str] = {}
    if refs:
        result[f"{prefix}_msd_no"] = refs[0].strip(":;#-")
    if dates:
        result[f"{prefix}_msd_date"] = normalize_date(dates[0]) or dates[0]
    # Remaining dates on the row are the clearance start and end dates.
    if len(dates) >= 3:
        result.setdefault("clearance_start_date", normalize_date(dates[1]) or dates[1])
        result.setdefault("clearance_end_date", normalize_date(dates[2]) or dates[2])
    return result


def parse_msd_from_text(text: str) -> dict[str, str]:
    """Deterministically pull MSD reference numbers and dates out of raw text.

    The model is unreliable for these reference codes, so they are parsed
    directly. Supports a clearance table (Clearance Category | MSD Ref Number |
    MSD Reference Date | Start Date | End Date), labelled lines
    (``Label: value`` or value on the following line), and OCR-collapsed rows.
    """
    original = [line.rstrip() for line in text.splitlines()]
    segmented = _segment_labels(text)
    result = _parse_msd_table(original)
    for key, value in _parse_msd_labels(segmented).items():
        result.setdefault(key, value)
    for line in segmented:
        for key, value in _parse_msd_row(line).items():
            result.setdefault(key, value)
    return result


def extract_document_details(text: str) -> dict[str, str]:
    """Read structured clearance details out of document text using the model.

    The document text (from a PDF or email) is sent to the local model, which
    is constrained to the ``DocumentDetails`` schema. Empty values are dropped.
    MSD reference numbers/dates are additionally parsed deterministically and
    fill any gaps the model leaves.
    """
    result: dict[str, str] = {}
    for _ in range(2):  # retry once if the model returns nothing useful
        content = chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract personnel and clearance details from the "
                        "provided document text for an ACRES security clearance "
                        "form. Return only the fields defined by the schema, and "
                        "use null for anything not present. Convert all dates to "
                        "YYYY-MM-DD format."
                    ),
                },
                {"role": "user", "content": text},
            ],
            schema=DocumentDetails.model_json_schema(),
        )
        parsed = DocumentDetails.model_validate_json(_extract_json(content))
        result = {}
        for key, value in parsed.model_dump().items():
            cleaned = value.strip() if isinstance(value, str) else ""
            if key.endswith("date") or key == "date_of_birth":
                cleaned = normalize_date(cleaned)
            if cleaned:
                result[key] = cleaned
        if result:
            break

    # MSD reference numbers/dates are parsed directly and take precedence,
    # since the model is unreliable for these codes.
    result.update(parse_msd_from_text(text))
    return result


@st.cache_data
def load_projects() -> pd.DataFrame:
    """Load and cache the static project database."""
    return pd.read_csv(PROJECTS_CSV)


def lookup_project(project_name: str | None) -> pd.Series | None:
    """Find a project by case-insensitive partial match on its name."""
    if not project_name:
        return None
    projects = load_projects()
    matches = projects[
        projects["Project Name"].str.contains(project_name, case=False, regex=False, na=False)
    ]
    if matches.empty:
        return None
    return matches.iloc[0]


def safe_filename(name: str) -> str:
    """Turn a person's name into a filesystem-safe token."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return cleaned or "Unknown"


def craft_nature(notes: str) -> str:
    """Expand brief notes into one professional full sentence.

    Turns the user's own short description of their work into the
    'NATURE OF INVOLVEMENT' text without inventing unsupported detail.
    """
    response = chat(
        messages=[
            {
                "role": "system",
                "content": (
                    "You rewrite short notes describing a person's project work "
                    "into ONE clear, professional full sentence suitable for a "
                    "security clearance form. Do not include appointment titles "
                    "or abbreviations. Only use the details provided; do not "
                    "invent facts. Return the sentence only, with no quotes, "
                    "bullets, or extra commentary."
                ),
            },
            {"role": "user", "content": notes},
        ],
    )
    return response.strip().strip('"')


def build_acres_row(data: dict) -> pd.DataFrame:
    """Assemble the final ACRES row from the collected fields."""
    row = {
        "NAME": data["name"],
        "NRIC": data.get("nric", ""),
        "PURPOSE OF CLEARANCE": data["purpose_of_clearance"],
        "PROJECT NAME": data["project_name"],
        "START DATE": data["start_date"],
        "END DATE": data["end_date"],
        "LOCATION OF WORK IN DETAIL": data["location_of_work"],
        "OPS MANAGER ENDORSEMENT": data["ops_manager"],
        "FOREIGNERS VISIT REFERENCE": data.get("foreigners_visit_reference", ""),
        "COMPANY NAME": data["company_name"],
        "TYPE OF SVC": data["type_of_svc"],
        "NATIONALITY": data["nationality"],
        "COUNTRY OF BIRTH": data["country_of_birth"],
        "DATE OF BIRTH": data["date_of_birth"],
        "RELIGION": data.get("religion", ""),
        "GENDER": data.get("gender", ""),
        "MOBILE NUMBER": data.get("mobile_number", ""),
        "CLEARANCE LEVEL": data["clearance_level"],
        "START DATE OF EMPLOYMENT": data.get("start_date_of_employment", ""),
        "CAT 1 MSD NO": data.get("cat1_msd_no", ""),
        "CAT 1 MSD DATE": data.get("cat1_msd_date", ""),
        "PROJECT CLEARANCE MSD NO": data.get("project_clearance_msd_no", ""),
        "PROJECT CLEARANCE MSD DATE": data.get("project_clearance_msd_date", ""),
        "HP AND EMAIL ADDRESS": data.get("hp_email", ""),
        "COMPANY APPT": data["company_appt"],
        "NATURE OF INVOLVEMENT": data["nature_of_involvement"],
        "COUNTRY IDENTIFICATION NO": data.get("country_identification_no", ""),
        "REMARKS": data.get("remarks", ""),
    }
    return pd.DataFrame([row], columns=ACRES_COLUMNS)


def normalize_gender(value: str) -> str:
    """Map loose gender values onto the selectable options."""
    text = value.strip().lower()
    if text.startswith("m"):
        return "Male"
    if text.startswith("f"):
        return "Female"
    return value if value in GENDER_OPTIONS else "Other"


def apply_document_details(details: dict[str, str]) -> None:
    """Copy document-extracted details into the form's widget state."""
    mapping = {
        "name": "in_name",
        "nric": "in_nric",
        "nationality": "in_nationality",
        "country_of_birth": "in_country_birth",
        "date_of_birth": "in_dob",
        "religion": "in_religion",
        "gender": "in_gender",
        "mobile_number": "in_mobile",
        "appointment_designation": "in_company_appt",
        "start_date_of_employment": "in_start_employment",
        "cat1_msd_no": "in_cat1_no",
        "cat1_msd_date": "in_cat1_date",
        "project_clearance_msd_no": "in_proj_msd_no",
        "project_clearance_msd_date": "in_proj_msd_date",
        "clearance_start_date": "in_start_date",
        "clearance_end_date": "in_end_date",
    }
    for key, widget_key in mapping.items():
        value = details.get(key)
        if not value:
            continue
        if key == "gender":
            value = normalize_gender(value)
        st.session_state[widget_key] = value


def _extract_fields(text: str, source: str) -> tuple[str, str, dict[str, str]]:
    """Extract details from text.

    Returns ``(kind, message, details)`` without applying anything, so callers
    can apply the details at a safe point in the script run.
    """
    text = (text or "").strip()
    if not text:
        return ("info", f"Add some text in the {source} box first.", {})
    try:
        details = extract_document_details(text)
    except Exception as exc:  # noqa: BLE001 - surface model errors to the user
        return ("error", f"The assistant could not process that: {exc}", {})
    st.session_state["last_details"] = details
    if details:
        return (
            "success",
            f"Assistant filled {len(details)} field(s) from your {source}: "
            + ", ".join(sorted(details)),
            details,
        )
    return (
        "warning",
        "The assistant could not find any clearance details in that text.",
        {},
    )


def on_ai_autofill() -> None:
    """Callback: fill the form from the pasted free text.

    Runs before the form fields are created, so it can apply directly.
    """
    kind, message, details = _extract_fields(
        st.session_state.get("ai_text", ""), "pasted text"
    )
    if details:
        apply_document_details(details)
    st.session_state["ai_message"] = (kind, message)


def read_uploaded_documents(uploaded) -> tuple[str, list[str]]:
    """Return (text, errors) for a list of uploaded PDF/image documents."""
    text_parts: list[str] = []
    errors: list[str] = []
    for doc in uploaded:
        try:
            text_parts.append(extract_text_from_document(doc))
        except Exception as exc:  # noqa: BLE001 - surface read errors
            errors.append(f"{doc.name}: {exc}")
    return "\n".join(text_parts).strip(), errors


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------


def render_header() -> None:
    """Render the branded page title."""
    st.markdown(
        """
        <div class="acres-title">
            <h1>ACRES Clearance Automated Intake</h1>
            <div class="acres-subtitle">
                Secure Access &amp; Clearance Request System &mdash; Intake Form
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_instructions() -> None:
    """Render the guidance block above the intake form."""
    st.markdown(
        """
        <div class="acres-instructions">
            <p>Please provide your details for ACRES clearance using the intake
            template below.</p>
            <ul>
                <li>Fields marked <strong>(For Locals only)</strong> or
                <strong>(For Foreigners only)</strong> appear only when
                applicable to the nationality entered.</li>
                <li>The foreigners visit reference is required only for the
                listed countries.</li>
                <li>Use Date of Birth in <strong>YYYY-MM-DD</strong> format
                (e.g. 1990-05-12) and NRIC/FIN like <strong>S1234567A</strong>.</li>
                <li>Fastest way: paste your details into the
                <strong>AI Intake Assistant</strong> and select
                <strong>Auto-fill form with AI</strong>.</li>
                <li>You can also attach a PDF/image clearance document near the
                MSD section and select <strong>Read Documents with AI</strong>.</li>
                <li>Select a project; the location, end date, ops manager and
                clearance level are filled in automatically.</li>
                <li>Describe your work briefly; the AI expands it into a full
                sentence you can edit.</li>
                <li>Review everything, then select
                <strong>Submit to Security</strong>.</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_access_gate() -> bool:
    """Return True when the user may proceed.

    When the ``ACRES_ACCESS_CODE`` environment variable is set, visitors must
    enter that code first. When it is unset, access is open (local use).
    """
    access_code = get_secret("ACRES_ACCESS_CODE").strip()
    if not access_code or st.session_state.get("authed"):
        return True

    st.subheader("Sign In")
    st.caption("Enter the access code shared with you to use the intake form.")
    entered = st.text_input("Access code:", type="password", key="access_code")
    if st.button("Enter", type="primary"):
        if entered.strip() == access_code:
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("Incorrect access code. Please try again.")
    return False


def main() -> None:
    st.set_page_config(
        page_title="ACRES Clearance Automated Intake",
        page_icon="🛡️",
        layout="centered",
    )
    st.markdown(BRAND_CSS, unsafe_allow_html=True)

    if not render_access_gate():
        return

    render_header()
    render_instructions()

    # Apply details extracted from an uploaded document (stashed before rerun,
    # applied here before any fields are created so it can fill the whole form).
    pending = st.session_state.pop("pending_details", None)
    if pending:
        apply_document_details(pending)

    # -- AI Intake Assistant (primary entry point) --------------------------
    st.markdown(
        """
        <div class="acres-ai">
            <h3><span class="acres-badge">AI</span>AI Intake Assistant</h3>
            <p>Paste an email, message, or notes and the local AI will read it
            and fill in the form below. Review and correct anything before
            submitting.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.text_area(
        "Paste anything (email, message, notes):",
        key="ai_text",
        height=130,
        placeholder=(
            "e.g. Please clear Tan Ah Kow, NRIC S1234567A, Singaporean, born "
            "12 May 1990. Religion Buddhist, mobile 91234567. Clearance "
            "Category: Project Clearance, MSD Ref Number M/202510131554246, "
            "MSD Reference Date 30 June 2024, Start Date 01 Feb 2025, End Date "
            "31 Jan 2026."
        ),
    )
    st.button(
        "Auto-fill form with AI",
        type="primary",
        on_click=on_ai_autofill,
    )
    st.caption(f"Model provider: {active_provider()}")
    _ai_message = st.session_state.pop("ai_message", None)
    if _ai_message:
        _kind, _text = _ai_message
        getattr(st, _kind)(_text)

    # Defaults for clearance start/end dates (documents override these).
    st.session_state.setdefault("in_start_date", _dt.date.today().isoformat())
    st.session_state.setdefault("in_end_date", "")

    # -- Applicant details ---------------------------------------------------
    st.subheader("Applicant Details")
    name = st.text_input("NAME:", key="in_name", placeholder="Tan Ah Kow").strip()
    nationality = st.text_input(
        "NATIONALITY:", key="in_nationality", placeholder="Singaporean"
    ).strip()

    local: bool | None
    if not nationality:
        local = None
    else:
        local = is_local(nationality)

    # Conditional identity fields based on local / foreigner status.
    nric = ""
    country_identification_no = ""
    foreigners_visit_reference = ""
    hp_email = ""

    if local is True:
        nric = st.text_input("NRIC:", key="in_nric", placeholder="S1234567A").strip()
        hp_email = st.text_input(
            "HP AND EMAIL ADDRESS (For Locals only to submit G50s):",
            key="in_hp_email",
            placeholder="91234567 / name@example.com",
        ).strip()
    elif local is False:
        country_identification_no = st.text_input(
            "COUNTRY IDENTIFICATION NO (For Foreigners only):",
            key="in_country_id",
        ).strip()
        if needs_foreign_visit_reference(nationality):
            foreigners_visit_reference = st.text_input(
                "FOREIGNERS VISIT REFERENCE (MANDATORY for the listed countries):",
                key="in_foreign_ref",
            ).strip()

    country_of_birth = st.text_input(
        "COUNTRY OF BIRTH:", key="in_country_birth", placeholder="Singapore"
    ).strip()
    date_of_birth = st.text_input(
        "DATE OF BIRTH (YYYY-MM-DD):", key="in_dob", placeholder="1990-05-12"
    ).strip()
    religion = st.text_input("RELIGION:", key="in_religion").strip()
    gender = st.selectbox(
        "GENDER:",
        options=GENDER_OPTIONS,
        index=None,
        placeholder="Select gender...",
        key="in_gender",
    )
    mobile_number = st.text_input(
        "MOBILE NUMBER:", key="in_mobile", placeholder="91234567"
    ).strip()
    start_date_of_employment = st.text_input(
        "START DATE OF EMPLOYMENT (YYYY-MM-DD):",
        key="in_start_employment",
        placeholder="2020-01-15",
    ).strip()

    # -- Project & clearance details ----------------------------------------
    st.subheader("Project & Clearance Details")
    projects = load_projects()
    selected_project = st.selectbox(
        "PROJECT NAME:",
        options=projects["Project Name"].tolist(),
        index=None,
        placeholder="Select a project...",
        key="sel_project",
    )
    project = lookup_project(selected_project) if selected_project else None

    purpose_of_clearance = st.selectbox(
        "PURPOSE OF CLEARANCE:",
        options=PURPOSE_OPTIONS,
        index=None,
        placeholder="Select purpose...",
        key="sel_purpose",
    )
    start_date = st.text_input(
        "START DATE (YYYY-MM-DD):", key="in_start_date", placeholder="2026-01-01"
    ).strip()

    # End date comes from the uploaded document if present, else the project.
    if project is not None and not st.session_state.get("in_end_date"):
        st.session_state["in_end_date"] = project["End Date"]
    end_date = st.text_input(
        "END DATE (YYYY-MM-DD):", key="in_end_date", placeholder="2026-12-31"
    ).strip()

    # Auto-filled from the selected project's register entry.
    st.text_input(
        "LOCATION OF WORK IN DETAIL (auto-filled, eg CNB, Blk 161):",
        value=project["Location of Work"] if project is not None else "",
        disabled=True,
    )
    st.text_input(
        "OPS MANAGER ENDORSEMENT (auto-filled, Rank, Name & Appt):",
        value=project["Ops Manager Endorsement"] if project is not None else "",
        disabled=True,
    )
    st.text_input(
        "CLEARANCE LEVEL (auto-filled; CAT required for this project):",
        value=str(project["Clearance Level"]) if project is not None else "",
        disabled=True,
    )

    company_name = st.text_input(
        "COMPANY NAME (with ADDRESS or UEN):", value=COMPANY_NAME, key="in_company_name"
    ).strip()
    type_of_svc = st.selectbox(
        "TYPE OF SVC:",
        options=TYPE_OF_SVC_OPTIONS,
        index=None,
        placeholder="Select type of service...",
        key="sel_type",
    )
    company_appt = st.text_input(
        "COMPANY APPT (Appointment / Designation):", key="in_company_appt"
    ).strip()

    # -- Attach clearance documents (just above the MSD inputs) -------------
    with st.expander("Attach MSD / clearance document (PDF or image)", expanded=True):
        st.caption(
            "Upload the clearance document (PDF or image). The AI reads it and "
            "fills the MSD details and other fields above and below."
        )
        uploaded_docs = st.file_uploader(
            "Upload PDF or image document(s)",
            type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"],
            accept_multiple_files=True,
            key="doc_uploader",
        )
        if st.button("Read Documents with AI"):
            if not uploaded_docs:
                st.session_state["doc_message"] = (
                    "info",
                    "Upload at least one document first.",
                )
            else:
                text, errors = read_uploaded_documents(uploaded_docs)
                st.session_state["ocr_text"] = text
                st.session_state["ocr_errors"] = errors
                if errors:
                    st.session_state["doc_message"] = (
                        "error",
                        "Could not read: " + "; ".join(errors),
                    )
                elif not text:
                    st.session_state["doc_message"] = (
                        "warning",
                        "No readable text was found in the document(s), even "
                        "after OCR. The file may be blank or very low quality.",
                    )
                else:
                    kind, message, details = _extract_fields(text, "document(s)")
                    st.session_state["doc_message"] = (kind, message)
                    if details:
                        # Re-run so the values are applied to fields that appear
                        # above this uploader (at the top of the script).
                        st.session_state["pending_details"] = details
                        st.rerun()

        # Show what happened, right where the uploader is.
        _doc_message = st.session_state.pop("doc_message", None)
        if _doc_message:
            _kind, _text = _doc_message
            getattr(st, _kind)(_text)

        _ocr_errors = st.session_state.pop("ocr_errors", None)
        if _ocr_errors:
            for _err in _ocr_errors:
                st.error(_err)

        _ocr_text = st.session_state.get("ocr_text")
        if _ocr_text is not None:
            with st.expander("What the AI read (raw OCR text)", expanded=True):
                if _ocr_text:
                    st.code(_ocr_text, language="text")
                else:
                    st.write("(no text was read from the document)")

        _details = st.session_state.get("last_details")
        if _details:
            with st.expander("Fields the AI found", expanded=False):
                st.json(_details)

    st.markdown("**Clearance MSD Details**")
    cat1_msd_no = st.text_input("CAT 1 MSD NO:", key="in_cat1_no").strip()
    cat1_msd_date = st.text_input(
        "CAT 1 MSD DATE (YYYY-MM-DD):", key="in_cat1_date", placeholder="2024-06-30"
    ).strip()
    project_clearance_msd_no = st.text_input(
        "PROJECT CLEARANCE MSD NO:", key="in_proj_msd_no"
    ).strip()
    project_clearance_msd_date = st.text_input(
        "PROJECT CLEARANCE MSD DATE (YYYY-MM-DD):",
        key="in_proj_msd_date",
        placeholder="2024-06-30",
    ).strip()

    # -- Nature of involvement ----------------------------------------------
    st.subheader("Nature of Involvement")
    st.caption(
        "MANDATORY: (1) Brief description of the work for clearance. "
        "(2) No appointment titles. (3) No abbreviations in the description."
    )
    job_notes = st.text_area(
        "Describe your work (brief notes):",
        key="job_notes",
        height=100,
        placeholder=JOB_EXAMPLE,
        help="Type your own brief notes, then select Process Details.",
    )
    if st.button("Process Details", type="primary"):
        if job_notes.strip():
            with st.spinner("Writing your job description..."):
                try:
                    st.session_state["in_nature"] = craft_nature(job_notes)
                    st.success("Draft generated. Review and edit it below.")
                except Exception as exc:  # noqa: BLE001 - surface errors to the user
                    # Fall back to the user's own notes if drafting is unavailable.
                    st.session_state["in_nature"] = job_notes.strip()
                    st.warning(
                        "Automatic drafting is unavailable, so your own notes "
                        f"were used instead. Details: {exc}"
                    )
        else:
            st.info("Add a brief description of your work, then try again.")

    nature_of_involvement = st.text_area(
        "NATURE OF INVOLVEMENT (draft):",
        key="in_nature",
        height=90,
        placeholder="Expanded from your description above; edit if needed.",
        help="Generated from your notes. Edit freely before submitting.",
    ).strip()

    # -- Validation ----------------------------------------------------------
    required = [
        ("NAME", name),
        ("NATIONALITY", nationality),
        ("COUNTRY OF BIRTH", country_of_birth),
        ("DATE OF BIRTH", date_of_birth),
        ("RELIGION", religion),
        ("GENDER", gender or ""),
        ("MOBILE NUMBER", mobile_number),
        ("START DATE OF EMPLOYMENT", start_date_of_employment),
        ("PROJECT NAME", selected_project or ""),
        ("PURPOSE OF CLEARANCE", purpose_of_clearance or ""),
        ("START DATE", start_date),
        ("END DATE", end_date),
        ("TYPE OF SVC", type_of_svc or ""),
        ("COMPANY NAME", company_name),
        ("COMPANY APPT", company_appt),
        ("NATURE OF INVOLVEMENT", nature_of_involvement),
    ]
    if local is True:
        required += [("NRIC", nric), ("HP AND EMAIL ADDRESS", hp_email)]
    elif local is False:
        required.append(("COUNTRY IDENTIFICATION NO", country_identification_no))
        if needs_foreign_visit_reference(nationality):
            required.append(("FOREIGNERS VISIT REFERENCE", foreigners_visit_reference))

    missing = [label for label, value in required if not (value and str(value).strip())]

    invalid = []
    if name and not NAME_PATTERN.match(name):
        invalid.append("NAME")
    if nationality and not NAME_PATTERN.match(nationality):
        invalid.append("NATIONALITY")
    if country_of_birth and not NAME_PATTERN.match(country_of_birth):
        invalid.append("COUNTRY OF BIRTH")
    if date_of_birth and not is_valid_date(date_of_birth):
        invalid.append("DATE OF BIRTH (YYYY-MM-DD)")
    if mobile_number and not MOBILE_PATTERN.match(mobile_number):
        invalid.append("MOBILE NUMBER")
    if start_date and not is_valid_date(start_date):
        invalid.append("START DATE (YYYY-MM-DD)")
    if end_date and not is_valid_date(end_date):
        invalid.append("END DATE (YYYY-MM-DD)")
    if start_date_of_employment and not is_valid_date(start_date_of_employment):
        invalid.append("START DATE OF EMPLOYMENT (YYYY-MM-DD)")
    if cat1_msd_date and not is_valid_date(cat1_msd_date):
        invalid.append("CAT 1 MSD DATE (YYYY-MM-DD)")
    if project_clearance_msd_date and not is_valid_date(project_clearance_msd_date):
        invalid.append("PROJECT CLEARANCE MSD DATE (YYYY-MM-DD)")
    if local is True and nric and not NRIC_PATTERN.match(nric):
        invalid.append("NRIC")
    if local is True and hp_email and "@" not in hp_email:
        invalid.append("HP AND EMAIL ADDRESS")

    if missing or invalid:
        if missing:
            st.warning("Please complete: " + ", ".join(missing))
        if invalid:
            st.error("Please correct the format of: " + ", ".join(invalid))
        return

    # -- Compile & present ---------------------------------------------------
    data = {
        "name": name,
        "nric": nric,
        "purpose_of_clearance": purpose_of_clearance or "",
        "project_name": project["Project Name"],
        "start_date": start_date,
        "end_date": end_date,
        "location_of_work": project["Location of Work"],
        "ops_manager": project["Ops Manager Endorsement"],
        "foreigners_visit_reference": foreigners_visit_reference,
        "company_name": company_name,
        "type_of_svc": type_of_svc or "",
        "nationality": nationality,
        "country_of_birth": country_of_birth,
        "date_of_birth": date_of_birth,
        "religion": religion,
        "gender": gender or "",
        "mobile_number": mobile_number,
        "start_date_of_employment": start_date_of_employment,
        "clearance_level": str(project["Clearance Level"]),
        "cat1_msd_no": cat1_msd_no,
        "cat1_msd_date": cat1_msd_date,
        "project_clearance_msd_no": project_clearance_msd_no,
        "project_clearance_msd_date": project_clearance_msd_date,
        "hp_email": hp_email,
        "company_appt": company_appt,
        "nature_of_involvement": nature_of_involvement
        or project["Nature of Involvement"],
        "country_identification_no": country_identification_no,
        "remarks": "",
    }
    result = build_acres_row(data)

    st.subheader("ACRES Clearance Row")
    st.caption("Review the generated row below before submitting.")
    st.dataframe(result, width="stretch")

    if st.button("Submit to Security", type="primary"):
        filename = f"{safe_filename(name)}_ACRES_ready.csv"
        out_path = Path(__file__).with_name(filename)
        result.to_csv(out_path, index=False)
        st.markdown(
            f'<div class="acres-success">Submitted successfully. Your clearance '
            f"request has been routed to the processing team and saved locally "
            f"as <strong>{filename}</strong>.</div>",
            unsafe_allow_html=True,
        )

    # -- Next steps / recipient ---------------------------------------------
    recipient = (
        project["Ops Manager Endorsement"] if project is not None else MIDDLE_MAN_NAME
    )
    st.markdown(
        f"""
        <div class="acres-footer">
            <h4>What happens next?</h4>
            <p>Send your completed ACRES row to the current clearance
            coordinator (the middle man) and ask for their feedback:</p>
            <ul>
                <li><strong>{recipient}</strong></li>
                <li>Coordinator contact: {MIDDLE_MAN_EMAIL}</li>
            </ul>
            <p>We are still learning the process for each project, so please
            forward it to whoever is coordinating clearance for your project
            and share their feedback with us.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
