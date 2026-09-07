# local_llm

Первый этап новой portable-сборки для Windows: [1.PREPARE-ONLINE.bat](./1.PREPARE-ONLINE.bat) собирает на компьютере с интернетом полностью готовые runtime-компоненты RAGFlow, PaddleOCR и локальных сервисов, проверяет их и упаковывает в девять solid-архивов. Docker, права администратора, installer, Windows Service, запись в registry и изменение системного `PATH` не используются.

Это пока только этап **online preparation**. Offline installer и launcher будут отдельными BAT-файлами; данный скрипт их функции в себя не смешивает.

## Назначение и исходные ограничения

Это проект сборки **RAGFlow + локальный llama.cpp + локальный PaddleOCR / PP-StructureV3** для полностью офлайн-работы с нейросетями, агентами и локальной файловой системой. Основная нагрузка — Word- и PDF-документы, включая OCR русскоязычных сканов. Для русского chat/LLM задан `Vikhr-Nemo-12B Q4_K_M`; рабочее решение для embeddings — `Qwen3-Embedding-8B-Q4_K_M.gguf` через отдельный локальный llama.cpp server.

Целевая машина:

- Windows 11 x64;
- Intel Core i3-8350K, 32 ГБ RAM;
- две NVIDIA GTX 1080 по 8 ГБ (Pascal, compute capability `sm_61`);
- почти заполненный системный SSD 250 ГБ;
- внешний HDD для всего проекта, моделей, пользовательских данных, временных файлов и кэшей;
- нет прав администратора;
- 40–50 пользователей могут работать с общей переносимой установкой **последовательно**, но не одновременно.

Системный `%APPDATA%` и `%LOCALAPPDATA%` не должны использоваться даже для кэша загрузки моделей. BAT перенаправляет profile/cache/temp-каталоги внутрь `app` до запуска дочерних инструментов, ничего не записывает через `setx` и добавляет portable-инструменты только во временный `PATH` текущего процесса. GPU используется там, где это быстрее CPU; CPU остаётся для баз данных, распаковки, PDF-растеризации, post-processing и другой ненейросетевой работы.

## Главные решения

| Компонент | Зафиксировано | Зачем именно так |
|---|---|---|
| RAGFlow | `0.27.1` | актуальный стабильный релиз от 28.08.2026; требует Python `>=3.13,<3.14` |
| RAGFlow Python | CPython `3.13.15` standalone | переносимый `install_only` runtime без registry и привязки conda-prefix |
| OCR Python | CPython `3.11.16` standalone | отдельный ABI для Windows GPU-wheel Paddle |
| PaddlePaddle | GPU `3.3.1`, CUDA 11.8, `cp311` | wheel содержит `sm_61`; это необходимый код для GTX 1080 |
| PaddleOCR / PaddleX | `3.7.0` / `3.7.2` | актуальная стабильная ветка с PP-OCRv6 и PP-StructureV3 |
| OCR-модели | `PP-DocLayout-L` + `PP-DocBlockLayout` + `PP-OCRv6_medium_det` + `eslav_PP-OCRv5_mobile_rec` + `SLANet_plus` | качественный layout/кириллический OCR и обязательное восстановление структуры таблиц |
| Node | `24.20.0 LTS` | только online production build frontend; в offline runtime Node не попадает |
| Portable Git | MinGit `2.55.0.5` | распаковывается в `app\build\git` и попадает только в `PATH` процесса BAT; системная установка не нужна |
| llama.cpp | `b10786`, официальный Windows Vulkan build | GPU-инференс на Pascal без зависимости от готовых CUDA 12/13 builds |
| Сервисы | MySQL `8.0.40`, Elasticsearch `8.11.3`, Silo `2026-08-06`, Valkey `8.1.6`, Caddy `2.11.4` | совместимые переносимые Windows binaries; Node/Java отдельно ставить не требуется |

RAGFlow и Paddle нельзя разумно поместить в один Python: текущий RAGFlow требует 3.13, а выбранный и проверяемый Windows GPU-wheel Paddle имеет ABI 3.11. Miniconda здесь не даёт преимущества и добавляет риск абсолютных prefix-путей при смене буквы внешнего диска.

