import base64
import tempfile
import unittest
import urllib.parse
import zlib
from pathlib import Path

from xml_to_mermaid import ConversionError, convert_file, parse_drawio_xml


GRAPH = """<mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="a" value="Bắt đầu&lt;br&gt;ngay" style="ellipse;html=1" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="100" height="50" as="geometry"/>
  </mxCell>
  <mxCell id="b" value="Hợp lệ?" style="rhombus;html=1" vertex="1" parent="1">
    <mxGeometry x="0" y="100" width="100" height="60" as="geometry"/>
  </mxCell>
  <mxCell id="e1" source="a" target="b" edge="1" parent="1">
    <mxGeometry relative="1" as="geometry"/>
  </mxCell>
  <mxCell id="label" value="Có" style="text;strokeColor=none;fillColor=none" vertex="1" parent="1">
    <mxGeometry x="55" y="55" width="30" height="20" as="geometry"/>
  </mxCell>
</root></mxGraphModel>"""


class ConverterTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        temp = tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-8", delete=False)
        temp.write(text)
        temp.close()
        self.addCleanup(Path(temp.name).unlink, missing_ok=True)
        return Path(temp.name)

    def test_plain_drawio_and_unicode(self):
        path = self.write(f'<mxfile><diagram name="Trang chính">{GRAPH}</diagram></mxfile>')
        output = convert_file(path)
        self.assertIn("%% A4 landscape; compact nodes; ELK collision-aware routing %%", output)
        self.assertIn('"layout":"elk"', output)
        self.assertIn('"wrappingWidth":110', output)
        self.assertIn("flowchart LR", output)
        self.assertIn('na(["Bắt đầu<br/>ngay"])', output)
        self.assertIn('nb{"Hợp lệ?"}', output)
        self.assertIn('na -->|"Có"| nb', output)
        self.assertNotIn("nlabel", output)

    def test_compressed_drawio(self):
        encoded = urllib.parse.quote(GRAPH).encode()
        compressor = zlib.compressobj(wbits=-15)
        payload = base64.b64encode(compressor.compress(encoded) + compressor.flush()).decode()
        path = self.write(f'<mxfile><diagram name="Compressed">{payload}</diagram></mxfile>')
        diagrams = parse_drawio_xml(path)
        self.assertEqual("Compressed", diagrams[0].name)
        self.assertEqual(3, len(diagrams[0].nodes))

    def test_missing_target_is_inferred_from_target_point(self):
        graph = GRAPH.replace(
            'source="a" target="b" edge="1"',
            'source="a" edge="1"',
        ).replace(
            '<mxGeometry relative="1" as="geometry"/>',
            '<mxGeometry relative="1" as="geometry"><mxPoint x="50" y="90" as="targetPoint"/></mxGeometry>',
            1,
        )
        path = self.write(graph)
        self.assertIn("na -->", convert_file(path, infer_text_labels=False))

    def test_native_child_edge_label(self):
        graph = GRAPH.replace(
            '<mxCell id="e1" source="a" target="b" edge="1" parent="1">',
            '<mxCell id="e1" source="a" target="b" edge="1" parent="1" value="">',
        ).replace(
            '</root>',
            '<mxCell id="edge-label" value="Tiếp tục" vertex="1" parent="e1">'
            '<mxGeometry relative="1" as="geometry"/></mxCell></root>',
        )
        path = self.write(graph)
        output = convert_file(path, infer_text_labels=False)
        self.assertIn('-->|"Tiếp tục"|', output)
        self.assertNotIn("edge-label", output)

    def test_multiple_diagrams_requires_selection(self):
        path = self.write(
            f'<mxfile><diagram name="Một">{GRAPH}</diagram>'
            f'<diagram name="Hai">{GRAPH}</diagram></mxfile>'
        )
        with self.assertRaisesRegex(ConversionError, "nhiều diagram"):
            convert_file(path)
        self.assertIn("flowchart", convert_file(path, diagram_name="Hai"))

    def test_many_nodes_use_compact_portrait_elk_layout(self):
        nodes = []
        edges = []
        for index in range(12):
            nodes.append(
                f'<mxCell id="n{index}" value="Bước {index}" vertex="1" parent="1">'
                f'<mxGeometry x="0" y="{index * 150}" width="100" height="50" as="geometry"/>'
                '</mxCell>'
            )
            if index:
                edges.append(
                    f'<mxCell id="e{index}" source="n{index - 1}" target="n{index}" '
                    'edge="1" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>'
                )
        path = self.write(
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            + "".join(nodes + edges)
            + '</root></mxGraphModel>'
        )
        output = convert_file(path)
        self.assertIn("%% A4 portrait; compact nodes; ELK collision-aware routing %%", output)
        self.assertIn("flowchart TD", output)
        self.assertIn('"layeringStrategy":"COFFMAN_GRAHAM"', output)
        self.assertIn('"fontSize":"13px"', output)

    def test_style_options_are_written_to_config(self):
        path = self.write(GRAPH)
        output = convert_file(
            path,
            curve="stepAfter",
            node_spacing=75,
            rank_spacing=90,
            page_layout="flowchart",
        )
        self.assertIn('"curve":"stepAfter"', output)
        self.assertIn('"nodeSpacing":75', output)
        self.assertIn('"rankSpacing":90', output)

    def test_forced_a4_landscape(self):
        path = self.write(GRAPH)
        output = convert_file(path, page_layout="landscape")
        self.assertIn("%% A4 landscape; compact nodes; ELK collision-aware routing %%", output)
        self.assertIn("flowchart LR", output)

    def test_unknown_diagram_name(self):
        path = self.write(GRAPH)
        with self.assertRaises(ConversionError):
            convert_file(path, diagram_name="Không tồn tại")


if __name__ == "__main__":
    unittest.main()
