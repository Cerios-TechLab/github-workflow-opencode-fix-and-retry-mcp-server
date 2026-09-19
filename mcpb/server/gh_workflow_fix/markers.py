"""Detection of the self-heal marker in workflow YAML."""
from __future__ import annotations


def parse_workflow_marker(yaml_text: str, marker: str) -> bool:
    """True if the given marker comment appears in a workflow definition."""
    needle = marker.strip()
    return any(line.strip() == needle for line in yaml_text.splitlines())


def has_marker_in_branch(yaml_text: str, branch: str) -> bool:
    """Reserved: marker + branch pairs for specific triggers (P2)."""
    return False
