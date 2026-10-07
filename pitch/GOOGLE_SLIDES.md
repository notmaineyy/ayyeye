# Putting the deck into Google Slides

The deck is generated as a PowerPoint file (`acres_pitch_deck.pptx`), which
Google Slides imports directly. This is the easiest way to edit it and share it
with colleagues for their edits.

## Import (2 minutes)

1. Open **https://drive.google.com** (signed in with your Google account).
2. Click **New → File upload** and choose `pitch/acres_pitch_deck.pptx`.
3. Once uploaded, right-click the file → **Open with → Google Slides**.
   - It opens as a converted Google Slides presentation automatically.
4. To make it a fully native, easy-to-edit copy: in Google Slides go to
   **File → Save as Google Slides**. (Recommended before heavy editing.)

## Edit

- Text, shapes and colours are all editable.
- **Speaker notes** are included — turn them on with
  **View → Show speaker notes**.
- To change the theme colours: **Slide → Edit theme**, or use the paint-format
  tool. The palette is:
  - Navy `#18418A`, Orange `#F26522`, Teal `#009E73`, Light gray `#F4F6F8`.
- Fonts use **Arial** so they render identically in Slides, PowerPoint and on
  any machine (no missing-font surprises).

## Share for colleagues to edit

1. Click **Share** (top-right).
2. Add your colleagues and set their role to **Editor**.
3. Click **Send** (or copy the link and send it).
4. They can edit live; changes appear for everyone. Use
   **File → Version history** if you need to roll back.

## Export back out

- **File → Download → Microsoft PowerPoint (.pptx)** to get an editable file
  back, or **PDF** to send a read-only copy.

## Optional: regenerate from source

If you'd rather edit the content in code, update `pitch/pitch_deck.md` /
`pitch/make_pptx.py` and run:

```bash
.venv/bin/python pitch/make_pptx.py
```

Then re-upload the regenerated `pitch/acres_pitch_deck.pptx` to Drive.
