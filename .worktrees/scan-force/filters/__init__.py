"""Signal filter package for Gold Signal Bot V3."""

from filters.chop import evaluate_chop
from filters.spread import evaluate_spread

__all__ = ["evaluate_chop", "evaluate_spread"]
