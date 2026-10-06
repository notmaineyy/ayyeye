# ACRES Clearance Automated Intake — Pitch Deck

> One-line pitch: **A self-serve portal that turns a messy clearance request
> into a ready-to-submit ACRES row and routes it straight to the project
> executive — no middleman.**

---

## 1. Title
**ACRES Clearance Automated Intake**
Clearing the path to clearance.
- Self-serve. AI-assisted. Direct to the project executive.
- Working prototype.

Speaker note: "Today, getting ACRES clearance is a manual, multi-step chase. We
built a tool that collapses it into one self-serve flow."

---

## 2. The Problem
Applying for security clearance is slow, fragmented and error-prone.
- A complex, 20+ column Excel sheet must be filled perfectly.
- Project details (ops manager, department, clearance level) are scattered.
- Applicants decode clearance categories and MSD reference numbers manually.
- Requests bounce through a middleman before reaching the project executive.
- Incomplete/wrong rows cause rework and delay clearances.
- Sensitive data is handled ad hoc over email.

Speaker note: "Every hop and every typo adds days."

---

## 3. The Cost
- **Days of waiting** per clearance, multiplied across a project.
- **Rework loops** between applicant, middleman and project executive.
- **Risk**: sensitive details scattered across email and spreadsheets.
- **No standard**: each request is formatted differently.

---

## 4. The Solution
A self-serve intake portal that does the work for you.
- Paste an email, or upload a PDF/image clearance document.
- AI extracts and validates every required field.
- Project details auto-fill from a project register.
- Produces a standardized, ready-to-submit ACRES row.
- Packages it and routes it **directly to the project executive**.

Speaker note: "The middleman disappears — the applicant goes straight to the
person who submits for clearance."

---

## 5. How It Works
1. **Input** — paste text or upload a scanned PDF/image.
2. **Read** — OCR (Tesseract) turns documents into text.
3. **Understand** — a language model extracts structured fields, constrained to
   a strict schema.
4. **Verify** — deterministic parsing for critical codes (MSD `M/…`, categories
   like `1A`, `01`, `2B`) and format checks for NRIC, dates, mobile.
5. **Assemble** — merge with the project register into one ACRES row.
6. **Route** — package and send to the project executive.

---

## 6. Where AI Is Used (and where it isn't)
AI does the messy, language-heavy parts — not the critical codes.
- **AI**: reading documents, extracting fields, drafting the "Nature of
  Involvement" sentence.
- **Not AI (deterministic)**: MSD reference numbers, clearance categories,
  dates, project lookup, validation, the final row.
- **Human-in-the-loop**: anything missing or mis-formatted is flagged for the
  user to fix.

Speaker note: "We deliberately don't trust the model with reference numbers —
those are parsed deterministically, so we get AI's flexibility without its
hallucinations."

---

## 7. Key Features
- **AI Intake Assistant** — paste anything, form fills itself.
- **Document OCR** — scanned PDFs and photos of forms.
- **Smart conditionals** — NRIC & mobile for locals; country ID & visit
  reference for foreigners; auto rules for listed countries.
- **Format validation** — NRIC, dates, mobile, emails.
- **Project auto-fill** — location, end date, ops manager, clearance level,
  department, project executive.
- **Direct routing** — recipient pre-filled; one-click email package.
- **Flexible AI backend** — local (private) or cloud; free tiers supported.

---

## 8. What Makes It Different
- **Removes the middleman**, not just digitises the form.
- **Hybrid intelligence**: LLM for language, deterministic code for critical
  values.
- **Privacy options**: can run fully local — clearance data never leaves the
  machine.
- **Self-serve**: applicants complete it themselves, correctly, first time.

---

## 9. Who It's For
- **Applicants** who need clearance for a project.
- **Project executives** who submit for clearance.
- **Ops managers** who endorse.
- **Project / security admin teams** who manage the pipeline.

---

## 10. Impact
- **Faster**: minutes instead of days of back-and-forth.
- **More accurate**: validated, standardized rows; fewer rework loops.
- **Less admin**: middleman step eliminated.
- **Safer**: privacy-preserving local AI option; consistent handling.
- **Scalable**: same flow for every project and person type.

---

## 11. Status
- **Working prototype**, deployed and iterated with real feedback.
- Reads real scanned clearance documents (OCR + AI).
- Handles the full ACRES field set and per-project routing.
- AI backend options already integrated (local Ollama; Gemini free tier;
  OpenCode Go; any OpenAI-compatible provider).

---

## 12. Roadmap
- Real email sending (SMTP/API) + delivery receipts.
- Live project register integration + SSO/role-based access.
- Audit trail & submission tracking.
- Multiple clearance rows per applicant; category-aware routing.
- Analytics: time-to-clearance, error rates, bottlenecks.

---

## 13. Risks & Mitigations
- **Sensitive data** → local model option; no third-party calls when configured
  locally; access codes; minimal retention.
- **Model accuracy** → schema constraints + deterministic parsing + human review.
- **Adoption** → works alongside the existing Excel template; low training.
- **Process variation per project** → routing is configurable per project.

---

## 14. The Ask
- **Pilot** with one project team.
- Gather feedback from applicants, ops managers and project executives.
- Measure time-to-clearance and rework before/after.
- Expand to more projects.

**Let's clear the middleman.**

---

## Appendix A — Demo Flow
1. Paste an email or upload a scanned clearance PDF.
2. Watch the form fill itself; correct anything flagged.
3. Select the project → ops manager, department, executive auto-fill.
4. Review the generated ACRES row.
5. Package & send to the project executive.

## Appendix B — Tech Snapshot
- Streamlit web app; Python.
- OCR: Tesseract + PyMuPDF.
- LLM: Ollama (local) or Gemini / OpenCode Go / OpenAI-compatible (cloud),
  schema-constrained extraction with Pydantic.
- Data: project register (CSV) + generated ACRES row (CSV).
