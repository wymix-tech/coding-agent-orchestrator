#!/usr/bin/env python3
"""Provider SPI for semantic code-impact intelligence.

V6 deliberately keeps code-graph infrastructure behind this contract. Providers emit
structural evidence; they never select flow profiles or quality-gate outcomes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class ProviderError(RuntimeError):
    pass


class CodeIntelligenceProvider(ABC):
    provider_id: str

    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def collect_impact(
        self,
        repo: Path,
        *,
        scope: str = "all",
        base_branch: Optional[str] = None,
        depth: int = 3,
        refresh_index: bool = True,
    ) -> Dict[str, Any]:
        """Return the canonical V6 semantic-impact document."""
        raise NotImplementedError
