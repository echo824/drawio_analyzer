"""core.inspector 检视能力测试（库 / CLI / HTTP 三个消费面）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import create_app
from core.inspector import inspect_file, main

TEMPLATE_ID = "oil_fracturing_v1"

MINI = """<mxfile><diagram><mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="v1" value="P001 层数 &amp;gt; 3" vertex="1" parent="1"/>
<mxCell id="v2" value="P001 重复码节点" vertex="1" parent="1"/>
<mxCell id="v3" value="普通说明顶点" vertex="1" parent="1"/>
<mxCell id="e1" edge="1" source="v1" target="vX" parent="1"/>
<mxCell id="e2" edge="1" source="v1" parent="1"/>
</root></mxGraphModel></diagram></mxfile>"""


@pytest.fixture()
def flow(templates_root: Path) -> Path:  # noqa: ANN001
    return templates_root / TEMPLATE_ID / "flow.drawio"


@pytest.fixture()
def client(templates_root: Path):  # noqa: ANN001, ANN201
    app = create_app(templates_root)  # 注册表仅为服务启动依赖；inspect 端点自身不消费它
    app.config["TESTING"] = True
    return app.test_client()


# ── 库层 ─────────────────────────────────────────────────
def test_inspect_real_template_ok(flow: Path) -> None:
    r = inspect_file(flow)
    assert r.ok and r.parse_error is None
    assert (r.node_count, r.edge_count, r.business_count) == (39, 44, 39)
    assert {k: len(v) for k, v in r.codes_by_kind.items()} == {"P": 14, "R": 14, "C": 11}
    assert r.duplicate_codes == []
    assert len(r.dangling_edges) == 5          # 与 v1 已知的 5 条悬空边一致（仅告警级信息）
    assert r.lowercase_codes == ["P003"]       # 图内 p003 小写码被识别并提示


def test_inspect_issues_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.drawio"
    bad.write_text(MINI, encoding="utf-8")
    r = inspect_file(bad)
    assert not r.ok
    assert r.duplicate_codes == ["P001"]
    assert r.dangling_edges == ["e1", "e2"]    # 指向不存在顶点 / 缺 target
    assert r.unclassified == ["普通说明顶点"]
    assert "重复业务码" in r.summary()


def test_inspect_parse_failure_no_raise(tmp_path: Path) -> None:
    broken = tmp_path / "broken.drawio"
    broken.write_text("<html>not drawio", encoding="utf-8")
    r = inspect_file(broken)
    assert not r.ok and r.parse_error
    assert r.summary().startswith("✗")


# ── CLI ─────────────────────────────────────────────────
def test_cli_main_success(flow: Path, capsys: pytest.CaptureFixture) -> None:
    assert main([str(flow)]) == 0
    assert "解析成功" in capsys.readouterr().out


def test_cli_main_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    bad = tmp_path / "bad.drawio"
    bad.write_text(MINI, encoding="utf-8")
    assert main([str(bad), "--json"]) == 1     # 重复码 → 非零退出
    payload = json.loads(capsys.readouterr().out)
    assert payload["duplicate_codes"] == ["P001"]


# ── HTTP ────────────────────────────────────────────────
def test_http_inspect_ok(client, flow: Path) -> None:  # noqa: ANN001
    resp = client.post("/api/v1/templates/inspect", json={"path": str(flow)})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] and body["business_count"] == 39


def test_http_inspect_rejects(client, tmp_path: Path) -> None:  # noqa: ANN001
    assert client.post("/api/v1/templates/inspect", json={}).status_code == 400
    assert client.post("/api/v1/templates/inspect",
                       json={"path": "a.txt"}).status_code == 400
    assert client.post("/api/v1/templates/inspect",
                       json={"path": str(tmp_path / "nope.drawio")}).status_code == 404


def test_http_inspect_parse_error_422(client, tmp_path: Path) -> None:  # noqa: ANN001
    broken = tmp_path / "broken.drawio"
    broken.write_text("<mxfile><diagram>ZmxhdA==</diagram></mxfile>", encoding="utf-8")
    resp = client.post("/api/v1/templates/inspect", json={"path": str(broken)})
    assert resp.status_code == 422
    assert resp.get_json()["parse_error"]
