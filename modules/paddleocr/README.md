# PaddleOCR module

Independent strict-GPU PaddlePaddle 3.3.1 (CUDA 11.8), PaddleOCR 3.7.0 and
PaddleX 3.7.2 API with the five unchanged PP-StructureV3 models. Health is
reported only after the GPU pipeline loads; CPU fallback remains disabled.

The ingestion profile starts OCR with `ingestion_gpu_index`, which defaults to
`auto`: prefer CUDA `gpu:1` when two GPUs are visible, otherwise use `gpu:0`.
Text recognition batches default to `8` to give the selected GPU more work per
OCR call while keeping the 8 GiB profile strict and table-aware.

Before returning OCR JSONL to RAGFlow, the gateway bounds every
`parsing_res_list[].block_content` to `max_block_tokens`, which defaults to
`900`. Oversized HTML tables are split by rows with the header repeated in each
chunk; oversized cells and plain text blocks are split further by text budget.
This prevents a single PP-StructureV3 table block from exceeding the local
embedding context even when the embedding server remains at 2048 tokens.

For measurement only, `MODULE.bat start cpu` starts the same gateway with
explicit `device: cpu`. The stack exposes this as
`LOCAL-LLM.bat start ingestion-cpu`. The normal ingestion profile remains
strict GPU and fails instead of silently falling back to CPU.

`LOCAL-LLM.bat start paddleocr` stops the other stack modules and starts this
module alone: the compatibility API listens on `127.0.0.1:9399`, while the
loopback-only document workbench listens on `127.0.0.1:9400`. The workbench
uses this module's bundled Python and PyMuPDF. It provides a server-directory
picker, recursive document tree, page-rendered PDF preview, synchronized
per-page Markdown editing, OCR option controls, sidecar Markdown saves and
Markdown attachment embedding into a PDF copy or the explicitly confirmed
original.

Direct module commands are:

```bat
MODULE.bat start auto
MODULE.bat workbench auto
MODULE.bat stop
```

The first command starts only the API. The second ensures that the API is
ready, starts the workbench and opens it in the default browser. See the root
README for the complete HTTP contract and `optionalPayload` fields.
