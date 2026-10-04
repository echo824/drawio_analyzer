"""ISSUE-1.1 Draw.io 解析器单测（需求 §3、§10.3）。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from config_loader import load_templates
from core.drawio_parser import (
    DrawioParseError,
    DrawioParser,
    extract_business_code,
    parse_style,
)

DEFAULT_PATTERN = r"^\s*([PRC])(\d+(?:-\d+)?)\b"

DANGLING_IDS = {
    "ukcuZfHQnJ5xVRSnUBC_-74",
    "ukcuZfHQnJ5xVRSnUBC_-86",
    "ukcuZfHQnJ5xVRSnUBC_-75",
    "ukcuZfHQnJ5xVRSnUBC_-77",
    "ukcuZfHQnJ5xVRSnUBC_-91",
}


@pytest.fixture()
def flow_path(templates_root: Path) -> Path:
    return templates_root / "oil_fracturing_v1" / "flow.drawio"


@pytest.fixture()
def parser(templates_root: Path) -> DrawioParser:
    cfg = load_templates(templates_root)["oil_fracturing_v1"]
    return DrawioParser(cfg.node_code_pattern)


@pytest.fixture()
def model(parser: DrawioParser, flow_path: Path):  # noqa: ANN201
    return parser.parse(flow_path)


# ── 真实模板结构 ──────────────────────────────────────────
def test_counts_match_template(model) -> None:  # noqa: ANN001
    assert len(model.nodes) == 37   # 顶点数
    assert len(model.edges) == 42   # 连线数


def test_code_map_contains_key_business_codes(model) -> None:  # noqa: ANN001
    for code in ("C001", "R01", "P001", "R02-1", "P003"):
        assert code in model.code_map


def test_kind_assignment(model) -> None:  # noqa: ANN001
    kinds = {n.code: n.kind for n in model.nodes if n.code}
    assert kinds["C001"] == "C"
    assert kinds["R01"] == "R"
    assert kinds["P001"] == "P"
    assert kinds["R02-1"] == "R"


def test_lowercase_code_normalized_to_upper(model) -> None:  # noqa: ANN001
    # p003 → P003；不应存在小写键
    assert "P003" in model.code_map
    assert all(code == code.upper() for code in model.code_map)


def test_geometry_parsed_for_root_node(model) -> None:  # noqa: ANN001
    node = next(n for n in model.nodes if n.code == "C001")
    assert node.geometry == (1230, 315, 180, 60)


def test_rich_text_and_entities_preserved(model) -> None:  # noqa: ANN001
    p011 = next(n for n in model.nodes if n.code == "P011")
    assert "<div>" in p011.label          # &lt;div&gt; 已解码为字面量
    p016 = next(n for n in model.nodes if n.code == "P016")
    assert "套管" in p016.label


def test_dangling_edges_preserved(model) -> None:  # noqa: ANN001
    dangling = {e.cell_id for e in model.dangling_edges()}
    assert DANGLING_IDS <= dangling
    assert len(model.dangling_edges()) >= 5


def test_no_duplicate_codes_in_real_template(model) -> None:  # noqa: ANN001
    assert model.duplicate_codes == set()


# ── extract_business_code 单元 ────────────────────────────
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("C001 油井具备可压性", "C001"),
        ("R02-1 潜力分析", "R02-1"),
        ("p003 周围油井", "P003"),          # 大小写归一
        ("  P006 本井... ", "P006"),          # 去除首尾空白
        ("说明性文字，无编号", None),
        ("P011 流压<div>…</div>", "P011"),
        ("", None),
        (None, None),
    ],
)
def test_extract_business_code(value: str | None, expected: str | None) -> None:
    assert extract_business_code(value, re.compile(DEFAULT_PATTERN, re.IGNORECASE)) == expected


# ── parse_style 单元 ──────────────────────────────────────
def test_parse_style() -> None:
    style = "ellipse;whiteSpace=wrap;html=1;fillColor=#FFCCFF;strokeColor=#000000;"
    parsed = parse_style(style)
    assert parsed["whiteSpace"] == "wrap"
    assert parsed["fillColor"] == "#FFCCFF"
    assert parsed["ellipse"] == ""          # 无 '=' 的裸键，值为空串


# ── 安全 / 压缩 负样例（V30 / V31）─────────────────────────
def test_xxe_rejected(tmp_path: Path) -> None:
    evil = tmp_path / "evil.drawio"
    evil.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE mxfile [<!ENTITY xxe "boom">]>\n'
        "<mxfile><diagram><mxGraphModel><root>&xxe;</root>"
        "</mxGraphModel></diagram></mxfile>",
        encoding="utf-8",
    )
    with pytest.raises(DrawioParseError):
        DrawioParser(DEFAULT_PATTERN).parse(evil)


def test_compressed_detected(tmp_path: Path) -> None:
    compressed = tmp_path / "z.drawio"
    compressed.write_text(
        '<mxfile><diagram id="p">YWJjZGVmZ2dpamts</diagram></mxfile>',
        encoding="utf-8",
    )
    with pytest.raises(DrawioParseError, match="V31"):
        DrawioParser(DEFAULT_PATTERN).parse(compressed)
