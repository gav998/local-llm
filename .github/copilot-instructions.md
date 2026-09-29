# Local document workspace

Use the terminal and the portable commands in `office-tools` for Word and Excel
files. DOCX/XLSX files are ZIP/XML containers and must not be edited with a text
editor.

- Inspect a document before changing it with
  `.\office-tools\office.bat inspect "<path>"`.
- For simple replacements or cell updates, use `replace-docx` or `set-xlsx`; run
  `.\office-tools\office.bat --help` for the exact syntax.
- For formatting, tables, charts, or other custom work, write a short Python script
  in `workspace` using `python-docx` or `openpyxl`, then run it with
  `.\office-tools\python.bat "workspace\<script>.py"`.
- Preserve the original by default. Save to a clearly named new DOCX/XLSX file
  unless the user explicitly asks to overwrite it.
- Reopen or inspect the produced file after saving it. Report the exact output path.
- `openpyxl` does not calculate formulas. Do not claim that cached formula results
  were recalculated; use installed Excel/LibreOffice automation when that is a
  requirement.
