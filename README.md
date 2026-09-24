# local_llm

Три независимые portable-папки для Windows 11 x64. Ничего не устанавливается
в систему, не нужны права администратора, системный Python, Docker или PATH.
После первого запуска можно перенести весь каталог на другой диск или компьютер.

## llama

1. Положите GGUF-модели в `llama\models\llm` и `llama\models\embed`.
2. Запустите `llama\start.bat`.
3. Если `llama\llama-cpp\llama-server.exe` отсутствует, скрипт найдёт архив
   `llama\_src\llama-b10786-bin-win-vulkan-x64.zip` либо предложит скачать его.
4. Выберите в меню модель для запуска или остановки. Текущий статус всегда виден
   в верхней части меню.

OpenAI-совместимые API по умолчанию:

- chat: `http://127.0.0.1:6381/v1`, ключ `local-llm`;
- embeddings: `http://127.0.0.1:6380/v1`, ключ `local-llm`.

Порты, ключ и GPU-настройки находятся в `llama\config.json`. При выходе из меню
все запущенные этим окном серверы останавливаются.

## paddleocr

Запустите `paddleocr\start.bat`. На первом запуске скрипт сам скачает embedded
Python, CUDA 11.8 wheel, зависимости и модели PP-StructureV3. Всё будет сохранено
внутри `paddleocr`; установочные архивы после успешной установки удаляются.

API слушает `http://127.0.0.1:9399`, ключ по умолчанию `local-ocr`:

```bat
curl.exe -H "Authorization: Bearer local-ocr" -F "file=@scan.png" http://127.0.0.1:9399/v1/ocr
```

Ответ: JSON с общим полем `text` и массивом `pages`. Асинхронный API
`/api/v2/ocr/jobs` также сохранён для digitizer. По умолчанию OCR выбирает GPU 1,
если видны две видеокарты, иначе GPU 0. Параметры находятся в
`paddleocr\config.json`; временно переопределить их можно так:

```bat
paddleocr\start.bat -Port 9399 -ApiKey local-ocr -Device auto
```

## digitizer

Сначала запустите PaddleOCR, при необходимости — chat-модель llama.cpp, затем
`digitizer\start.bat`. Embedded Python и зависимости установятся автоматически,
после чего откроется `http://127.0.0.1:9400`.

Без аргументов URL и ключи читаются из соседних папок `paddleocr` и `llama`.
Если папки находятся отдельно, укажите значения явно:

```bat
digitizer\start.bat -PaddleUrl http://127.0.0.1:9399 -PaddleKey local-ocr -LlamaUrl http://127.0.0.1:6381/v1 -LlamaKey local-llm
```

Llama используется только для выбранных пользователем операций исправления OCR;
PaddleOCR должен быть запущен до старта digitizer. Данные остаются в
`digitizer\data` и рядом с открытыми PDF.

Закрытие соответствующего `start.bat` или нажатие Enter в нём останавливает
запущенный из этого окна процесс.
