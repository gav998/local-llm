"""Deterministic GOST OCR to GSM tables pipeline."""

from .pipeline import GostGsmPipeline, PipelineError

__all__ = ["GostGsmPipeline", "PipelineError"]
