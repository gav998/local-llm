# Зафиксированный перечень online-входов

Ниже перенесён полный перечень из прежнего монолитного сборщика. Имена,
версии и URL не обновлялись при разделении. Исходные установочные архивы
хранятся в общем корневом `_src`; одинаковые входы нескольких модулей не
дублируются. Wheelhouse, online-cache, npm/pip/uv cache и логи сборки остаются
в `modules/<name>/_src`, поэтому уже скачанные данные не нужно переносить или
загружать повторно.

| Файл / версия | Теперь принадлежит модулю |
|---|---|
| `7zr.exe`, 7-Zip 26.02 | общий bootstrap сборки |
| `7z2602-extra.7z` | общая упаковка |
| `uv-x86_64-pc-windows-msvc.zip`, uv 0.12.9 | ragflow, paddleocr |
| CPython 3.13.15, standalone release 20260901 | ragflow |
| CPython 3.11.16, standalone release 20260901 | paddleocr |
| Node.js 24.20.0 | web (production build) |
| MinGit package 2.55.0.5 / Git 2.55.0.windows.5 | ragflow |
| Eclipse Temurin JRE 21.0.8+9 Windows x64 | ragflow |
| RAGFlow source 0.27.1 | ragflow, web |
| `datrie` 0.8.3 cp313 wheel | ragflow |
| PaddlePaddle GPU 3.3.1 cp311, CUDA 11.8 / sm_61 | paddleocr |
| `PP-DocLayout-L_infer.tar` | paddleocr |
| `PP-DocBlockLayout_infer.tar` | paddleocr |
| `PP-OCRv6_medium_det_infer.tar` | paddleocr |
| `eslav_PP-OCRv5_mobile_rec_infer.tar` | paddleocr |
| `SLANet_plus_infer.tar` | paddleocr |
| `PP-LCNet_x1_0_doc_ori_infer.tar` | paddleocr |
| `UVDoc_infer.tar` | paddleocr |
| `PP-LCNet_x1_0_textline_ori_infer.tar` | paddleocr |
| `PP-OCRv4_server_seal_det_infer.tar` | paddleocr |
| `PP-FormulaNet_plus-S_infer.tar` | paddleocr |
| `PP-Chart2Table_infer.tar` | paddleocr |
| DejaVu Sans 2.37 | paddleocr |
| Microsoft VC runtime 14.44.35211 (app-local DLL extraction) | ragflow, paddleocr |
| MySQL 8.0.40 | mysql |
| Elasticsearch 8.11.3 | elasticsearch |
| Silo release `2026-08-06T00-00-00Z` | silo |
| Valkey Windows 8.1.6 | valkey |
| Caddy 2.11.4 | web |
| llama.cpp b10786 Vulkan x64 | llama-cpp |

Точные URL находятся рядом с владельцем в `module.json`; при отсутствии файла
модульный `PREPARE-ONLINE.bat` печатает этот URL и точный путь в общем `_src`.

## RAGFlow Python compatibility pins

Сохранены прежние CPython 3.13 отклонения: NumPy 2.3.5, XGBoost 2.1.4,
scikit-learn 1.8.0, datrie 0.8.3 и graspologic-native 1.2.5. Сохранились
version-pinned `ragflow-windows-{additions,overrides,excludes}.txt`, подготовка
wheelhouse lock, audited GraphRAG adapter и metadata/RECORD patches.

RAGFlow assets также не обновлялись:

- `InfiniFlow/deepdoc` revision `de0e793dc6d744406c96dabd688ccc969f41b443`;
- `InfiniFlow/text_concat_xgb_v1.0` revision `722ed09a54f23f14fe0279ce6b74ce18e1960f54`;
- NLTK data revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a`;
- `cl100k_base.tiktoken`;
- Eclipse Temurin JRE 21.0.8+9 Windows x64;
- Apache Tika server 3.3.0.

Для этих файлов намеренно не применяются content hash/size проверки: локально
изменённые рабочие assets принимаются подготовщиком без повторного чтения целого файла.

## Внешние данные

GGUF по-прежнему не являются build-входами и переносятся отдельно:
`Qwen3-Embedding-8B-Q4_K_M.gguf` и переименованный
`Vikhr-Nemo-12B-Q4_K_M.gguf`.
