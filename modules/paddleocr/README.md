# PaddleOCR module

Independent strict-GPU PaddlePaddle 3.3.1 (CUDA 11.8), PaddleOCR 3.7.0 and
PaddleX 3.7.2 API with the five unchanged PP-StructureV3 models. Health is
reported only after the GPU pipeline loads; CPU fallback remains disabled.

The ingestion profile starts OCR with `ingestion_gpu_index`, which defaults to
`auto`: prefer CUDA `gpu:1` when two GPUs are visible, otherwise use `gpu:0`.
Text recognition batches default to `8` to give the selected GPU more work per
OCR call while keeping the 8 GiB profile strict and table-aware.

For measurement only, `MODULE.bat start cpu` starts the same gateway with
explicit `device: cpu`. The stack exposes this as
`LOCAL-LLM.bat start ingestion-cpu`. The normal ingestion profile remains
strict GPU and fails instead of silently falling back to CPU.
