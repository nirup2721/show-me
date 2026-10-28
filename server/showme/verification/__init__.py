"""Verification lives here, apart from the tools and the agent. One check per file.

A check is not a tool the agent may choose to call; it runs inside the tool the agent must call to move on
(advance_step), so neither the model nor a bug in the loop can skip it.
"""
from .verify_step import verify_step  # noqa: F401
