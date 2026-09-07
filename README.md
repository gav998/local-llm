# local_llm

Portable offline-стек для Windows 11 x64: RAGFlow 0.27.1, локальный `llama.cpp`, PaddleOCR / PP-StructureV3, MySQL, Elasticsearch, Silo, Valkey и Caddy. Не нужны Docker, WSL, права администратора, installer, Windows Service, запись в registry или изменение системного `PATH`.

Проект состоит из двух этапов:

1. [1.PREPARE-ONLINE.bat](./1.PREPARE-ONLINE.bat) один раз собирает и проверяет runtime на Windows-компьютере с интернетом.
2. [LOCAL-LLM.bat](./LOCAL-LLM.bat) устанавливает готовые архивы на целевом компьютере без интернета и управляет стеком через меню или команды.

Целевое железо: Intel Core i3-8350K, 32 ГБ RAM и две NVIDIA GTX 1080 по 8 ГБ. Все приложения, данные, модели, логи, временные файлы, профили и кэши размещаются внутри каталога проекта на внешнем HDD. Одну общую установку могут последовательно использовать разные Windows-пользователи; одновременный запуск несколькими пользователями не поддерживается.

## Быстрый сценарий

### 1. Подготовка на Windows с интернетом

Поместите репозиторий в короткий путь на NTFS/exFAT, например `X:\local_llm`. Online builder намеренно отклоняет пробелы и CMD/URI-метасимволы в build path. Сначала соберите вручную загружаемые vendor-файлы:

```bat
1.PREPARE-ONLINE.bat artifacts-only
```

BAT печатает фиксированный URL и ожидаемый путь для каждого отсутствующего файла. Для всех 23 vendor-файлов проверяется наличие ожидаемого имени; контрольные суммы не вычисляются.

Затем выполните полную online-сборку:

```bat
1.PREPARE-ONLINE.bat
```

Если сборка выполняется непосредственно на целевой GTX 1080, можно сразу провести аппаратный тест:

```bat
1.PREPARE-ONLINE.bat gpu-test
```

Результат появится в `_src\prepared`. В набор входят девять архивов, offline launcher `LOCAL-LLM.bat`, README, bootstrap `7zr.exe`, audit и `prepared.ok`.

### 2. Перенос на компьютер без интернета

Скопируйте **весь** каталог `_src\prepared` на внешний HDD целевого компьютера. Путь может содержать пробелы и кириллицу, но из-за синтаксиса CMD не должен содержать `! % & ^ < > |`; launcher проверяет это до работы с файлами. GGUF переносятся отдельно, потому что уже квантованные модели почти не сжимаются. Сначала установите runtime, затем положите модели в созданные каталоги:

```text
<каталог с LOCAL-LLM.bat>\
  LOCAL-LLM.bat
  00-bootstrap-tools.7z ... 50-llama-vulkan-runtime.7z
```

После `install`:

```text
app\models\embed\Qwen3-Embedding-8B-Q4_K_M.gguf
app\models\llm\Vikhr-Nemo-12B-Q4_K_M.gguf
```

Если имена отличаются, отредактируйте пути через пункт 8 меню.

### 3. Установка и запуск

Двойной щелчок по `LOCAL-LLM.bat` открывает меню. Первый запуск — пункт `1`.

```bat
LOCAL-LLM.bat install
```

Установщик:

- находит архивы рядом с BAT или в `_src\prepared`;
- проверяет наличие всех обязательных файлов набора до запуска `7zr.exe`;
- тестирует каждый архив и распаковывает всё в новый staging-каталог;
- публикует `app` одним `move`, не смешивая частичную установку с рабочей;
- создаёт уникальные локальные пароли и конфигурацию всех путей/портов;
- проверяет оба Python runtime, RAGFlow assets, OCR API contract и GraphRAG;
- обязательно запускает реальный CUDA 11.8 / `sm_61` OCR+table E2E без CPU fallback;
- инициализирует и защищает portable MySQL;
- пишет `app\data\control\install.ok.json` только после полного успеха.

