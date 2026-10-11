import json

from triage.tools import TOOL_SPECS, run_tool


def test_unknown_tool_is_reported_to_the_model():
    assert run_tool("nope", "{}").startswith("ERROR")


def test_search_kb_returns_a_list():
    assert isinstance(json.loads(run_tool("search_kb", json.dumps({"query": "refund"}))), list)


def test_tool_specs_are_well_formed():
    for spec in TOOL_SPECS:
        assert spec["function"]["parameters"]["type"] == "object"
