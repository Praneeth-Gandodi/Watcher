"""Simulation tick/runtime boundary.

The composition root is integration-controlled. Agent 2 owns world, movement,
and safety updates exposed through this runtime but must coordinate changes to
the composition boundary through an integration PR.
"""
