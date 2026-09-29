# Office tools

Portable terminal helpers for a VS Code agent. On first use, the launchers download
their own Python runtime and install `python-docx` and `openpyxl` inside this folder.
Nothing is installed system-wide.

Typical commands from the repository root:

```bat
.\office-tools\office.bat inspect "workspace\contract.docx"
.\office-tools\office.bat inspect "workspace\report.xlsx" --sheet "Sheet1" --range "A1:F30"
.\office-tools\office.bat replace-docx "workspace\contract.docx" "workspace\contract.edited.docx" --old "Old text" --new "New text"
.\office-tools\office.bat set-xlsx "workspace\report.xlsx" "workspace\report.edited.xlsx" --sheet "Sheet1" --set B2 42 --set C2 "=B2*2"
.\office-tools\office.bat inspect "D:\Documents\report.xlsx" --sheet "Sheet1" --range "A1:F30"
```

The `workspace` directory is optional. In portable VS Code, use **File → Add Folder
to Workspace...** to expose an existing document directory to the Local Agent.
The commands also accept absolute paths anywhere the current Windows user can
access.

For a change that the small CLI does not cover, create a Python script in
`workspace` and run it with the prepared environment:

```bat
.\office-tools\python.bat "workspace\edit_document.py"
```

The CLI supports `.docx`, `.xlsx`, and `.xlsm`. It deliberately refuses to replace
an input file unless `--overwrite` is explicit. It does not support legacy `.doc`
or `.xls` files. `openpyxl` writes formulas but does not calculate them; use desktop
Excel or LibreOffice when a recalculation or complex Office automation is required.
