# local_llm

Проект содержит первый самостоятельный этап новой portable-сборки: [1.PREPARE-ONLINE.bat](./1.PREPARE-ONLINE.bat). Он готовит на **Windows x64 с интернетом** проверенное дерево `app`, прогревает OCR строго на GPU и превращает каталоги с тысячами файлов в несколько solid-архивов в `_src\prepared`.

Это не переработка старого монолитного BAT по строкам. Архитектура изменена так, чтобы офлайн-этап впоследствии только проверял SHA-256 и распаковывал готовые компоненты. На офлайн-машине не должны запускаться `pip`, `npm`, компилятор или сетевые загрузчики.

## Что зафиксировано

| Компонент | Версия / решение | Причина |
|---|---|---|
| RAGFlow | `0.27.1` | актуальный стабильный релиз; требует Python `>=3.13,<3.14` |
| Python для RAGFlow | `3.13.15`, python-build-standalone | обычный `venv` и Conda плохо переносят смену буквы диска |
| Node | `22.23.2 LTS` | только онлайн-сборка production frontend; `node_modules` не нужен в runtime |
| PaddleOCR | `3.7.0` | содержит PP-OCRv6; это новее упомянутой версии 3.1 |
| PaddleX | `3.7.2` | совместимая ветка `3.7.x`, basic serving с `/layout-parsing` |
| Python для OCR | `3.11.16`, отдельный runtime | официальный Paddle GPU wheel имеет ABI `cp311` |
| PaddlePaddle GPU | `3.3.1`, CUDA 11.8 wheel | wheel содержит код `sm_61` для GTX 1080; CUDA 12.6 wheel Pascal не содержит |
| OCR-профиль | PP-StructureV3: layout + reading order + PP-OCRv6 medium | полный профиль не помещается надёжно в 8 ГБ VRAM |
| llama.cpp | snapshot `b10786`, Vulkan | текущие готовые CUDA-сборки нельзя считать надёжными для Pascal; Vulkan остаётся GPU-инференсом |
| MySQL / Elasticsearch | `8.0.40` / `8.11.3` | официальные консервативные pins RAGFlow |
| Object storage | Silo release `2026-08-06` | актуальная замена MinIO в RAGFlow |
| Redis protocol | Valkey `8.1.6` community Windows build | официальный Valkey Windows не поддерживает; `fakeredis` для очереди задач недостаточен |