GPU-проверка может занять несколько минут. Если она не прошла, файлы и логи сохраняются, но запуск блокируется; после исправления причины достаточно повторить `install`.

После установки доступны три профиля:

| Команда | Что запускается | Назначение |
|---|---|---|
| `LOCAL-LLM.bat start core` | MySQL, Elasticsearch, Silo, Valkey, RAGFlow API, Caddy | UI и обслуживание данных без нейросетевой нагрузки |
| `LOCAL-LLM.bat start ingestion` | core + embeddings + PaddleOCR + task executor | загрузка, OCR, таблицы, chunking и индексация |
| `LOCAL-LLM.bat start chat` | core + embeddings + Vikhr chat | поиск и ответы; OCR/worker останавливаются для освобождения VRAM |

Откройте адрес, напечатанный BAT (по умолчанию `http://127.0.0.1:9388`). Все сервисы слушают только loopback.

## Меню и команды

```bat
LOCAL-LLM.bat                         rem интерактивное меню
LOCAL-LLM.bat install                 rem установка/повторная строгая проверка
LOCAL-LLM.bat start core
LOCAL-LLM.bat start ingestion
LOCAL-LLM.bat start chat
LOCAL-LLM.bat status
LOCAL-LLM.bat stop
LOCAL-LLM.bat verify
LOCAL-LLM.bat verify --gpu
LOCAL-LLM.bat config show
LOCAL-LLM.bat config edit
LOCAL-LLM.bat devices                 rem Vulkan-устройства llama.cpp
```

`status` сверяет не только PID: supervisor PID и child PID привязаны к identity процесса, а сетевые сервисы проверяются через реальные readiness/health endpoints. Осиротевший child показывается отдельно и безопасно завершается командой `stop` после повторной проверки identity. `stop` сначала использует штатное завершение MySQL, Valkey и Caddy, затем посылает отдельной process group сигнал завершения; принудительно завершается только конкретное дерево PID и только после timeout.

При ошибке запуска новые процессы текущей попытки откатываются. Процессы никогда не ищутся и не завершаются по имени или заголовку окна.

## Перенос установки и настройка путей

Корень всегда вычисляется от `%~dp0`; буква диска и имя каталога не зашиты. После переноса всего каталога на другой диск просто запустите `LOCAL-LLM.bat`: перед каждой командой он заново генерирует path-sensitive MySQL, Elasticsearch, Caddy и RAGFlow configs для текущего расположения.

Настройки находятся в:

```text
app\config\runtime\local-llm.ini
```

Пункт `8` открывает этот файл в Notepad и после закрытия проверяет значения. Перед редактированием стек должен быть остановлен. Можно менять:

- порты;
- относительные пути к обоим GGUF внутри `app` (выход за portable-корень блокируется);
- CUDA index PaddleOCR;
- размещение llama.cpp по Vulkan GPU и `tensor-split`;
- context/batch;
- лимиты памяти MySQL, Elasticsearch и Valkey;
- startup/shutdown timeout.

Секреты хранятся отдельно в `app\config\runtime\secrets.json`, повторно не генерируются и не должны публиковаться. После переноса `install` повторять не требуется; повторный `install` той же версии заново создаёт path-sensitive configs и выполняет полную проверку, не перезаписывая данные. Launcher и controller разных версий намеренно несовместимы: нельзя смешивать файлы из разных prepared-наборов, а upgrade существующих данных следует делать в отдельной копии.

## GPU-профили и модели

Две GTX 1080 по 8 ГБ не являются одной картой с 16 ГБ. Конфигурация по умолчанию рассчитана на раздельные режимы:

- ingestion: OCR на CUDA GPU 0, embedding на llama/Vulkan GPU 1;
- chat: embedding на GPU 0, Vikhr распределяется `0.20,0.80` с основной GPU 1;
- `core`: нейросетевые процессы не запущены.

