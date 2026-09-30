# GOST GSM MCP

Детерминированный MCP-помощник для сериализации больших OCR-документов по ГСМ. Он читает HTML/Markdown, последовательно обрабатывает логические строки и ячейки, хранит provenance и экспортирует три CSV:

1. `01_gsm_brands.csv` — марка, НД, назначение и применимость для техники;
2. `02_gsm_group_brand.csv` — группа, подгруппа, марка;
3. `03_gsm_relations.csv` — основная марка, тип связи, связанная марка, примечание.

Модель управляет процессом и рассматривает неоднозначности. Подсчёт, парсинг, progress, staging, QC и экспорт выполняет Python, поэтому повторный вызов batch не дублирует строки.

## Быстрый запуск в VS Code

1. Папку `gost-gsm-mcp` можно перенести отдельно. Запустите в ней `start.bat` и
   оставьте окно открытым. При первом старте portable Python и зависимости
   установятся внутрь этой же папки.
2. Скопируйте папку `vscode` из комплекта рядом с `Code.exe` и запускайте
   `vscode\start.bat` либо вручную добавьте HTTP MCP
   `http://127.0.0.1:8765/mcp` в user-level конфигурацию VS Code.
3. Выполните `MCP: List Servers` и убедитесь, что `gostGsm` доступен.
4. Выберите custom agent `GSM GOST Orchestrator` и передайте путь к OCR-файлу:

```text
Просканируй D:\OCR\gost_gsm.html, покажи найденные таблицы и, если выбор однозначен, инициализируй обработку. Затем обработай первые 10 строк.
```

При нормальном запуске терминал показывает
`Starting Streamable HTTP at http://127.0.0.1:8765/mcp`. Быстрая проверка:
откройте `http://127.0.0.1:8765/health` или выполните
`curl.exe http://127.0.0.1:8765/health`. Порт и путь настраиваются в
`server.json`.

Job по умолчанию хранится в `gost-gsm-mcp\jobs\default`. Исходник может лежать
в любом доступном текущему Windows-пользователю каталоге. Для независимых задач
передавайте MCP-инструментам отдельный абсолютный `project_root`.

## Обычный рабочий процесс

Первый прогон:

```text
Обработай D:\OCR\gost_gsm.html пакетами по 10 строк. Продолжай блоками по 25 batch, финальный CSV пока не экспортируй.
```

Проверка проблем:

```text
Покажи блокирующие review flags и разбери строку R000245 через ревизора.
```

Финал:

```text
Выполни финальную валидацию. Если blockers отсутствуют, экспортируй три CSV.
```

Дирижёр не включает `allow_partial` и `allow_blockers` без явного разрешения пользователя.

## Поддерживаемый источник

- `.html` / `.htm`, включая `rowspan`, `colspan`, `<br>` и таблицы, повторённые по страницам;
- Markdown pipe tables;
- Markdown со встроенными HTML-таблицами;
- один файл или каталог. Для пары с одинаковым именем `document.html` + `document.md` выбирается HTML.

Главная таблица должна иметь четыре семантических столбца: основная, дублирующая, резервная и зарубежная марка ГСМ. Таблица назначения должна иметь марку и текст назначения/условий применения. Если автоопределение дало несколько кандидатов, используйте `scan_source`, затем передайте выбранные `brand_table_ids` и `purpose_table_ids` в `initialize_project`.

PDF напрямую не разбирается: сначала получите OCR HTML/Markdown через `paddleocr` или другой инструмент.

## Точное поведение

- Ячейки идут в порядке `main`, `duplicate`, `reserve`, `foreign`.
- Позиции делятся по строкам, `<br>`, спискам и безопасным точкам с запятой. Запятая не является разделителем.
- Предпочтительный формат позиции: `<марка> по <нормативный документ>`.
- Без НД позиция сохраняется, а `normdoc_not_found` попадает в review flags.
- OCR-подмены символов не исправляются автоматически.
- Одна основная марка связывается со всеми связанными марками строки. При равном числе позиций применяется порядок. При несовпадении количества создаётся `ambiguous_relation_mapping`.
- Назначение сначала ищется по марке + НД, затем по марке в контексте группы/подгруппы. Разные тексты назначения блокируют автоматический выбор.
- Table 1 на экспорте объединяется по внутреннему ключу `марка || НД`; признаки техники агрегируются как область применимости (`+` имеет приоритет, затем `-`, затем пусто).
- Table 2 и Table 3 сохраняют порядок первого появления и удаляют только полностью одинаковые внешние строки.

