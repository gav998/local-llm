# Structured PDF digitizer

Independent loopback web tool for entity-oriented markup of scanned PDF files.
It never reads the PDF text layer: every marked field or table cell is rendered
to PNG by PyMuPDF and sent to the separate PaddleOCR API. Optional text cleanup
uses the separate OpenAI-compatible llama.cpp chat API.

Open `http://127.0.0.1:9400/?source=C:\path\scan.pdf`, or pass `path` / `blob`
instead of `source`. Local files keep `*.pdf.digitizer.json` and
`digitizer.templates.json` beside the PDF. HTTP(S) blob sources are copied into
the portable `data/blob-cache` first, and their sidecars remain next to that
local cached copy.

The UI supports scalar fields, table headers, multi-page row regions, explicit
draggable row/column cuts, common values for following rows, per-region prompts,
background OCR, operator modifications, reusable templates and JSON export.

Direct commands:

```bat
MODULE.bat install
MODULE.bat start
MODULE.bat stop
MODULE.bat status
MODULE.bat verify
```

`start` expects PaddleOCR to be running at its connection-contract URL. llama.cpp
is optional until a region has “обработать через llama.cpp” enabled.
