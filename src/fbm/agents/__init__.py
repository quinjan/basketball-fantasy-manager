"""Render FBM's agent-neutral sources (agents/roles, agents/skills) into each AI agent's native files.

`render.py` is pure (sources + models + MCP command -> planned files); `writer.py` applies a plan with a manifest so
installs are idempotent and never touch files FBM didn't generate.
"""

from fbm.agents.render import (
    AGENT_TARGETS,
    SETUP_SKILL_COPIES,
    Models,
    Planned,
    generic_instructions,
    mcp_command,
    render,
)
from fbm.agents.writer import MANIFEST_PATH, install, load_manifest, status_files, sync_setup_skill, uninstall

__all__ = [
    "AGENT_TARGETS",
    "MANIFEST_PATH",
    "SETUP_SKILL_COPIES",
    "Models",
    "Planned",
    "generic_instructions",
    "install",
    "load_manifest",
    "mcp_command",
    "render",
    "status_files",
    "sync_setup_skill",
    "uninstall",
]
