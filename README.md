# local_llm — независимые portable-модули

Проект собирает локальный RAG-стек для Windows 11 x64 без Docker, WSL, прав
администратора, Windows Services, реестра и изменения системного `PATH`.
Монолитного установщика больше нет: каждый компонент имеет собственные
исходные загрузки, online-сборку, переносимый архив, offline-конфигурацию,
процессы, данные, логи и health-check.

## Модули

| Каталог | Версия | Публичный интерфейс |
|---|---|---|
| `modules/mysql` | MySQL 8.0.40 | TCP `127.0.0.1:3306`, база и отдельная учётная запись RAGFlow |
| `modules/elasticsearch` | 8.11.3 | HTTP `127.0.0.1:1200` |
| `modules/silo` | 2026-08-06 | MinIO API `127.0.0.1:9000` |
| `modules/valkey` | 8.1.6 | authenticated RESP `127.0.0.1:6379` |
| `modules/llama-cpp` | b10786 Vulkan | OpenAI-compatible embedding/chat API `:6380`/`:6381` |
| `modules/paddleocr` | Paddle 3.3.1, OCR 3.7.0, X 3.7.2 | strict-GPU OCR jobs API `:9399` |
| `modules/ragflow` | 0.27.1 | API `127.0.0.1:9380` и отдельный task executor |
| `modules/web` | RAGFlow web 0.27.1, Caddy 2.11.4 | UI `127.0.0.1:9388` |

Связь выполняется только через loopback API и версионированные локальные
контракты `modules\<name>\state\connection.json`. Один модуль не пишет в
каталоги другого и не использует его бинарные файлы.

## Online-сборка одного модуля

Перейдите в нужный каталог и запустите `PREPARE-ONLINE.bat`. Сценарий покажет
точное имя, неизменённый URL и свой каталог `_src` для каждого отсутствующего
файла. Положите файл туда без переименования и продолжите. Результат появится в
`modules\<name>\prepared\local-llm-<name>-<version>.7z`.

Online-корень не должен содержать CMD-метасимволы `! % & ^ < > |`. Пробелы и
кириллица допустимы для offline-каталога; для тяжёлой online-сборки по-прежнему
рекомендуется короткий путь на NTFS/exFAT, например `X:\local_llm`.

Каждый модуль собирается и проверяется отдельно. Повторять уже успешно
собранные модули для сборки другого не нужно. `CHECK-SOURCES.bat` только
показывает, в каких модулях ещё отсутствуют online-входы; он ничего не собирает.

Для RAGFlow и OCR сохраняются wheelhouse и package cache только в их `_src`.
В переносимый архив входят уже установленные Python runtime, модели и assets,
а не online-инструменты или wheels. Полный исходный перечень и версии находятся
в [docs/SOURCE-INVENTORY.md](docs/SOURCE-INVENTORY.md).

## Ручной перенос и распаковка

1. Соберите восемь модульных `.7z` и запустите `PREPARE-STACK.bat`.
2. На целевом внешнем NTFS/exFAT-диске создайте любой каталог.
3. Вручную распакуйте содержимое `local-llm-stack-2026.09.11.zip` в него.
4. Вручную распакуйте туда же содержимое каждого из восьми `.7z`. Архивы
   содержат непересекающиеся пути `modules\<name>`.
5. Запустите `LOCAL-LLM.bat install`. Оболочка лишь последовательно вызывает
   независимый `MODULE.bat install` каждого модуля.

Можно перенести, распаковать, установить и проверить только один модуль:

```bat
modules\mysql\MODULE.bat install
modules\mysql\MODULE.bat start
modules\mysql\MODULE.bat verify
modules\mysql\MODULE.bat stop
```

## Модели GGUF

Как и раньше, две крупные модели переносятся отдельно и не перепаковываются:

- `Qwen3-Embedding-8B-Q4_K_M.gguf` →
  `modules\llama-cpp\models\embed\Qwen3-Embedding-8B-Q4_K_M.gguf`;
- `Vikhr-Nemo-12B-Q4_K_M.gguf` →
  `modules\llama-cpp\models\llm\Vikhr-Nemo-12B-Q4_K_M.gguf`.

## Запуск сборки

```bat
LOCAL-LLM.bat start core
LOCAL-LLM.bat start ingestion
LOCAL-LLM.bat start chat
LOCAL-LLM.bat status
LOCAL-LLM.bat verify
LOCAL-LLM.bat stop
```

`ingestion` использует embedding на GPU 1 и strict CUDA OCR на GPU 0. `chat`
останавливает OCR/worker, перезапускает embedding на GPU 0 и запускает Vikhr с
распределением `0.20,0.80`. Две GTX 1080 по 8 ГБ не считаются общей памятью.

При первом открытии UI зарегистрируйте локальную учётную запись RAGFlow через
`Sign up`; адрес вида `user@local.test` служит только логином, письмо не
отправляется. Данные учётных записей находятся в portable MySQL. Одна
распакованная сборка рассчитана на последовательную работу 40–50 пользователей,
но не на одновременный запуск из нескольких Windows-сеансов.

До запуска нужны только установленные администратором NVIDIA driver и Vulkan
runtime. Модули не устанавливают драйверы и не меняют конфигурацию Windows.

Реальные BAT/GPU/OCR тесты выполняются на целевой Windows-машине. Linux-проверка
репозитория валидирует структуру, фиксированные источники и независимость
архивов, но не выдаётся за аппаратную приёмку.
