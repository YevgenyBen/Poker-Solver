"""Pydantic response models for the solver API."""

from pydantic import BaseModel


class SolveResponse(BaseModel):
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    opening_range: dict[str, dict[str, float]]
    position: str
    positions: list[str]


class EquityResponse(BaseModel):
    hand_a: str
    hand_b: str
    board: str
    equity_a: float
    equity_b: float


class FlopSolveResponse(BaseModel):
    board: str
    pot: float
    stack_bb: float
    iterations: int
    elapsed_seconds: float
    strategy: dict[str, dict[str, float]]
    position: str
    positions: list[str]
