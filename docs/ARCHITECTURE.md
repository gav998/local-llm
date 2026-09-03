# Архитектура portable local_llm

## Решение

Online-компьютер является сборочной машиной. Он формирует не только кэш установщиков, но и полностью собранные компоненты, выполняет smoke tests и упаковывает каждый компонент в solid-7z. Будущий offline installer не должен разрешать зависимости: только SHA-256, распаковка, генерация конфигурации с текущим путём и повторные target smoke tests.

```text
manual vendor files in _src
          |
          v
safe acquire/extract -> version checks -> online dependency resolution
          |                                  |
          |                                  v
          |                         ready portable runtimes
          |                                  |
          +----------> GPU/service/web smoke tests
                                             |
                                             v
                                  component .7z + SHA-256
```

Обычный wheelhouse недостаточен для RAGFlow: его frozen graph велик, часть Windows/Python 3.13 пакетов может собираться из source или VCS. Готовый `python-rag` runtime — основной offline artifact. Для OCR дополнительно сохраняется binary-only wheelhouse: эта ветка существенно меньше, и её можно доказуемо установить с `--no-index`.

## Почему два Python

RAGFlow 0.27.1 ограничивает Python диапазоном `>=3.13,<3.14`. Официальный Paddle GPU cu118 wheel для Windows, в котором подтверждён Pascal `sm_61`, имеет ABI `cp311`. Попытка объединить их в одну среду либо нарушает RAGFlow, либо теряет нужный GPU wheel. Поэтому:

- `app\runtime\python-rag`: CPython 3.13.15;
- `app\runtime\python-ocr`: CPython 3.11.16 + Paddle cu118;
- связь — HTTP basic serving PaddleX на localhost, который совпадает с актуальным RAGFlow provider suffix `/layout-parsing`;
- старые `ocr_bridge.py` и patch `paddleocr_parser.py` не используются.

Miniconda не выбрана: conda environments записывают prefix и плохо подходят диску, буква которого меняется. `python-build-standalone install_only` разворачивается без installer/registry, зависимости ставятся прямо в этот interpreter, а запуск далее должен идти через `python.exe -m ...`, не через потенциально path-bound launchers из `Scripts`.

## OCR и 8 ГБ VRAM

Основная конфигурация [pp-structure-v3-8gb.yaml](../_src/project/pp-structure-v3-8gb.yaml) создаёт одну pipeline на `gpu:0`:

- `PP-DocLayout-S` — layout;
- `PP-DocBlockLayout` — block/reading order;
- `PP-OCRv6_medium_det` и `PP-OCRv6_medium_rec` — text;
- batch size 1, max side 1200;
- document unwarp/orientation, text-line orientation, seals, tables, formulas и charts выключены.

Это осознанно не называется «полным PP-StructureV3»: для полного профиля 8 ГБ недостаточно. В следующем этапе таблицы и формулы разумно оформить отдельными workers на второй карте и запускать только когда llama.cpp выгружен. Если основной профиль всё же получает OOM на конкретном driver, следующая допустимая ступень — `PP-OCRv6_small_det/rec`, затем `tiny`; CPU fallback запрещён.

## llama.cpp и модели

Текущий llama.cpp распространяет rolling snapshots, а не обычную stable-semver ветку. Зафиксирован конкретный Vulkan snapshot, потому что готовые новые CUDA archives нельзя считать Pascal-compatible без проверки их compile architectures. Vulkan по-прежнему выполняет inference на GTX, не на CPU.

Планируемые `Vikhr-Nemo-12B Q4_K_M` и `Qwen3-Embedding-8B-Q4_K_M` не включены в подготовительный скрипт: для первого имени нужен точный repository/file, а quantized GGUF лучше переносить отдельно. По памяти безопасна не схема «LLM + embedding + OCR всегда включены», а состояния:

1. ingestion: chat server остановлен; OCR обрабатывает страницу, освобождается/остаётся на GPU 0; embedding работает отдельной очередью;
2. chat: OCR workers остановлены; Vikhr-Nemo разбит по GPU 0/1; embedding вызывается последовательно либо частично выгружается;
3. maintenance: сервисы данных без GPU workers.

Точный tensor split определяется не размером GGUF на диске, а фактической VRAM после KV cache/context. Его надо измерить на целевой Windows.

## Сервисы

- MySQL 8.0.40 и Elasticsearch 8.11.3 оставлены на официальных pins RAGFlow, а не обновлены «ради номера».
- Silo используется как S3-compatible object store актуальной поставки RAGFlow.
- Официальный Valkey не поддерживает Windows. Community `valkey-windows` — единственная заведомо спорная часть; её версия зафиксирована и будет проверяться интеграционным тестом очереди. `fakeredis` удалён из концепции, потому что in-memory эмуляция не даёт требуемой надёжности task queue.
- Caddy нужен будущему launcher для статического `web/dist`, SPA fallback и proxy API. Node после online build в runtime не нужен.

## Portable boundary

BAT до первого Python/Node запуска задаёт `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, XDG/Hugging Face/pip/uv/npm/PaddleX/NLTK/tiktoken/CUDA caches внутри `app`. Он не вызывает `setx`, installer, Windows service manager, registry или system PATH.

Это application-level boundary, не виртуальная машина. NVIDIA driver остаётся системным. Windows shell и некоторые native libraries могут читать Known Folders; финальная приёмка требует Process Monitor trace на чистой учётной записи.

## Следующие файлы

1. `2.INSTALL-OFFLINE.bat`: проверка `_src\prepared\SHA256SUMS.txt`, безопасная распаковка в temp, atomic promotion в `app`, генерация path-sensitive конфигураций и target smoke.
2. `3.START.bat`: lock, проверка портов, schema migrations, режимы ingestion/chat, корректное завершение БД.
3. `4.STOP.bat` и `5.STATUS.bat`: PID files и health endpoints вместо убийства окон по title.
4. Интеграционные тесты: MySQL schema init/migration, Valkey queue semantics, S3 upload, Elasticsearch index, PaddleX `/layout-parsing`, llama `/v1/embeddings` и RAGFlow end-to-end document ingestion.

