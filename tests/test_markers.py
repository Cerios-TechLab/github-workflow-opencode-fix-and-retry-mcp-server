from gh_workflow_fix.markers import has_marker_in_branch, parse_workflow_marker

MARKER = "# self-heal: true"
OTHER = "# self-heal: false"
RAW = """
name: CI

on:
  push:
    branches: [main]

# self-heal: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""


def test_marker_found_in_raw():
    assert parse_workflow_marker(RAW, MARKER) is True


def test_marker_missing():
    assert parse_workflow_marker(RAW, OTHER) is False


def test_other_marker_ignored():
    tagged = RAW.replace("# self-heal: true", "# other: true")
    assert parse_workflow_marker(tagged, MARKER) is False


def test_empty_text():
    assert parse_workflow_marker("", MARKER) is False
    assert parse_workflow_marker("# no workflow here", MARKER) is False


def test_branch_paren_helper_always_false():
    assert has_marker_in_branch(RAW, "main") is False
