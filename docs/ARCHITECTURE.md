# Архитектура portable local_llm

## Два этапа и trust boundary

`1.PREPARE-ONLINE.bat` превращает Windows-компьютер с интернетом в одноразовую сборочную машину. Он разрешает dependency graph, собирает frontend, проверяет native imports/assets и выпускает закрытый набор `_src\prepared`.

```text
23 вручную полученных vendor artifacts в _src
                    |
                    v
 file presence -> staging extract -> version/key checks
                    |
                    v
 online resolve/build -> smoke/audit -> final tree seals
                    |
                    v
    9 solid 7z -> isolated rehydrate -> required file set
                    |
                    v
     atomic _src\prepared + LOCAL-LLM.bat + README
```

`LOCAL-LLM.bat install` работает уже без сети. До запуска payload executable он проверяет наличие всех обязательных файлов prepared-набора. Затем bootstrap `7zr.exe` раскрывает полный `7za.exe`; все архивы отдельно тестируются и распаковываются в случайный staging. После последнего использования staged `7za.exe` launcher заново вычисляет seals immutable-компонентов, поэтому seal описывает именно опубликованное дерево, а не промежуточное состояние packager. Готовое `app` публикуется одним `move`. Частичный staging не смешивается с рабочей установкой и при ошибке сохраняется для диагностики.

Отладочный marker `notest` рядом с BAT включает сознательно непроверенный
короткий путь. Online-сценарий упаковывает уже существующее `app`, не изменяя
его и не выполняя recursive tree audit/cleanup/probes/audits/seals/archive-test/rehydration. Приватная
runtime-конфигурация и логи исключаются фильтрами 7-Zip. Offline-сценарий только
распаковывает архивы и публикует дерево без сверки версии launcher/controller.
Контроллер пишет отдельный `validation_mode=notest`,
а не выдаёт пропущенный GPU E2E за успешный. Такой marker принимается при
старте только пока присутствует сам файл `notest`; конфигурация, инициализация
MySQL и реальные health checks выполняются при первом запуске профиля.

Установка считается завершённой только после static/import проверок, реального CUDA/OCR/table E2E без CPU fallback и инициализации MySQL. Между неуспешными попытками `install-progress.json` сохраняет законченные фазы и привязывает их к пути, control version, seal-маркерам и проверочным конфигам. Полное чтение sealed payload повторяется на каждой попытке; только после него разрешён skip дорогих завершённых фаз. Последним записывается `app\data\control\install.ok.json`, progress удаляется; `start` без корректного marker запрещён.

Wheelhouse не используется как переносимый offline-формат: это перенесло бы
platform resolution на целевую машину. Но online-сборщик хранит разрешённые
Windows wheels в `_src\wheelhouse\rag` и `_src\wheelhouse\ocr`, а внутренние
кэши package managers — в `_src\package-cache`. При пересборке сначала делается
полностью offline-проба wheelhouse и только недостающие файлы запрашиваются из
индекса. В prepared-архивы по-прежнему попадают уже установленные и проверенные
runtime trees. GGUF переносятся отдельно.

## Portable boundary

