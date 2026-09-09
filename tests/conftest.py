import sys
from pathlib import Path

from dify_plugin import DifyPluginEnv
import pytest


PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))


@pytest.fixture
def plugin_environment():
    return DifyPluginEnv()


@pytest.fixture
def plugin_directory(monkeypatch):
    monkeypatch.chdir(PLUGIN_DIR)
    return PLUGIN_DIR
