"""SMC analysis package for Gold Signal Bot V3."""

from smc.orb import detect_orb
from smc.provider import SmcAdapter, SmcAnalysisResult
from smc.sfp import detect_sfp
from smc.turtle_soup import detect_turtle_soup

__all__ = [
    "SmcAdapter",
    "SmcAnalysisResult",
    "detect_orb",
    "detect_sfp",
    "detect_turtle_soup",
]
