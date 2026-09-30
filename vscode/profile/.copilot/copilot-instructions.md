# Portable local document tools

- For plain text, Markdown, JSON, CSV, and source code, use the agent's normal file
  read/search/edit tools. Do not ask the user to run a terminal command just to read
  one of these files.
- For `.docx`, `.xlsx`, and `.xlsm`, call the `office` MCP tools directly. Inspect a
  file before editing it and inspect the result after editing it.
- Preserve the original Office file by default. Write a clearly named output file
  unless the user explicitly asks to overwrite the source.
- `openpyxl` preserves and writes formulas but does not calculate them. Do not claim
  that formula results were recalculated.
- Report the exact output path after creating or changing a file.
