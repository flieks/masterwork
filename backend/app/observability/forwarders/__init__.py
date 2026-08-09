"""Standalone hook scripts, one per agent, copied onto disk at connect time.

Nothing here is imported by the app — these files run under a bare `python3` in
whatever process the coding agent spawns, so they must stay stdlib-only.
"""