## Состояние job

```text
gost-gsm-mcp/jobs/default/
  .gost-gsm-project.json
  config.json                       # необязательно
  work/
    manifest/
      source_rows.jsonl
      source_cells.jsonl
      purpose_rows.jsonl
      tables_catalog.json
    batches/
      B000001.parsed.json
      B000001.relations.json
      B000001.purpose.json
      B000001.overrides.json         # появляется после ручной правки
      B000001.flags.json
    staging/
      table1_occurrences.jsonl
      table2_occurrences.jsonl
      table3_occurrences.jsonl
    progress.json
    review_flags.jsonl
    qc_report.md
  out/
    01_gsm_brands.csv
    02_gsm_group_brand.csv
    03_gsm_relations.csv
```

JSONL staging нельзя править вручную. Для ручных решений используются `set_item_override`, `set_purpose_override`, `add_relation_override` и `clear_override`; после них helper сам повторяет зависимые шаги, staging и QC. `set_item_override` меняет эффективную марку/НД, но сохраняет raw OCR и исходный parsed item для аудита.

## MCP tools

- `scan_source` — каталог и preview физических таблиц без записи job;
- `initialize_project` — manifest и начальный progress;
- `get_progress`, `get_next_batch` — состояние и preview следующего диапазона;
- `process_next_batch`, `process_batches` — полный детерминированный проход;
- `list_review_flags`, `get_row_detail`, `find_purpose_candidates` — ревизия;
- `set_item_override`, `set_purpose_override`, `add_relation_override`, `clear_override` — трассируемые правки;
- `validate_final`, `export_final_csv` — финальный контроль и экспорт.

`process_batches` ограничен 1000 batch за вызов, один batch — конфигурационным максимумом 20 строк. Для живого контроля дирижёр использует блоки по 25 batch.

## Настройка заголовков и НД

Скопируйте `config.example.json` как `jobs\default\config.json` либо в каталог
другого job и добавьте варианты заголовков или префиксы нормативных документов.
Объекты объединяются с defaults, массив конкретного поля заменяет default-массив.

После изменения config выполните повторную инициализацию с `force=true`. Она удаляет только сгенерированные manifest/batch/staging и три итоговых CSV текущего job; исходный OCR и `config.json` остаются.

## Локальная проверка

На Windows:

```bat
gost-gsm-mcp\test.bat
```

Проверка MCP runtime:

```bat
gost-gsm-mcp\python.bat gost-gsm-mcp\server.py --self-check
```

Для разработки с уже установленным Python 3.11+:

```bash
python -m venv .venv
.venv/bin/pip install -r gost-gsm-mcp/requirements.txt
PYTHONPATH=gost-gsm-mcp .venv/bin/python -m unittest discover -s gost-gsm-mcp/tests -v
```

## Безопасность и переносимость

- MCP использует Streamable HTTP и принимает соединения только через loopback
  (`127.0.0.1`, `localhost` или `::1`). Конфигурация с внешним host отклоняется.
- При первом старте сеть нужна только для portable Python, uv и wheels. После подготовки runtime сервер работает локально.
- Профиль, temp, pip/uv cache и Python находятся внутри `gost-gsm-mcp`.
- Сервер пишет только в выбранный `project_root` и не принимает корень диска или корень профиля пользователя как job.
- HTTP-сервер не запускается VS Code автоматически: его окно и жизненный цикл
  независимы от редактора.

Формат HTTP MCP и user-level custom agents соответствует официальной
документации VS Code: [MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
и [Custom agents](https://code.visualstudio.com/docs/agent-customization/custom-agents).
Сервер использует официальный Python SDK MCP 2.x:
[modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk).