Нумерация CUDA и Vulkan обычно совпадает, но это не гарантируется. Сначала выполните `LOCAL-LLM.bat devices`, затем при необходимости исправьте `[gpu]` в `local-llm.ini`. Defaults для 8 ГБ — embedding context/batch `2048/512`, chat `4096/256`; конкретные context/batch/tensor split всё равно следует подтвердить на целевых GGUF и документах.

RAGFlow получает локальные OpenAI-compatible endpoints:

- embeddings: `http://127.0.0.1:6380/v1`;
- chat: `http://127.0.0.1:6381/v1`;
- PaddleOCR: `http://127.0.0.1:9399`, алгоритм `PP-StructureV3`.

Defaults записываются в локальный `service_conf`. После создания первого пользователя проверьте в UI, что для dataset выбран локальный embedding provider, а для сканов — layout recognizer `PaddleOCR` / `PP-StructureV3`. RAGFlow хранит выбор модели конкретного пользователя/dataset в MySQL, поэтому launcher не перезаписывает его при каждом старте.

## Состав и версии

| Компонент | Версия / профиль |
|---|---|
| RAGFlow | `0.27.1`, native Windows compatibility patch |
| RAGFlow Python | CPython `3.13.15` standalone |
| OCR Python | CPython `3.11.16` standalone |
| PaddlePaddle | GPU `3.3.1`, CUDA 11.8, Windows `cp311`, проверка `sm_61` |
| PaddleOCR / PaddleX | `3.7.0` / `3.7.2` |
| OCR models | PP-DocLayout-L, PP-DocBlockLayout, PP-OCRv6 medium det, East Slavic PP-OCRv5 rec, SLANet_plus |
| llama.cpp | `b10786`, Windows Vulkan |
| Data services | MySQL `8.0.40`, Elasticsearch `8.11.3`, Silo `2026-08-06`, Valkey `8.1.6` |
| Web proxy | Caddy `2.11.4` |
| Online-only tools | Node `24.20.0`, MinGit `2.55.0.5`, uv `0.12.9` |

RAGFlow требует Python `>=3.13,<3.14`, а выбранный Windows GPU-wheel Paddle имеет ABI `cp311`, поэтому два автономных Python runtime являются намеренным решением.

## Где искать состояние и ошибки

```text
app\logs\                         логи каждого runtime-сервиса и проверок
app\data\control\                PID/identity metadata, профиль, install.ok
app\config\runtime\              пользовательский INI, secrets, generated configs
app\data\mysql\                  MySQL data
app\data\elasticsearch\          Elasticsearch indices
app\data\silo\                   S3 objects
app\data\valkey\                 queue/cache persistence
app\data\ocr-jobs\               локальные OCR jobs/results
_work\offline-install-...\        сохранённый staging при ошибке распаковки
```

При сбое сначала выполните `LOCAL-LLM.bat status`, затем приложите полный лог соответствующего сервиса. Нельзя отключать диск или завершать процессы вручную во время записи MySQL/Elasticsearch/Silo; используйте `LOCAL-LLM.bat stop`.

Если окно было принудительно закрыто именно во время распаковки, следующий `install` fail-closed остановится на `_work\offline-install.lock`. Удалять этот каталог можно только после проверки, что другой `LOCAL-LLM.bat install` не выполняется; сохранённый `_work\offline-install-*` пригоден для диагностики, но не считается установленным приложением.

## Границы проверки

- Native Windows не является официальным deployment target RAGFlow; совместимые отклонения точно закреплены и проверяются в online build.
- Valkey Windows — community build, поэтому его реальная queue-семантика должна быть подтверждена на целевой машине.
- NVIDIA driver и Vulkan runtime должны быть заранее установлены администратором системы; проект их не меняет.
- Linux-сервер этого репозитория используется только для source/unit/static-проверок. Реальный BAT, Windows executables, две GTX 1080 и OCR E2E проверяются на целевом Windows ПК.
- Application-level portability не заменяет ProcMon-приёмку под чистой Windows-учётной записью.

Подробное устройство trust chain, native RAGFlow patch, OCR gateway и управления процессами: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).
