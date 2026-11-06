"""Single NuttX behavior entry: frozen native SIM, without synthetic fallback.

A missing native capability remains an explicit scoring blocker. Source-OS
compilation, nRF52840 builds and hardware validation are separate lifecycles.
"""
from .sim.adapter import evaluate_behavior

__all__ = ['evaluate_behavior']
