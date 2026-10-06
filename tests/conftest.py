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


# 合成一张"描述含阈值占位符(x12/x13/x20/x3/x4)"的最小明文流程图。
# 生产模板 2026-10 已全面改为数字直写（无占位符），§6.6 替换机制的**集成覆盖**
# 遂与具体模板解耦，改以此夹具为输入：既锁定"符号→数值/括号保护/仅 P 参与/只读往返"
# 的机制行为，又不牵连生产模板的实际文本。value 内 &amp;gt; 经 XML 解析降一层为 &gt;，
# 与 substitute_label 纯函数入参风格一致。
_SYMBOLS_FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net">
  <diagram id="sym" name="symbols">
    <mxGraphModel dx="800" dy="600" grid="1" gridSize="10" arrows="1" fold="1" page="1">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <mxCell id="v_p006" value="P006 本井初期与目前日产油之差 &amp;gt; x12 t" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="40" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="v_p005" value="P005 本井含水 &amp;lt; x13% 或 &amp;gt; （全区平均值或井网平均值-Y）分段给Y值。" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="120" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="v_p013" value="P013 与本井连通水井（井距&amp;lt;350米）开井数 &amp;gt; x20 口" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="200" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="v_p001" value="P001 可压储层层数 &amp;gt; x3个 且 折算厚度（(砂岩-有效)/3+有效） &amp;gt; x4m" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="280" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="v_r06" value="R06 全部满足可压性条件" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="360" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="v_c001" value="C001 油井具备可压性 x13" style="rounded=0;whiteSpace=wrap;html=1;fontSize=12;strokeColor=#000000;fillColor=#FFFFFF;" vertex="1" parent="1"><mxGeometry x="40" y="440" width="240" height="40" as="geometry" /></mxCell>
        <mxCell id="e1" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#000000;" edge="1" parent="1" source="v_p006" target="v_r06"><mxGeometry relative="1" as="geometry" /></mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


@pytest.fixture()
def symbol_flow_path(tmp_path: Path) -> Path:
    """落盘合成符号流程图，返回路径（测试自行用 cfg.node_code_pattern 解析）。"""
    p = tmp_path / "symbols_flow.drawio"
    p.write_text(_SYMBOLS_FLOW_XML, encoding="utf-8")
    return p
