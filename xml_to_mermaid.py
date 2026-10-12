#!/usr/bin/env python3
"""Convert diagrams.net/draw.io XML flowcharts to Mermaid flowcharts.

The converter intentionally uses only Python's standard library.  It supports
plain and compressed draw.io diagrams, common flowchart shapes, edge labels,
and incomplete connectors whose missing endpoint can be inferred from the
geometry stored by draw.io.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import re
import sys
import textwrap
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


VALID_DIRECTIONS = ("auto", "TD", "TB", "BT", "LR", "RL")
PAGE_LAYOUTS = ("adaptive", "portrait", "landscape", "flowchart")
MERMAID_CURVES = (
    "rounded",
    "basis",
    "bumpX",
    "bumpY",
    "cardinal",
    "catmullRom",
    "linear",
    "monotoneX",
    "monotoneY",
    "natural",
    "step",
    "stepAfter",
    "stepBefore",
)


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    def distance_to(self, point: tuple[float, float]) -> float:
        """Euclidean distance from *point* to this rectangle (zero inside)."""
        px, py = point
        dx = max(self.x - px, 0.0, px - (self.x + self.width))
        dy = max(self.y - py, 0.0, py - (self.y + self.height))
        return math.hypot(dx, dy)


@dataclass
class Node:
    cell_id: str
    label: str
    style: dict[str, str]
    box: Box
    is_text: bool = False

    @property
    def mermaid_id(self) -> str:
        return "n" + re.sub(r"[^A-Za-z0-9_]", "_", self.cell_id)


@dataclass
class Edge:
    cell_id: str
    source: str | None
    target: str | None
    label: str
    style: dict[str, str]
    points: list[tuple[float, float]] = field(default_factory=list)
    source_point: tuple[float, float] | None = None
    target_point: tuple[float, float] | None = None


@dataclass
class Diagram:
    name: str
    nodes: list[Node]
    edges: list[Edge]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class A4FlowPlan:
    orientation: str
    direction: str
    label_chars: int
    font_size: int
    node_spacing: int
    rank_spacing: int
    layer_bound: int


class ConversionError(ValueError):
    """Raised when input cannot be interpreted as draw.io XML."""


def _number(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _style_map(raw_style: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (raw_style or "").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
        else:
            result[item] = "1"
    return result


def _clean_label(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:div|p|li)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _decode_compressed_diagram(payload: str) -> ET.Element:
    try:
        compressed = base64.b64decode(payload.strip())
        xml_text = urllib.parse.unquote(zlib.decompress(compressed, -15).decode("utf-8"))
        return ET.fromstring(xml_text)
    except (ValueError, UnicodeError, zlib.error, ET.ParseError) as exc:
        raise ConversionError(f"Không giải nén được diagram draw.io: {exc}") from exc


def _diagram_roots(root: ET.Element) -> list[tuple[str, ET.Element]]:
    if root.tag == "mxGraphModel":
        return [("Diagram", root)]
    if root.tag != "mxfile":
        graph = root.find(".//mxGraphModel")
        if graph is None:
            raise ConversionError("Không tìm thấy mxGraphModel trong file XML.")
        return [(root.get("name", "Diagram"), graph)]

    results: list[tuple[str, ET.Element]] = []
    for index, item in enumerate(root.findall("./diagram"), 1):
        name = item.get("name") or f"Diagram {index}"
        graph = item.find("./mxGraphModel")
        if graph is None and (item.text or "").strip():
            graph = _decode_compressed_diagram(item.text or "")
        if graph is not None:
            results.append((name, graph))
    if not results:
        raise ConversionError("File mxfile không chứa diagram hợp lệ.")
    return results


def _cell_label(cell: ET.Element, parents: dict[ET.Element, ET.Element]) -> str:
    value = cell.get("value")
    if value is None:
        wrapper = parents.get(cell)
        if wrapper is not None and wrapper.tag in {"object", "UserObject"}:
            value = wrapper.get("label") or wrapper.get("value")
    return _clean_label(value)


def _point(element: ET.Element | None) -> tuple[float, float] | None:
    if element is None or element.get("x") is None or element.get("y") is None:
        return None
    return _number(element.get("x")), _number(element.get("y"))


def _parse_graph(name: str, graph: ET.Element) -> Diagram:
    parents = {child: parent for parent in graph.iter() for child in parent}
    nodes: list[Node] = []
    edges: list[Edge] = []
    cells = list(graph.findall(".//mxCell"))
    edge_ids = {cell.get("id") for cell in cells if cell.get("edge") == "1"}
    embedded_edge_labels = {
        cell.get("parent", ""): label
        for cell in cells
        if cell.get("vertex") == "1" and cell.get("parent") in edge_ids
        if (label := _cell_label(cell, parents))
    }

    for cell in cells:
        geometry = cell.find("./mxGeometry")
        style = _style_map(cell.get("style"))
        if cell.get("vertex") == "1":
            # Native draw.io connector labels are relative vertices whose
            # parent is the connector itself, rather than values on the edge.
            if cell.get("parent") in edge_ids:
                continue
            if geometry is None:
                box = Box(0, 0, 0, 0)
            else:
                box = Box(
                    _number(geometry.get("x")),
                    _number(geometry.get("y")),
                    _number(geometry.get("width")),
                    _number(geometry.get("height")),
                )
            nodes.append(
                Node(
                    cell_id=cell.get("id", f"node_{len(nodes) + 1}"),
                    label=_cell_label(cell, parents),
                    style=style,
                    box=box,
                    is_text="text" in style and style.get("strokeColor") == "none",
                )
            )
        elif cell.get("edge") == "1":
            points: list[tuple[float, float]] = []
            source_point = target_point = None
            if geometry is not None:
                array = geometry.find("./Array[@as='points']")
                if array is not None:
                    points = [p for child in array.findall("./mxPoint") if (p := _point(child))]
                source_point = _point(geometry.find("./mxPoint[@as='sourcePoint']"))
                target_point = _point(geometry.find("./mxPoint[@as='targetPoint']"))
            edges.append(
                Edge(
                    cell_id=cell.get("id", f"edge_{len(edges) + 1}"),
                    source=cell.get("source"),
                    target=cell.get("target"),
                    label=_cell_label(cell, parents)
                    or embedded_edge_labels.get(cell.get("id", ""), ""),
                    style=style,
                    points=points,
                    source_point=source_point,
                    target_point=target_point,
                )
            )
    return Diagram(name=name, nodes=nodes, edges=edges)


def parse_drawio_xml(path: Path) -> list[Diagram]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ConversionError(f"Không đọc được XML '{path}': {exc}") from exc
    return [_parse_graph(name, graph) for name, graph in _diagram_roots(root)]


def _nearest_node(
    point: tuple[float, float], nodes: Iterable[Node], excluded: set[str], threshold: float
) -> Node | None:
    candidates = [(node.box.distance_to(point), node) for node in nodes if node.cell_id not in excluded]
    if not candidates:
        return None
    distance, node = min(candidates, key=lambda item: item[0])
    return node if distance <= threshold else None


def _infer_endpoints(diagram: Diagram, threshold: float) -> None:
    node_by_id = {node.cell_id: node for node in diagram.nodes if not node.is_text}
    real_nodes = list(node_by_id.values())
    for edge in diagram.edges:
        if edge.source not in node_by_id:
            edge.source = None
        if edge.target not in node_by_id:
            edge.target = None

        # draw.io sometimes keeps sourcePoint/targetPoint even when a terminal is
        # connected.  Prefer the explicit terminal and use geometry only to fill gaps.
        if edge.source is None:
            point = edge.source_point or (edge.points[0] if edge.points else None)
            if point:
                node = _nearest_node(point, real_nodes, {edge.target} if edge.target else set(), threshold)
                if node:
                    edge.source = node.cell_id
        if edge.target is None:
            point = edge.target_point or (edge.points[-1] if edge.points else None)
            if point:
                node = _nearest_node(point, real_nodes, {edge.source} if edge.source else set(), threshold)
                if node:
                    edge.target = node.cell_id


def _segment_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _edge_path(edge: Edge, node_by_id: dict[str, Node]) -> list[tuple[float, float]]:
    start = node_by_id[edge.source].box.center if edge.source in node_by_id else edge.source_point
    end = node_by_id[edge.target].box.center if edge.target in node_by_id else edge.target_point
    return ([start] if start else []) + edge.points + ([end] if end else [])


def _attach_text_labels(diagram: Diagram, threshold: float) -> set[str]:
    """Attach standalone text near a connector and return consumed node IDs."""
    node_by_id = {node.cell_id: node for node in diagram.nodes}
    consumed: set[str] = set()
    for node in (item for item in diagram.nodes if item.is_text and item.label):
        best: tuple[float, Edge] | None = None
        for edge in diagram.edges:
            if edge.label:
                continue
            path = _edge_path(edge, node_by_id)
            if len(path) < 2:
                continue
            distance = min(_segment_distance(node.box.center, a, b) for a, b in zip(path, path[1:]))
            if best is None or distance < best[0]:
                best = distance, edge
        if best and best[0] <= threshold:
            best[1].label = node.label
            consumed.add(node.cell_id)
    return consumed


def _choose_direction(diagram: Diagram, requested: str) -> str:
    if requested != "auto":
        return "TD" if requested == "TB" else requested
    content_nodes = [node for node in diagram.nodes if not node.is_text]
    nodes = {node.cell_id: node for node in content_nodes}

    # A tall source diagram with several steps is usually easier to read after
    # rotating its main flow to the horizontal axis.  Small diagrams retain
    # their natural top-to-bottom orientation.
    if len(content_nodes) >= 4:
        left = min(node.box.x for node in content_nodes)
        right = max(node.box.x + node.box.width for node in content_nodes)
        top = min(node.box.y for node in content_nodes)
        bottom = max(node.box.y + node.box.height for node in content_nodes)
        if bottom - top > (right - left) * 1.25:
            return "LR"

    dx = dy = 0.0
    for edge in diagram.edges:
        if edge.source in nodes and edge.target in nodes:
            x1, y1 = nodes[edge.source].box.center
            x2, y2 = nodes[edge.target].box.center
            dx += abs(x2 - x1)
            dy += abs(y2 - y1)
    return "LR" if dx > dy * 1.25 else "TD"


def _mermaid_config(curve: str, node_spacing: int, rank_spacing: int) -> str:
    """Build a compact, portable Mermaid init directive."""
    config = {
        "flowchart": {
            "curve": curve,
            "nodeSpacing": node_spacing,
            "rankSpacing": rank_spacing,
            "diagramPadding": 20,
        },
        "themeCSS": (
            ".flowchart-link{stroke-linecap:round;stroke-linejoin:round;}"
            ".edgeLabel{padding:2px 4px;}"
        ),
    }
    return f"%%{{init: {json.dumps(config, ensure_ascii=False, separators=(',', ':'))}}}%%"


def _wrap_label(label: str, width: int) -> str:
    if width <= 0:
        return label
    wrapped: list[str] = []
    for paragraph in label.splitlines() or [label]:
        lines = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        wrapped.extend(lines or [""])
    return "\n".join(wrapped)


def _quote_label(label: str, fallback: str, wrap_chars: int = 0) -> str:
    label = label or fallback
    label = _wrap_label(label, wrap_chars)
    label = label.replace("&", "&amp;").replace('"', "&quot;")
    label = label.replace("\n", "<br/>")
    return f'"{label}"'


def _node_syntax(node: Node, wrap_chars: int = 0) -> str:
    label = _quote_label(node.label, node.cell_id, wrap_chars)
    style = node.style
    shape = style.get("shape", "")
    if "rhombus" in style or shape == "rhombus":
        return f"{{{label}}}"
    if shape in {"parallelogram", "parallelogram2"}:
        return f"[/{label}/]" if shape == "parallelogram" else f"[\\{label}\\]"
    if shape in {"cylinder", "database"}:
        return f"[({label})]"
    if shape == "hexagon":
        return f"{{{{{label}}}}}"
    if "ellipse" in style or shape in {"ellipse", "oval"}:
        return f"([{label}])"
    if shape == "process" or style.get("rounded") == "0":
        return f"[{label}]"
    if style.get("rounded") == "1":
        return f"({label})"
    return f"[{label}]"


def _edge_syntax(edge: Edge) -> str:
    dashed = edge.style.get("dashed") == "1"
    no_arrow = edge.style.get("endArrow") == "none"
    if dashed:
        connector = "-.-" if no_arrow else "-.->"
    else:
        connector = "---" if no_arrow else "-->"
    if edge.label:
        label = _quote_label(edge.label, edge.label)
        return f"{connector}|{label}|"
    return connector


def _a4_flow_plan(node_count: int, requested: str) -> A4FlowPlan:
    """Choose compact node metrics while leaving room for ELK edge routing."""
    node_count = max(1, node_count)
    if requested == "portrait" or (requested == "adaptive" and node_count > 10):
        orientation, direction, layer_bound = "portrait", "TD", 4
    else:
        orientation, direction, layer_bound = "landscape", "LR", 4

    if node_count <= 12:
        font_size, label_chars = 13, 20
        node_spacing, rank_spacing = 48, 62
    elif node_count <= 25:
        font_size, label_chars = 12, 18
        node_spacing, rank_spacing = 54, 68
    else:
        font_size, label_chars = 11, 16
        node_spacing, rank_spacing = 60, 76
    return A4FlowPlan(
        orientation,
        direction,
        label_chars,
        font_size,
        node_spacing,
        rank_spacing,
        layer_bound,
    )


def _a4_mermaid_config(plan: A4FlowPlan, curve: str) -> str:
    config = {
        "layout": "elk",
        "htmlLabels": True,
        "flowchart": {
            "curve": curve,
            "useMaxWidth": True,
            "nodeSpacing": plan.node_spacing,
            "rankSpacing": plan.rank_spacing,
            "diagramPadding": 10,
            "padding": 8,
            "wrappingWidth": 110,
            "minNodeWidth": 80,
        },
        "elk": {
            "preset": "default",
            "layeringStrategy": "COFFMAN_GRAHAM",
            "layeringLayerBound": plan.layer_bound,
            "straightenEdges": True,
            "mergeEdges": False,
            "considerModelOrder": "NODES_AND_EDGES",
            "orientFeedbackEdges": True,
        },
        "themeVariables": {"fontSize": f"{plan.font_size}px"},
        "themeCSS": (
            ".nodeLabel p{line-height:1.2;margin:0;}"
            ".flowchart-link{stroke-linecap:round;stroke-linejoin:round;}"
            ".edgeLabel{padding:2px 4px;}"
        ),
    }
    return f"%%{{init: {json.dumps(config, ensure_ascii=False, separators=(',', ':'))}}}%%"


def diagram_to_mermaid(
    diagram: Diagram,
    direction: str = "auto",
    infer_distance: float = 60.0,
    infer_text_labels: bool = True,
    curve: str = "rounded",
    node_spacing: int = 60,
    rank_spacing: int = 80,
    page_layout: str = "adaptive",
) -> str:
    diagram.warnings.clear()
    _infer_endpoints(diagram, infer_distance)
    consumed = _attach_text_labels(diagram, infer_distance) if infer_text_labels else set()
    visible_nodes = [node for node in diagram.nodes if node.cell_id not in consumed]
    visible_ids = {node.cell_id for node in visible_nodes}
    visible_by_id = {node.cell_id: node for node in visible_nodes}

    if page_layout == "flowchart":
        selected_direction = _choose_direction(diagram, direction)
        config_line = _mermaid_config(curve, node_spacing, rank_spacing)
        label_chars = 0
        comment: list[str] = []
    else:
        plan = _a4_flow_plan(len(visible_nodes), page_layout)
        selected_direction = plan.direction
        config_line = _a4_mermaid_config(plan, curve)
        label_chars = plan.label_chars
        comment = [
            f"%% A4 {plan.orientation}; compact nodes; ELK collision-aware routing %%"
        ]

    lines = [config_line, *comment, f"flowchart {selected_direction}"]
    for node in visible_nodes:
        lines.append(f"    {node.mermaid_id}{_node_syntax(node, label_chars)}")
    for edge in diagram.edges:
        if edge.source in visible_ids and edge.target in visible_ids:
            source = visible_by_id[edge.source]
            target = visible_by_id[edge.target]
            lines.append(f"    {source.mermaid_id} {_edge_syntax(edge)} {target.mermaid_id}")
        else:
            diagram.warnings.append(
                f"Bỏ qua cạnh {edge.cell_id}: không xác định đủ source/target "
                f"({edge.source or '?' } -> {edge.target or '?'})."
            )
    return "\n".join(lines) + "\n"


def convert_file(
    input_path: Path,
    output_path: Path | None = None,
    diagram_name: str | None = None,
    direction: str = "auto",
    infer_distance: float = 60.0,
    infer_text_labels: bool = True,
    curve: str = "rounded",
    node_spacing: int = 60,
    rank_spacing: int = 80,
    page_layout: str = "adaptive",
) -> str:
    diagrams = parse_drawio_xml(input_path)
    if diagram_name:
        selected = [diagram for diagram in diagrams if diagram.name == diagram_name]
        if not selected:
            available = ", ".join(repr(diagram.name) for diagram in diagrams)
            raise ConversionError(f"Không có diagram {diagram_name!r}. Hiện có: {available}")
    else:
        if len(diagrams) > 1:
            available = ", ".join(repr(diagram.name) for diagram in diagrams)
            raise ConversionError(
                "File có nhiều diagram. Hãy chọn một diagram bằng --diagram. "
                f"Hiện có: {available}"
            )
        selected = diagrams

    chunks: list[str] = []
    for diagram in selected:
        if len(selected) > 1:
            chunks.append(f"%% Diagram: {diagram.name}\n")
        chunks.append(
            diagram_to_mermaid(
                diagram,
                direction,
                infer_distance,
                infer_text_labels,
                curve,
                node_spacing,
                rank_spacing,
                page_layout,
            )
        )
        for warning in diagram.warnings:
            print(f"Cảnh báo [{diagram.name}]: {warning}", file=sys.stderr)
    result = "\n".join(chunk.rstrip() for chunk in chunks) + "\n"
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result, encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chuyển flowchart XML của diagrams.net/draw.io sang Mermaid."
    )
    parser.add_argument("input", type=Path, help="File .drawio hoặc .xml đầu vào")
    parser.add_argument("-o", "--output", type=Path, help="File .mmd đầu ra (mặc định: stdout)")
    parser.add_argument("--diagram", help="Chỉ chuyển diagram/page có đúng tên này")
    parser.add_argument(
        "--direction", choices=VALID_DIRECTIONS, default="auto", help="Hướng flowchart (mặc định: auto)"
    )
    parser.add_argument(
        "--page-layout",
        choices=PAGE_LAYOUTS,
        default="adaptive",
        help="Bố cục trang: A4 tự động/dọc/ngang hoặc flowchart cũ (mặc định: adaptive)",
    )
    parser.add_argument(
        "--infer-distance",
        type=float,
        default=60.0,
        metavar="PX",
        help="Khoảng cách tối đa để suy ra đầu nối/nhãn rời (mặc định: 60)",
    )
    parser.add_argument(
        "--no-infer-labels",
        action="store_true",
        help="Không tự gắn các text rời gần đường nối thành nhãn cạnh",
    )
    parser.add_argument(
        "--curve",
        choices=MERMAID_CURVES,
        default="rounded",
        help="Kiểu đường nối Mermaid (mặc định: rounded)",
    )
    parser.add_argument(
        "--node-spacing",
        type=int,
        default=60,
        metavar="PX",
        help="Khoảng cách giữa các node cùng cấp (mặc định: 60)",
    )
    parser.add_argument(
        "--rank-spacing",
        type=int,
        default=80,
        metavar="PX",
        help="Khoảng cách giữa các cấp của flowchart (mặc định: 80)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.infer_distance < 0 or args.node_spacing < 0 or args.rank_spacing < 0:
        print("Lỗi: các giá trị khoảng cách không được âm.", file=sys.stderr)
        return 2
    try:
        result = convert_file(
            input_path=args.input,
            output_path=args.output,
            diagram_name=args.diagram,
            direction=args.direction,
            infer_distance=args.infer_distance,
            infer_text_labels=not args.no_infer_labels,
            curve=args.curve,
            node_spacing=args.node_spacing,
            rank_spacing=args.rank_spacing,
            page_layout=args.page_layout,
        )
    except ConversionError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        try:
            sys.stdout.write(result)
        except UnicodeEncodeError:
            # Some Windows terminals still default to a legacy code page.
            sys.stdout.buffer.write(result.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
