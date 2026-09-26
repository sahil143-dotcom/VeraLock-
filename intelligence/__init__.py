"""VeraLock Brain: text-turn commitment intelligence.

Owns reasoning only. Persistence lives in Vault (`storage/`, `evidence/`, `followup/`).
Imports commitment types from `shared/commitment_schema.py` and does not redefine them.
"""

from intelligence.pipeline import BrainPipeline, TurnInput, TurnResult

__all__ = ["BrainPipeline", "TurnInput", "TurnResult"]