Основные официальные источники: [RAGFlow v0.27.1](https://github.com/infiniflow/ragflow/releases/tag/v0.27.1), [RAGFlow pyproject](https://github.com/infiniflow/ragflow/blob/v0.27.1/pyproject.toml), [PaddleOCR v3.7.0](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0), [PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-StructureV3/PP-StructureV3.html), [python-build-standalone 20260901](https://github.com/astral-sh/python-build-standalone/releases/tag/20260901), [MinGit 2.55.0.5](https://github.com/git-for-windows/git/releases/tag/v2.55.0.windows.5) и [llama.cpp b10786](https://github.com/ggml-org/llama.cpp/releases/tag/b10786).

## Структура

```text
local_llm\
  1.PREPARE-ONLINE.bat
  _src\
    <23 vendor artifacts>    файлы, которые пользователь кладёт сюда вручную
    project\                 versioned requirements/config/helpers
    logs\                    подробные журналы online-сборки
    prepared\                атомарно опубликованный offline-набор
  _work\                     только временные staging-каталоги
  app\
    build\                   uv, Node, MinGit и временные online-материалы
    cache\paddlex\...        пять явно подготовленных OCR/table-моделей
    config\                  locks, freeze и provenance/smoke records
    data\                    локальные profile/cache/data roots
    models\llm               будущие chat GGUF
    models\embed             будущие embedding GGUF
    ragflow                  backend и проверенные нейтральные assets
    runtime                  два Python runtime, VC DLL и llama.cpp
    services                 MySQL, Elasticsearch+JDK, Silo, Valkey, Caddy, OCR gateway
    web                      готовый production frontend
```

Скрипт определяет корень только через `%~dp0`: буква диска и имя папки нигде не зашиты. Все известные `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, temp и cache-переменные перенаправляются внутрь `app` до первого запуска PowerShell/Python/Node.

## Запуск

1. Поместите проект в короткий локальный путь на внешнем диске, например `X:\local_llm`. Файловая система должна поддерживать файлы больше 4 ГБ; NTFS или exFAT подходят. Не используйте пробелы, `#` или CMD-метасимволы `! & ( ) % ; ^ < > |`.
2. NVIDIA driver и Vulkan runtime должны уже работать в Windows. Проект их не устанавливает и не может сделать это без администратора.
3. Запустите:

   ```bat
   1.PREPARE-ONLINE.bat artifacts-only
   ```

   Для каждого отсутствующего vendor-файла BAT печатает на английском имя, фиксированный URL и точный путь `_src\...`, затем ждёт `pause` и проверяет снова. Сам BAT эти 23 файла не скачивает. Полный перечень и SHA-256 находится в [_src/project/artifacts.sha256](./_src/project/artifacts.sha256).

4. После подготовки всех vendor-файлов запустите обычную online-сборку:

   ```bat
   1.PREPARE-ONLINE.bat
   ```

   Здесь интернет уже используется для транзитивных PyPI/npm-зависимостей и строго зафиксированных RAGFlow assets. Результат устанавливается в переносимые runtime-каталоги, а не оставляется россыпью wheelhouse-файлов.

5. На целевом Windows-компьютере с GTX 1080 обязательно выполните:

   ```bat
   1.PREPARE-ONLINE.bat gpu-test
   ```

   Режим проверяет CUDA 11.8, наличие `sm_61` в wheel, выбранную карту, реальную CUDA-матрицу, загрузку пяти моделей, OCR изображения и PDF, распознавание табличной структуры и получение table-блока через **штатный** parser RAGFlow. CPU fallback отсутствует. В `build-info.txt` успешный результат записывается как `target_gpu_e2e=passed`.

Обычный запуск полезен для сборки на любой Windows x64 машине, но не делает непроверенную GTX 1080 «подтверждённо совместимой». Будущий offline installer также должен отказать в `install.ok`, пока target GPU E2E не пройдёт.

## Четырёхаргументная функция

Требуемый общий механизм находится под меткой `:EnsureArtifact`:

```bat
call :EnsureArtifact "file-name" "destination-under-app" "key-file" "fixed-url"
```

Он принимает ровно четыре аргумента и выполняет нужный цикл: ждёт файл непосредственно в `_src`, сверяет fail-closed SHA-256, создаёт destination, проверяет key, распаковывает или копирует через staging, повторно проверяет key и при автоматической ошибке ждёт ручной обработки.

Есть одно намеренное усиление исходного требования: одного старого key-файла недостаточно для пропуска. Рядом должен быть marker с именем и SHA-256 именно текущего source artifact; для обычного файла дополнительно выполняется бинарное сравнение. Поэтому случайно оставшийся или подменённый каталог не считается готовым.

## Offline-набор

После полного успеха `_src\prepared` содержит:

```text
7zr.exe
00-bootstrap-tools.7z
10-python-rag-runtime.7z
20-python-ocr-gpu-runtime.7z
21-paddle-models.7z
30-ragflow-backend.7z
31-ragflow-web-dist.7z
40-services.7z
41-config-seed.7z
50-llama-vulkan-runtime.7z
PORTABILITY-AUDIT.json
SHA256SUMS.txt
SOURCE-SHA256SUMS.txt
build-info.txt
prepared.ok
```

Перед публикацией все девять архивов тестируются, распаковываются вместе в новый временный каталог и повторно проходят runtime/import/OCR-contract/RAGFlow/portability проверки в окружении, которое не видит исходное `app`. Старый корректный `_src\prepared` заменяется только атомарным `move`; при ошибке он сохраняется.

GGUF в архивы не включаются: уже квантованные файлы почти не сжимаются и только зря замедлят HDD. `Vikhr-Nemo-12B Q4_K_M` и `Qwen3-Embedding-8B-Q4_K_M.gguf` нужно переносить отдельно вместе с их SHA-256.

## Ограничения, которые важно принять заранее

- Native Windows без Docker не является штатным deployment target RAGFlow. Скрипт фиксирует точные совместимые отклонения: NumPy `2.3.5`, XGBoost `2.1.4`, собственный проверенный MSVC wheel `datrie 0.8.3`, app-local VC runtime. Полный fork `graspologic` по-прежнему исключён из-за требования `numpy<2`, но GraphRAG включён через закреплённый `graspologic-native 1.2.5` и audited-адаптер: hierarchical Leiden выполняет тот же Rust backend, а largest connected component — NetworkX. Сборка проверяет импорт полного GraphRAG entrypoint и реальное разбиение тестового графа.
- Upstream `ragflow_deps\download_deps.py` предназначен для Linux/Docker-слоя и среди прочего загружает Ubuntu `.deb`. BAT не запускает его на Windows. Нужные платформенно-нейтральные RAGFlow assets загружаются отдельным проектным helper-скриптом с закреплёнными revision, размером и SHA-256; это не означает пропуск нужных runtime-зависимостей.
- `PP-StructureV3` всегда запускается с `use_table_recognition: true`: layout сначала выделяет table-регионы, затем `SLANet_plus` с batch 1 восстанавливает ячейки/строки/столбцы, переиспользуя общий кириллический OCR. Отключить таблицы через API нельзя. Тяжёлый `table_recognition_v2` с пятью дополнительными моделями, а также formulas, charts, seals и preprocessing выключены ради одной карты 8 ГБ. Реальный `gpu-test` остаётся обязательным memory/compatibility gate.
- Две GTX 1080 по 8 ГБ не образуют общую 16-ГБ память. Для ваших моделей нужен режимный scheduler: ingestion (`OCR → embedding`) и chat не должны одновременно пытаться занять обе карты. Точный Vulkan tensor split и context/KV cache определяются замером на целевом ПК.
- Строго GPU относится к нейросетевому inference. Растеризация PDF, декодирование, post-processing, JSON, chunking и работа базы всё равно выполняются CPU.
- Текущий Paddle wheel действительно содержит `sm_61`, но актуальная общая документация Paddle формально ориентируется на более новые GPU. Поэтому реальный `gpu-test` на GTX 1080 — обязательный compatibility gate, а не обещание на основании имени wheel.
- Переносимое хранилище рассчитано на последовательную работу разных пользователей. Одновременный запуск общей MySQL/Elasticsearch базы несколькими аккаунтами и отключение HDD во время работы опасны для данных. ACL диска должны давать запись всем нужным пользователям.
- Переменные известных библиотек изолированы, а payload сканируется на абсолютный build-path и reparse points. Но только Process Monitor на чистой Windows-учётной записи может доказать, что неизвестная native DLL ни разу не обратилась к Known Folders.

Подробности реализации и границы следующих этапов: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).
