# Архитектура portable local_llm

## Граница этапа 1

`1.PREPARE-ONLINE.bat` превращает Windows-компьютер с интернетом в одноразовую сборочную машину. Он не запускает постоянный stack и не создаёт конфигурацию конкретной offline-машины. Его единственный конечный продукт — проверенное дерево `app` и компактный `_src\prepared`, из которого следующий этап сможет установить stack без сети, package manager и компилятора.

```text
23 вручную полученных vendor artifacts в _src
                    |
                    v
     SHA-256 -> staging extract -> key/version checks
                    |
                    v
    online resolve/build (uv, PyPI, npm, pinned assets)
                    |
                    v
  готовые Python runtimes + web dist + services + OCR models
                    |
                    v
       smoke/audit -> 9 solid 7z -> isolated rehydrate
                    |
                    v
             atomic _src\prepared
```

Wheelhouse как основной способ переноса сознательно отвергнут. Для огромного RAGFlow-графа это оставило бы разрешение платформенных проблем offline installer-у. Здесь разрешение, установка, native import и model smoke происходят online; на offline-компьютер переезжают уже готовые файлы. Временный OCR wheelhouse также удаляется после установки.

Проверенный `7zr.exe` лежит в `_src\prepared` отдельно: он раскрывает первый `00-bootstrap-tools.7z`, после чего остальные archives обрабатывает полный `7za.exe` из этого bundle. Это устраняет bootstrap-зависимость «extractor находится внутри архива, который ещё нечем открыть».

## Portable boundary

Корень всегда вычисляется относительно BAT. До первого PowerShell-вызова создаются и назначаются локальные `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, PowerShell/.NET roots. До Python/Node дополнительно назначаются XDG, Hugging Face, pip, uv, npm, Paddle, NLTK, tiktoken, CUDA и bytecode cache roots. Внешние `PYTHONPATH`, virtualenv/conda, Node, pip/uv/npm и Java option-переменные очищаются.

Принципиально отсутствуют:

- `setx`, registry writes и изменение системного `PATH`;
- MSI/EXE installation и Windows Service registration;
- Docker/WSL;
- запись runtime в профиль текущего пользователя;
- path-bound Python launchers из `Scripts`;
- `node_modules` и online build tools в offline bundles.

Microsoft VC Redistributable не запускается: из проверенного официального EXE извлекаются разрешённые app-local DLL, включая `vcomp140.dll`, нужный XGBoost. Python берётся из [python-build-standalone](https://github.com/astral-sh/python-build-standalone), который публикует redistributable standalone distributions.

Граница остаётся application-level sandbox, а не виртуальной машиной. Системный NVIDIA driver, Vulkan loader, Windows DLL и Known Folders существуют снаружи. Поэтому финальная приёмка включает Process Monitor trace под новой учётной записью.

## Почему два Python

[RAGFlow 0.27.1](https://github.com/infiniflow/ragflow/releases/tag/v0.27.1) задаёт `requires-python = ">=3.13,<3.14"`, поэтому backend получает CPython 3.13.15. Windows GPU-wheel Paddle 3.3.1/cu118 выбран в ABI `cp311`, поэтому OCR получает CPython 3.11.16. Оба — `install_only` standalone archives одной датированной поставки `20260901`.

Conda/Miniconda не используется: environment relocation и смена буквы диска требуют prefix-rewriting, которое здесь не даёт пользы. Два обычных автономных interpreter-а связаны только loopback HTTP.

## RAGFlow на native Windows

Upstream lock сначала экспортируется через `uv export --frozen`. Затем для реальной Windows CPython 3.13 собирается hash-locked граф с минимальными явными отклонениями:

- `numpy==2.3.5`: upstream `1.26.4` не имеет Windows wheel для CPython 3.13;
- `xgboost==2.1.4`: upstream `1.6.0` несовместим с NumPy 2;
- `datrie==0.8.3`: проектный MSVC/cp313 wheel построен из pinned sdist отдельным GitHub Actions workflow, функционально проверен и опубликован с SHA-256;
- `infinity-sdk==0.7.3`: только его metadata constraint `numpy<2` меняется на проверенный `numpy>=2,<2.4`, а RECORD пересчитывается;
- полный `graspologic` исключён, но `graspologic-native==1.2.5` добавлен напрямую. Audited-адаптер преобразует NetworkX-рёбра в native tuples для hierarchical Leiden и выбирает largest connected component средствами NetworkX; GraphRAG включён;
- `infinity-emb` исключён: embeddings обслуживает отдельный llama.cpp server.

Патчер применим только к точным исходным SHA-256 RAGFlow 0.27.1, Leiden-модуля, audited-адаптера и точным metadata bytes. Неизвестная версия аварийно останавливает сборку. Verifier импортирует task executor с lazy GraphRAG, затем полный GraphRAG entrypoint, выполняет hierarchical Leiden реальным Rust backend, запускает русский tokenizer, materializes `cl100k` без сети и выполняет prediction pinned XGBoost model.

MinGit 2.55.0.5 всё равно входит в online build tools и добавляется в `PATH` только текущего BAT-процесса. Полный fork `graspologic` требует `numpy<2`, поэтому он не устанавливается. Его активный в RAGFlow GraphRAG код использует hierarchical Leiden из компактного Rust-пакета `graspologic-native`; Windows x64 wheel `cp38-abi3` работает с CPython 3.13 и не зависит от NumPy 1.x. Неиспользуемый GraphRAG node2vec helper, которому нужен полный `graspologic`, в этот профиль не входит.

Нейтральные RAGFlow assets тоже имеют неизменяемые revision/hash/size: DeepDoc ONNX, text-concat XGBoost, NLTK ZIP, cl100k и Tika 3.3.0. Tika JAR и canonical `.jar.md5` лежат в корне RAGFlow; будущий launcher задаст `TIKA_SERVER_JAR=file:///.../ragflow/tika-server-standard-3.3.0.jar` и использует JDK из Elasticsearch.

