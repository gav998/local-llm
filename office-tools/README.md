# Office MCP

Самостоятельная portable-папка для чтения и безопасного изменения Word/Excel.
Основной интерфейс для LLM — Streamable HTTP MCP; CLI сохранён для ручной
диагностики. Системный Python, установка, права администратора и изменение `PATH`
не нужны.

## Запуск MCP

Запустите `start.bat` и оставьте окно открытым. Первый запуск скачает portable
Python, MCP SDK, `python-docx` и `openpyxl` внутрь этой папки. После подготовки
работа возможна офлайн.

- MCP: `http://127.0.0.1:8766/mcp`;
- health: `http://127.0.0.1:8766/health`;
- host, port и path: `server.json`.

Сервер разрешает только loopback host. Папку можно переносить отдельно от
`local_llm`: все runtime, кэши, temp и профиль создаются внутри неё.

## MCP-инструменты

- `inspect_word` — читает абзацы и таблицы DOCX;
- `inspect_excel` — читает листы, диапазон, значения и формулы XLSX/XLSM;
- `replace_word_text` — заменяет текст в DOCX, включая таблицы и колонтитулы;
- `set_excel_cells` — записывает значения или формулы в указанные ячейки;
- `create_word` — создаёт простой DOCX;
- `create_excel` — создаёт простой XLSX из двумерного массива.

Пути могут быть абсолютными. По умолчанию инструменты отказываются перезаписывать
исходник, а записанный файл повторно открывают для проверки. Агент должен вызывать
эти инструменты сам, а не просить пользователя выполнять terminal-команду.

Формулы Excel сохраняются, но `openpyxl` их не вычисляет. Для пересчёта, сложных
диаграмм и ActiveX нужен установленный Excel/LibreOffice. Старые `.doc` и `.xls`
не поддерживаются.

## Подключение к VS Code

Готовый user-level профиль находится в соседней папке `vscode`. Для ручной
настройки добавьте в VS Code user `mcp.json`:

```json
{
  "servers": {
    "office": {
      "type": "http",
      "url": "http://127.0.0.1:8766/mcp"
    }
  }
}
```

## CLI для диагностики

CLI не нужен агенту, но полезен для ручной проверки:

```bat
office.bat inspect "D:\Documents\report.xlsx" --sheet "Sheet1" --range "A1:F30"
office.bat replace-docx "D:\Documents\contract.docx" "D:\Documents\contract.edited.docx" --old "Old text" --new "New text"
office.bat set-xlsx "D:\Documents\report.xlsx" "D:\Documents\report.edited.xlsx" --sheet "Sheet1" --set B2 42
test.bat
```

Для нестандартной локальной диагностики `python.bat script.py` запускает скрипт
в подготовленном окружении этой папки.
