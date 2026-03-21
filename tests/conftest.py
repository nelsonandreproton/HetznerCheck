"""Shared fixtures: inject a stub 'docker' module so tests run without the SDK installed."""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def stub_docker_module():
    """Inject a minimal stub into sys.modules so `import docker` inside collect_container_disk works."""
    if "docker" not in sys.modules:
        stub = ModuleType("docker")
        stub.from_env = MagicMock()
        sys.modules["docker"] = stub
        yield stub
        del sys.modules["docker"]
    else:
        yield sys.modules["docker"]