Upstream `ragflow_deps\download_deps.py` намеренно не запускается: это helper для Linux/Docker dependency layer, который в том числе получает Ubuntu `.deb`. Его нельзя считать универсальным Python dependency installer для native Windows. Проектный `prepare_ragflow_assets.py` переносит только действительно нужные platform-neutral assets и проверяет каждый из них по закреплённым revision, размеру и SHA-256.

## OCR: PP-StructureV3 для 8 ГБ Pascal

RAGFlow 0.27.1 ожидает асинхронный контракт `/api/v2/ocr/jobs`; это видно в его [штатном PaddleOCR parser](https://github.com/infiniflow/ragflow/blob/v0.27.1/deepdoc/parser/paddleocr_parser.py). Basic PaddleX endpoint имеет другой контракт. Поэтому `ocr_job_gateway.py` не патчит RAGFlow, а адаптирует именно jobs API к одной локальной `PPStructureV3` pipeline на `127.0.0.1`.

Профиль одной GTX 1080:

- `PP-DocLayout-L` — более сильный layout detector;
- `PP-DocBlockLayout` — regions/reading order;
- `PP-OCRv6_medium_det` — актуальный medium text detector;
- `eslav_PP-OCRv5_mobile_rec` — официальный recognizer для русского/белорусского/украинского/английского текста;
- `SLANet_plus` — компактное восстановление HTML/cell structure для найденных table-регионов;
- short side `736`, hard max `4000`, batch `1`;
- `use_table_recognition` всегда включён; таблицы обрабатываются последовательно с batch 1 и переиспользуют общий OCR result;
- formulas, charts, seals, document preprocessing и orientation branches выключены.

PP-OCRv6 medium recognition хорош, но его unified language set не включает кириллицу, поэтому detector v6 сочетается с East Slavic v5 recognizer. Для таблиц выбран поддерживаемый компактный `table_recognition` subpipeline с одной `SLANet_plus`, а не `table_recognition_v2`, который инициализирует классификатор, две structure-модели и два RT-DETR cell detector. Так сохраняется структура строк/столбцов при существенно меньшем VRAM-риске. Штатный parser RAGFlow 0.27.1 пока возвращает table HTML как table-labelled section, а отдельный список `tables` оставляет пустым; GraphRAG получает структурированный HTML в содержимом секции.

Strict gate проверяет не только `paddle.is_compiled_with_cuda()`. Он требует CUDA 11.8, видимый GPU, capability не ниже 6.1, наличие соответствующей архитектуры в `paddle.version.cuda_archs()`, выполняет матричное вычисление, загружает pipeline из пяти локальных каталогов и прогоняет image+PDF с размеченной таблицей через штатный RAGFlow parser. Тест обязан получить table-labelled section и связанные значения двух ячеек. Любая ошибка завершает этап; CPU fallback отсутствует.

Важно: современная [Windows installation matrix Paddle](https://www.paddlepaddle.org.cn/documentation/docs/install/pip/windows-pip_en.html) формально ориентирована на более новые GPU, хотя выбранный wheel содержит `sm_61`. Поэтому только target E2E, а не статический анализ wheel, подтверждает конкретные GTX 1080/driver.

## Frontend и сервисы

Node 24.20.0 LTS существует только в `app\build`: `npm ci` использует upstream `package-lock.json`, затем создаёт production `dist`; `node_modules` и исходный `dist` удаляются. В offline bundles попадает только статический `app\web`.

Сервисы остаются переносимыми binaries:

- MySQL 8.0.40 и Elasticsearch 8.11.3 — консервативные pins для RAGFlow; Elasticsearch приносит собственный JDK;
- [Silo 2026-08-06](https://github.com/pgsty/silo/releases/tag/RELEASE.2026-08-06T00-00-00Z) сохраняет MinIO wire/config/on-disk compatibility и имеет Windows archive;
- Valkey 8.1.6 берётся из community Windows build. Это не официально поддерживаемая Valkey платформа, поэтому будущий этап обязан проверить реальные queue semantics;
- Caddy 2.11.4 позже будет обслуживать SPA и proxy, не требуя Node в runtime.

На этапе 1 binaries только запускаются с безопасными version/probe arguments. Инициализация базы, S3 bucket, schema migration и постоянные процессы относятся к offline install/start, а не к preparation.

## Проверка целостности

Trust chain выглядит так:

1. все 23 вручную положенных файла имеют обязательный SHA-256 в `artifacts.sha256`;
2. распаковка идёт во временный каталог, archive layout/key проверяется до atomic replacement;
3. dependency graph сохраняется в compiled/freeze records; npm использует lock integrity;
4. каждый скачиваемый RAGFlow asset проверяется по byte size и SHA-256, provenance хранится в JSON;
5. Python local-wheel `direct_url.json` удаляются вместе с RECORD-строками;
6. native imports, tokenizer, XGBoost, OCR API contract и portable executables проходят smoke;
7. audit отклоняет symlink/junction/reparse point и точный абсолютный build-root в launch/config/source/PE файлах;
8. девять solid-архивов тестируются, распаковываются вместе в изолированную копию и повторно проверяются без доступа к исходным runtime/cache;
9. только после этого пишется `prepared.ok`, рассчитываются SHA-256 bundles и новый набор атомарно заменяет старый.

## GPU и планируемые GGUF

`Vikhr-Nemo-12B Q4_K_M` и `Qwen3-Embedding-8B-Q4_K_M.gguf` не скачиваются автоматически: требуется выбрать точные repository/revision/file и зафиксировать отдельные SHA-256. GGUF переносятся без повторного 7z-сжатия.

Две карты по 8 ГБ не дают одному процессу 16 ГБ contiguous VRAM. Практический режимный план:

1. ingestion: chat остановлен; OCR работает на выбранной карте, затем embedding обрабатывает очередь;
2. chat: OCR pipeline выгружен; Vikhr-Nemo распределяется Vulkan backend по двум картам; embedding запускается ограниченно/последовательно;
3. maintenance: только CPU/data services.

Одновременный resident OCR + 12B chat + 8B embedding почти наверняка упрётся в VRAM. Будущий launcher должен управлять состояниями, а не просто запускать всё сразу. Конкретные layer split, context и KV-cache будут зафиксированы после измерений на целевом ПК.

## Следующие отдельные этапы

1. `2.INSTALL-OFFLINE.bat`: проверка bundle SHA-256, staging extraction, atomic install, path-sensitive конфигурации, target GPU gate, DB/S3/Valkey/Elasticsearch integration tests.
2. `3.START.bat`: PID/lock files, режимы ingestion/chat, безопасный порядок старта и readiness endpoints.
3. `4.STOP.bat` и `5.STATUS.bat`: graceful shutdown и health, а не `taskkill` по title.
4. End-to-end ingestion: PDF → PP-StructureV3 layout/text → RAGFlow chunking → Qwen3 embedding → Elasticsearch retrieval → Vikhr answer.
