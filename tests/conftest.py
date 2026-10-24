import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from word_guardian.config import DEFAULTS, validate
from word_guardian.store import Store


@pytest.fixture
def cfg():
    return validate(
        dict(
            deepcopy(DEFAULTS),
            enabled=True,
            start_date="2026-10-24",
            platform_id="qq-family",
            bot_id="bot-1",
            group_id="group-openid",
            user_ids=["sister-openid", "u_alias"],
            session="qq-family:GroupMessage:group-openid",
        )
    )


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "ledger.sqlite3")
    yield db
    db.db.close()


@pytest.fixture(scope="session")
def plugin_class():
    # Load the actual AstrBot entry point with package-relative imports intact.
    package = types.ModuleType("guardian_test_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location("guardian_test_plugin.main", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.Main
