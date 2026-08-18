"""Pydantic response models for the solver API."""

from pydantic import BaseModel


class SolveResponse(BaseModel):
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    opening_range: dict[str, dict[str, float]]
    position: str
    positions: list[str]
