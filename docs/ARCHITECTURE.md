# Архитектура модульной сборки

Каждый каталог `modules/<name>` является самостоятельной единицей поставки:

```text
module.json              фиксированные версии, URL, порты и зависимости
PREPARE-ONLINE.bat       единственная online-точка входа модуля
src/prepare.ps1          staging, проверка ключевых файлов, sealing, упаковка
src/build-hook.ps1       только модульная сборка Python/web (если нужна)
MODULE.bat               offline install/start/stop/status/verify
control.ps1              конфигурация и health-check конкретного компонента
lib/runtime.ps1          локальная копия portable/process/seal primitives
_src/                    только online-входы и cache этого модуля
prepared/                только архив этого модуля
```

Внутри переносимого архива всегда один путь `modules/<name>`. Поэтому архивы
можно распаковать вручную в любом порядке и они не перезаписывают друг друга.
`payload.sha256.json` создаётся после сборки и проверяется до упаковки, при
offline install и по команде verify. Изменяемые `config/runtime`, `data`,
`logs`, `state`, `temp` не входят в seal.

## Граф runtime-зависимостей

```text
mysql ─────────┐
elasticsearch ─┤
silo ──────────┼──> ragflow API/task ──> web gateway
valkey ────────┤
llama.cpp APIs ┤
paddleocr API ─┘
```

Зависимость означает только чтение connection contract и обращение к loopback
endpoint. RAGFlow никогда не импортирует Python из OCR-модуля, web не читает
файлы RAGFlow, а общие VC DLL дублируются в Python-модулях сознательно.

## Portable boundary

Каждый контроллер вычисляет корень от собственного файла и перенаправляет
`HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, Python/HF cache внутрь
модуля. Все сервисы слушают `127.0.0.1`. Случайные пароли MySQL, Silo, Valkey и
OCR создаются при install и остаются в `state`. Никаких system-wide операций
контроллеры не выполняют.

Корневая оболочка не владеет payload или PID. Она реализует только профили и
вызывает публичную команду каждого модуля. Поэтому ошибка сборки/установки
локализуется в одном архиве, одном state и одном наборе логов.

Каждый модуль записывает PID вместе с Windows process start time и считает
процесс своим только при совпадении обоих значений, поэтому повторно
использованный PID не завершается как старый сервис. Readiness не имеет
искусственного дедлайна: медленный HDD может инициализироваться сколько нужно,
но преждевременное завершение процесса немедленно считается ошибкой. MySQL,
Valkey и Caddy сначала получают штатную shutdown-команду; оставшееся дерево
процесса при необходимости завершается по точному PID.
