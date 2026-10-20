"""Release metadata must agree, or PyPI and the MCP registry reject the release."""
import json
import os
import re

import skimread

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_versions_agree():
    pyproject = re.search(r'^version = "([^"]+)"$', _read("pyproject.toml"), re.M).group(1)
    server = json.loads(_read("server.json"))
    assert pyproject == skimread.__version__ == server["version"]
    assert all(p["version"] == pyproject for p in server["packages"])


def test_readme_carries_mcp_registry_ownership_marker():
    server = json.loads(_read("server.json"))
    assert f"mcp-name: {server['name']} " in _read("README.md")
    assert len(server["description"]) <= 100
