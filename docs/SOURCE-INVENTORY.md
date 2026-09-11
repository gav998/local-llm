# Зафиксированный перечень online-входов

Ниже перенесён полный перечень из прежнего монолитного сборщика. Имена,
версии и URL не обновлялись при разделении. Общие build-only инструменты
дублируются в `_src` нужного модуля, чтобы модуль можно было собрать отдельно.

| Файл / версия | Теперь принадлежит модулю |
|---|---|
| `7zr.exe`, 7-Zip 26.02 | каждый модуль, только bootstrap сборки |
| `7z2602-extra.7z` | каждый модуль, только упаковка |
| `uv-x86_64-pc-windows-msvc.zip`, uv 0.12.9 | ragflow, paddleocr |
| CPython 3.13.15, standalone release 20260901 | ragflow |
| CPython 3.11.16, standalone release 20260901 | paddleocr |
| Node.js 24.20.0 | web (production build) |
| MinGit package 2.55.0.5 / Git 2.55.0.windows.5 | ragflow |
| RAGFlow source 0.27.1 | ragflow, web |
| `datrie` 0.8.3 cp313 wheel | ragflow |
| PaddlePaddle GPU 3.3.1 cp311, CUDA 11.8 / sm_61 | paddleocr |
| `PP-DocLayout-L_infer.tar` | paddleocr |
| `PP-DocBlockLayout_infer.tar` | paddleocr |
| `PP-OCRv6_medium_det_infer.tar` | paddleocr |
| `eslav_PP-OCRv5_mobile_rec_infer.tar` | paddleocr |
| `SLANet_plus_infer.tar` | paddleocr |
| DejaVu Sans 2.37 | paddleocr |
| Microsoft VC runtime 14.44.35211 (app-local DLL extraction) | ragflow, paddleocr |
| MySQL 8.0.40 | mysql |
| Elasticsearch 8.11.3 | elasticsearch |
| Silo release `2026-08-06T00-00-00Z` | silo |
| Valkey Windows 8.1.6 | valkey |
| Caddy 2.11.4 | web |
| llama.cpp b10786 Vulkan x64 | llama-cpp |

Точные URL находятся рядом с владельцем в `module.json`; при отсутствии файла
`PREPARE-ONLINE.bat` печатает этот URL и точный `_src`-путь.

## RAGFlow Python compatibility pins

Сохранены прежние CPython 3.13 отклонения: NumPy 2.3.5, XGBoost 2.1.4,
scikit-learn 1.8.0, datrie 0.8.3 и graspologic-native 1.2.5. Сохранились
hash-locked `ragflow-windows-{additions,overrides,excludes}.txt`, подготовка
wheelhouse lock, audited GraphRAG adapter и metadata/RECORD patches.

RAGFlow assets также не обновлялись:

- `InfiniFlow/deepdoc` revision `de0e793dc6d744406c96dabd688ccc969f41b443`;
- `InfiniFlow/text_concat_xgb_v1.0` revision `722ed09a54f23f14fe0279ce6b74ce18e1960f54`;
- NLTK data revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a`;
- `cl100k_base.tiktoken` SHA-256 `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`;
- Apache Tika server 3.3.0 SHA-256 `2aca63d25f84774d759de6e132ae7f5723e3ee2adf1d51f585658baba1335e9b`.

## Внешние данные

GGUF по-прежнему не являются build-входами и переносятся отдельно:
`Qwen3-Embedding-8B-Q4_K_M.gguf` и переименованный
`Vikhr-Nemo-12B-Q4_K_M.gguf`.