Официальные исходные данные: [RAGFlow v0.27.1](https://github.com/infiniflow/ragflow/releases/tag/v0.27.1), [его `pyproject.toml`](https://github.com/infiniflow/ragflow/blob/v0.27.1/pyproject.toml), [PaddleOCR v3.7.0](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0), [PP-StructureV3](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-StructureV3/PP-StructureV3.html), [PaddleX serving](https://github.com/PaddlePaddle/PaddleX/blob/release/3.7/docs/pipeline_deploy/serving.en.md) и [Windows Paddle installation](https://www.paddlepaddle.org.cn/documentation/docs/install/pip/windows-pip_en.html).

## Каталоги

```text
local_llm\
  1.PREPARE-ONLINE.bat
  _src\
    <vendor files>          скачиваются человеком по подсказкам BAT
    project\                versioned config/requirements/helpers
    logs\                   журнал каждого долгого шага
    prepared\               готовые архивы для будущего offline install
  _work\                    только временная работа этого проекта
  app\
    build\                  online-only Node, Git, uv и OCR wheelhouse
    cache\paddlex\...        явно подготовленные модели OCR
    config\                 lock/freeze/build/smoke records
    data\                   переносимый profile, NLTK и будущие данные
    models\llm              Vikhr-Nemo GGUF будет добавлен отдельно
    models\embed            Qwen3-Embedding GGUF будет добавлен отдельно
    ragflow                 backend source и нейтральные model assets
    runtime                 два Python runtime и llama.cpp Vulkan
    services                MySQL, Elasticsearch, Silo, Valkey, Caddy
    web                     готовый production `dist`
```

Артефакты поставщика лежат прямо в `_src`, как и требовалось. `_src\project` отделяет только наши небольшие файлы, а `_src\prepared` — конечный результат.

## Как запускать

1. Используйте Windows 10/11 x64 и короткий путь на **NTFS**, например `X:\local_llm`. FAT32 не допускает GGUF больше 4 ГБ; путь не должен содержать `!`.
2. NVIDIA driver должен быть установлен заранее. BAT не требует повышения прав и принципиально не устанавливает driver, service, registry key или системный `PATH`.
3. При желании сначала выполните `1.PREPARE-ONLINE.bat artifacts-only`. Для каждого отсутствующего файла BAT покажет английскую инструкцию, точный URL и будет после `pause` проверять тот же файл снова. Сам BAT vendor-файлы не скачивает.
4. Запустите `1.PREPARE-ONLINE.bat` без аргументов. На этом шаге `pip`, `npm`, Hugging Face и NLTK используют интернет для транзитивных зависимостей.
5. Успехом считается только наличие `_src\prepared\prepared.ok`, всех архивов и прохождение реального GPU warmup. Ошибка OCR не переключается на CPU.

Главная четырёхаргументная функция находится в BAT под меткой `:EnsureArtifact`:

```bat
call :EnsureArtifact "file-name" "destination-under-app" "key-file" "fixed-url"
```

Она проверяет безопасные относительные пути, ждёт недостающий `_src\file-name`, сверяет известный SHA-256, создаёт каталоги, копирует либо распаковывает во временный каталог, убирает единственный versioned root архива и проверяет конечный key file. После автоматической ошибки она ждёт ручной распаковки и повторяет проверку key file.

## Что окажется в `_src\prepared`

```text
00-bootstrap-tools.7z
10-python-rag-runtime.7z
20-python-ocr-gpu-runtime.7z
21-paddle-models.7z
22-ocr-wheelhouse.7z
30-ragflow-backend.7z
31-ragflow-web-dist.7z
40-services.7z
41-config-seed.7z
50-llama-vulkan-runtime.7z
SHA256SUMS.txt
SOURCE-SHA256SUMS.txt
build-info.txt
prepared.ok
```

GGUF повторно не сжимаются: это почти не уменьшает размер и зря нагружает HDD. Их следует переносить отдельными файлами с собственным SHA-256.

## Важные ограничения

- RAGFlow документирует source launch с Docker-сервисами; native Windows без Docker — наша проверяемая, но не upstream-supported конфигурация. Установка официального frozen dependency graph может потребовать MSVC на **онлайн-сборочной** машине. BAT при этом завершится ошибкой и сохранит точный журнал, а не создаст ложный `prepared.ok`.
- Полный PP-StructureV3 по официальным замерам требует больше 8 ГБ VRAM. Даже опубликованный lightweight-вариант достигает примерно 8,2 ГБ. Поэтому основной профиль сохраняет layout, порядок чтения и лучший общий PP-OCRv6 medium, но отключает tables/formulas/charts/seals. Эти функции нужно делать отдельными последовательными workers позднее.
- `gpu:0,1` не превращает две GTX 1080 в одну карту на 16 ГБ: PaddleX создаёт отдельную pipeline-копию на каждой карте. Нужен режимный планировщик: ingestion (`OCR -> embedding`) и chat не должны одновременно занимать обе карты.
- «Строго GPU» здесь означает, что все нейросетевые вычисления OCR обязаны пройти на CUDA. Растеризация PDF, декодирование изображений, post-processing, JSON и chunking всё равно используют CPU.
- Windows wheel cu118 фактически содержит `sm_61`, но текущая общая страница Paddle формально заявляет более новое поколение GPU. Поэтому warmup именно на целевой GTX 1080 — обязательная часть, а не формальность.
- Все известные cache/profile variables перенаправлены в `app`, но абсолютную гарантию отсутствия обращений native DLL к Known Folders нужно подтвердить на целевой Windows через Process Monitor.
- Одно portable-хранилище можно использовать из разных учётных записей последовательно. Одновременный запуск общей MySQL/Elasticsearch базы несколькими пользователями недопустим; отключение USB/HDD во время работы может повредить данные.

Подробное обоснование и границы следующих этапов находятся в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

