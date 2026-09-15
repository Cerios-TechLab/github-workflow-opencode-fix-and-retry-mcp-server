"""Detectie van de self-heal marker in workflow-YAML."""
from __future__ import annotations


def parse_workflow_marker(yaml_text: str, marker: str) -> bool:
    """True als de opgegeven marker-comment in een werkstroomdefinitie staat."""
    needle = marker.strip()
    return any(line.strip() == needle for line in yaml_text.splitlines())


def has_marker_in_branch(yaml_text: str, branch: str) -> bool:
    """Reservering: paren van marker + branch bij specifieke triggers (P2)."""
    return False
