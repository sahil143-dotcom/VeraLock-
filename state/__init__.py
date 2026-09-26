"""Commitment lifecycle. Brain owns status transitions; Vault persists them."""

from state.commitment_machine import CommitmentMachine, IllegalTransitionError

__all__ = ["CommitmentMachine", "IllegalTransitionError"]
