"""Auto-failover provider: try several engines in order until one succeeds.

Default order (config ``ai.fallback_chain``): Gemini -> Groq -> Local (Ollama)
-> Heuristic.

If Gemini is out of quota / unreachable, the next AI engine (the local model)
is tried before finally dropping to the offline heuristic engine — so you keep
AI-quality analysis for as long as any AI backend is available, instead of
falling straight to rule-based output.

Each AI sub-provider is built with its own heuristic fallback DISABLED, so this
chain (not the sub-provider) controls the order. Heuristic never fails, so it is
always the terminal step.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from ..config import Config
from ..log_parser import ErrorDigest
from .base import AnalysisProvider, AnalysisResult
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .heuristic_provider import HeuristicProvider
from .local_provider import LocalProvider

log = logging.getLogger("autorca.chain")


class ChainProvider(AnalysisProvider):
    name = "chain"

    def __init__(self, config: Config):
        self.config = config
        # Sub-providers must raise (not self-fallback) so the chain moves on.
        no_fallback = replace(config, fallback_to_heuristic=False)
        builders = {
            "gemini": lambda: GeminiProvider(no_fallback),
            "groq": lambda: GroqProvider(no_fallback),
            "local": lambda: LocalProvider(no_fallback),
            "heuristic": lambda: HeuristicProvider(config),
        }
        self.steps: list = []
        for step in config.fallback_chain:
            build = builders.get(str(step).lower())
            if build:
                self.steps.append((str(step).lower(), build()))
        # Heuristic is the guaranteed last resort.
        if not any(n == "heuristic" for n, _ in self.steps):
            self.steps.append(("heuristic", HeuristicProvider(config)))

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:
        failed: list = []
        for name, provider in self.steps:
            try:
                result = provider.analyze(digest, file_name)
                if failed:
                    result.engine = f"{result.engine or name} (failover from {' → '.join(failed)})"
                return result
            except Exception as exc:  # broad: quota, network, model-missing, parse
                log.warning("Chain: '%s' engine failed (%s); trying next.", name, str(exc)[:120])
                failed.append(name)
        # Unreachable (heuristic never raises), but stay safe.
        raise RuntimeError("All engines in the fallback chain failed: " + ", ".join(failed))
