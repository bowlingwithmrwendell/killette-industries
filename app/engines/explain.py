"""Explainability: every score the service returns carries the formula that
produced it, the exact inputs, and a plain-English line of reasoning.

    {"formula": "...", "inputs": {...}, "reasoning": "..."}
"""
from __future__ import annotations


def explain(formula: str, inputs: dict, reasoning: str) -> dict:
    return {"formula": formula, "inputs": dict(inputs), "reasoning": reasoning}
