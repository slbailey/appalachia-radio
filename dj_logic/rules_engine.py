"""
Rules engine for DJ segment selection.

Phase 7: Thin decision layer that wraps CadenceManager.
Future phases can extend this with time-of-day rules, genre rules, etc.
"""

import logging

logger = logging.getLogger(__name__)


class RulesEngine:
    """
    Thin decision layer for DJ segment selection.
    
    Currently wraps CadenceManager. This is the place to add:
    - Time-of-day rules
    - Genre-based rules
    - Song attribute rules
    - etc.
    """
    
    def __init__(self, cadence: 'CadenceManager') -> None:
        """
        Initialize the rules engine.
        
        Args:
            cadence: CadenceManager instance to use for spacing and probability
        """
        self._cadence = cadence
    
    def can_consider_speaking(self) -> bool:
        """
        Check if DJ can consider speaking (cadence allows it).
        
        Returns:
            True if minimum spacing requirement is met, False otherwise
        """
        return self._cadence.can_play_segment()
    
    def intro_probability(self) -> float:
        """
        Get probability for playing an intro.
        
        Returns:
            Probability (0.0 to 1.0) based on cadence
        """
        return self._cadence.speaking_probability()
    
    def outro_probability(self) -> float:
        """
        Get probability for playing an outro.
        
        Returns:
            Probability (0.0 to 1.0) based on cadence
        """
        return self._cadence.speaking_probability()
