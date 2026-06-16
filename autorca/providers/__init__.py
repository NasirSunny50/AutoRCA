"""Pluggable AI analysis providers."""
from __future__ import annotations

from ..config import Config
from .base import AnalysisProvider, AnalysisResult
from .gemini_provider import GeminiProvider
from .heuristic_provider import HeuristicProvider


def build_provider(config: Config) -> AnalysisProvider:
    """Factory: return the provider named in config, defaulting to heuristic."""
    if config.provider == "gemini":
        return GeminiProvider(config)
    return HeuristicProvider(config)


__all__ = [
    "AnalysisProvider",
    "AnalysisResult",
    "GeminiProvider",
    "HeuristicProvider",
    "build_provider",
]
