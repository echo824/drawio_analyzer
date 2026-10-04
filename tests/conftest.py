"""pytest 公共夹具。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_ROOT = REPO_ROOT / "templates"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="重新生成 tests/golden 下的快照基准（确认后入库）",
    )


@pytest.fixture(scope="session")
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.fixture(scope="session")
def templates_root() -> Path:
    assert TEMPLATES_ROOT.is_dir(), f"模板目录缺失: {TEMPLATES_ROOT}"
    return TEMPLATES_ROOT


@pytest.fixture()
def client():  # noqa: ANN201
    app = create_app(TEMPLATES_ROOT)
    app.config["TESTING"] = True
    return app.test_client()