Корень вычисляется от `%~dp0`. До запуска дочерних процессов BAT/controller назначают локальные `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, XDG, Hugging Face, Paddle, Python bytecode, CUDA cache, NLTK и tiktoken roots. Дочерние сервисы получают новый whitelist environment, а не весь профиль пользователя.

Принципиально отсутствуют:

- `setx`, registry writes и изменение системного `PATH`;
- MSI/EXE installation и Windows Service registration;
- Docker/WSL;
- запись Python/Node/package-manager runtime в профиль пользователя;
- path-bound Python launchers из `Scripts`;
- `node_modules` и online build tools в offline bundles.

Microsoft VC Redistributable не запускается: из проверенного официального EXE извлекаются app-local DLL, включая `vcomp140.dll` для XGBoost. Системными зависимостями остаются Windows DLL, NVIDIA driver и Vulkan loader.

Path-sensitive файлы находятся в `app\config\runtime` и атомарно регенерируются перед каждой командой. Они, включая `secrets.json`, а также сгенерированный `ragflow\conf\local.service_conf.yaml` и runtime-логи RAGFlow удаляются из online build tree до sealing и не попадают в prepared-архивы. Поэтому offline install создаёт новые secrets уже в фактическом месте установки, а перенос всего установленного каталога или смена буквы диска не требует reinstall. В установленном каталоге secrets генерируются один раз и не перезаписываются.

## Почему два Python

RAGFlow 0.27.1 требует CPython `>=3.13,<3.14`; Windows GPU-wheel Paddle 3.3.1/cu118 выбран в ABI `cp311`. Поэтому RAGFlow получает standalone CPython 3.13.15, OCR — standalone CPython 3.11.16. Оба runtime изолированы и связаны loopback HTTP.

Conda не используется: prefix relocation и повторная запись абсолютных путей не дают преимуществ для этого профиля.

## RAGFlow на native Windows

Upstream lock экспортируется через `uv export --frozen`, затем для Windows CPython 3.13 компилируется hash-locked граф с ограниченными отклонениями:

- `numpy==2.3.5`, потому что upstream 1.26.4 не имеет нужного cp313 Windows wheel;
- `xgboost==2.1.4`, совместимый с NumPy 2;
- `scikit-learn==1.8.0`, потому что RAGFlow импортирует `sklearn` из task/deepdoc кода, но upstream держит старый `scikit-learn==1.5.0` в отключённом блоке без CPython 3.13 wheel;
- `datrie==0.8.3` как закреплённый MSVC/cp313 wheel;
- metadata constraint `infinity-sdk==0.7.3` меняется на проверенный `numpy>=2,<2.4` с пересчётом RECORD;
- полный `graspologic` исключён из-за `numpy<2`, но GraphRAG включён через `graspologic-native==1.2.5` и audited adapter;
- `infinity-emb` исключён: embeddings обслуживает отдельный llama.cpp server.

Patch применим только к известным SHA-256 исходникам RAGFlow. Verifier импортирует task executor и полный GraphRAG entrypoint, выполняет hierarchical Leiden, русский tokenizer, offline cl100k и prediction закреплённой XGBoost-модели.

RAGFlow tree sealed целиком, кроме двух заранее объявленных runtime-путей:

```text
conf/local.service_conf.yaml    location/secrets/endpoints текущей машины
logs/                           штатные rotating logs RAGFlow
```

Исключения понимаются fingerprint-функцией как точный файл и точный directory prefix; произвольные соседние файлы всё ещё меняют seal. Это устраняет ложное падение integrity после первого запуска, не превращая backend в непроверяемый mutable tree.

Seal хранит общий SHA-256 от отсортированных строк `relative path + size +
file SHA-256` и число файлов. Для mutable Python/RAGFlow/web-деревьев рядом с
marker также архивируется per-file manifest. Он не ослабляет проверку общего
hash, а позволяет при расхождении записать точные `ADDED`, `MISSING` и
`CHANGED` пути. Online rehydrate пишет такой diff в
`_src\logs\tree-fingerprint-mismatch.log`, offline controller — в
`app\logs\tree-seal-verify.log`. `.pyc` внутри runtime остаются частью seal;
новый runtime bytecode всегда направляется в `app\cache\python-bytecode`.

Elasticsearch запускается с рабочим каталогом в `app\logs\elasticsearch\runtime`, потому что штатные JVM options используют относительные пути `logs/gc.log`, `logs/hs_err_pid...` и `data` для аварийных файлов. Для совместимости с установками, которые уже запускались старым controller, из seal исключён только `services\elasticsearch\logs\`; бинарники, библиотеки и конфигурация vendor-дерева продолжают проверяться полностью.

Нейтральные DeepDoc, XGBoost, NLTK, cl100k и Tika assets имеют фиксированные revision/hash/size. `TIKA_SERVER_JAR` указывает на app-local JAR, Java берётся из Elasticsearch.

## OCR: PP-StructureV3 на 8 ГБ Pascal

RAGFlow ожидает асинхронный `/api/v2/ocr/jobs`, а PaddleX basic serving предоставляет другой протокол. `ocr_job_gateway.py` адаптирует контракт к одной strict-GPU `PPStructureV3` pipeline на `127.0.0.1`.

Профиль включает:

- PP-DocLayout-L;
- PP-DocBlockLayout;
- PP-OCRv6 medium detector;
- East Slavic PP-OCRv5 recognizer для русского/белорусского/украинского/английского;
- SLANet_plus для HTML/cell structure таблиц;
- batch 1, short side 736, hard max 4000;
- выключенные formulas/charts/seals/orientation/preprocessing branches.

Табличный pipeline переиспользует общий OCR result и не создаёт второй OCR внутри каждой таблицы. Strict gate требует CUDA 11.8, видимый GPU, capability `>=6.1`, наличие `sm_61` в wheel, реальную CUDA-матрицу, загрузку пяти локальных моделей, image+PDF OCR и table-labelled section через штатный parser RAGFlow.

Gateway bind разрешён только на loopback, использует bearer token и сохраняет jobs в `app\data\ocr-jobs`. `PADDLE_PDX_DISABLE_DEVICE_FALLBACK=1`; ошибка GPU не переключает inference на CPU.

## Data services и generated configs

Controller генерирует:

- `mysql.ini`: app-local basedir/datadir, loopback, `mysqlx=OFF`, ограниченный pool;
- отдельный `ES_PATH_CONF`: single-node, loopback, security/network download выключены, heap фиксирован;
- `valkey.conf`: loopback, protected mode, пароль, AOF в app-local data; путь к конфигу
  передаётся Cygwin-сборке Valkey в формате `/cygdrive/<drive>/...`;
- Caddyfile: loopback SPA + reverse proxy `/v1/*` и `/api/*`;
- `local.service_conf.yaml`: MySQL, Silo/MinIO, Elasticsearch, Valkey, local llama endpoints и PaddleOCR;
- `local-llm.ini`: единственный пользовательский файл портов, моделей, GPU placement и memory limits.

MySQL впервые запускается с `--initialize-insecure`, затем при обычном старте выполняет одноразовый app-local `init-file`: назначает случайный пароль `root@localhost`, создаёт отдельную учётную запись `root@127.0.0.1` для TCP при включённом `skip-name-resolve` и базу `rag_flow`. Готовность подтверждается реальным авторизованным `SELECT`, после чего временный SQL-файл удаляется, а сервер штатно останавливается. Elasticsearch, Silo и Valkey создают данные при первом start. Все данные вынесены из sealed vendor trees в `app\data`.

Silo сохраняет MinIO API/config compatibility. Valkey — community Windows build; launcher проверяет реальный authenticated `PING` напрямую по RESP/TCP, не полагаясь на поведение Cygwin `valkey-cli.exe`, а целевая приёмка должна дополнительно прогнать RAGFlow queue semantics.

## Процессы, PID identity и остановка

Каждый сервис запускается отдельным `local_llm_supervisor.py` в скрытой Windows console/process group. Metadata содержит supervisor PID, child PID, token и Windows process creation identity. PID считается принадлежащим сервису только при совпадении identity; переиспользованный Windows PID не будет завершён как старый процесс.

Readiness:

| Сервис | Проверка |
|---|---|
| MySQL | authenticated `mysqladmin ping` (при bootstrap также без пароля) |
| Elasticsearch | HTTP root/info |
| Silo | `/minio/health/ready` |
| Valkey | authenticated `PING` |
| llama embedding/chat | `/health` |
| PaddleOCR | `/health` после загрузки GPU pipeline |
| RAGFlow | `/api/v1/system/healthz` со всеми data dependencies |
| Caddy | SPA root через frontend port |
| task executor | live PID identity; очередь контролируется RAGFlow/Valkey |

При `stop` supervisor сначала вызывает штатные shutdown-команды MySQL, Valkey и Caddy. Остальным отправляется `CTRL_BREAK_EVENT` в их отдельную process group. Только после timeout выполняется `taskkill /PID <точный child> /T /F`. Если supervisor аварийно исчез, controller распознаёт child как orphan по паре PID/creation identity и может завершить именно это дерево. Поиск по имени процесса или title не используется.

Запуск профиля транзакционен относительно новых процессов: если readiness одного компонента не достигнут, сервисы, поднятые этой попыткой, останавливаются в обратном порядке; ранее работавший core сохраняется.

У startup readiness нет дедлайна: медленный диск не считается ошибкой. Controller
продолжает короткие health-пробы до готовности, немедленно прерывая ожидание при
завершении child или supervisor. Один независимый console viewer показывает новые
строки всех логов выбранного профиля и не участвует в управлении процессами.

## GPU scheduler

Профили не запускают всё одновременно:

```text
core       data services + RAGFlow API + web
ingestion  core + embedding(GPU 1) + OCR(CUDA GPU 0) + worker
chat       core + embedding(GPU 0) + Vikhr(split GPU 0/1 = 0.20/0.80)
```

Переход между ingestion/chat сначала проверяет наличие требуемых GGUF, затем останавливает конфликтующие worker/OCR/chat и перезапускает embedding с placement нового режима. Defaults `0.20,0.80`, embedding context/batch `2048/512` и chat `4096/256` уменьшают риск выхода за 8 ГБ, но остаются отправной точкой, а не аппаратной гарантией. Нумерация Vulkan сверяется через `llama-server --list-devices`.

## Integrity chain

1. Для 23 vendor artifacts проверяются ожидаемые имена и наличие файлов.
2. Распаковка каждого идёт через staging с key/version marker.
3. Python/npm dependency graph фиксируется в compiled/freeze/lock records.
4. RAGFlow assets проверяются по revision, byte size и SHA-256.
5. Python metadata очищается от build-path.
6. Native imports, GraphRAG, tokenizer, XGBoost и OCR contract проходят smoke.
7. Audit отклоняет reparse points и absolute build-path leaks.
8. Runtime и immutable vendor trees получают final seals с минимальными известными exclusions.
9. Девять архивов rehydrate в изолированное дерево и повторно проверяются до и после запуска payload executable.
10. Prepared payload, launcher, обновляемый control helper и README проверяются как полный обязательный набор файлов.
11. Offline installer проверяет наличие всех файлов и тестирует каждый 7z до atomic publish; при каждой команде BAT также сверяет версию controller.
12. `install.ok` появляется только после target GPU E2E и MySQL initialization.

## Оставшиеся границы

- Реальные BAT/native/GPU тесты выполняются на Windows 11 x64 с двумя GTX 1080; Linux CI даёт source/static/unit coverage.
- После первого запуска нужна end-to-end приёмка пользовательского PDF: upload → OCR/table → embedding → Elasticsearch retrieval → Vikhr answer.
- Process Monitor под чистой учётной записью остаётся финальной проверкой неизвестных обращений native DLL к Windows Known Folders.
- Общий data root поддерживает последовательную, но не одновременную работу разных пользователей.
