import os

import pytest

from scgraph.config import Config
from scgraph.llm.smoke import run_smoke
from scgraph.llm.store import Store

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("SCGRAPH_LIVE_TESTS") != "1" or not os.environ.get("MODEL_API_KEY"),
        reason="set SCGRAPH_LIVE_TESTS=1 and MODEL_API_KEY to run",
    ),
]


def test_meta_smoke_live(tmp_path):
    store = Store(tmp_path / "llm.sqlite")
    assert run_smoke(Config.from_env(), "meta", store=store) == 0
    assert store.report()[0]["calls"] >= 2
