import tempfile
import unittest
from pathlib import Path

from xml_to_mermaid_gui import convert_inputs, safe_filename, source_stem


GRAPH = """<mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="a" value="Bắt đầu" style="ellipse" vertex="1" parent="1">
    <mxGeometry x="0" y="0" width="100" height="50" as="geometry"/>
  </mxCell>
  <mxCell id="b" value="Kết thúc" style="ellipse" vertex="1" parent="1">
    <mxGeometry x="0" y="100" width="100" height="50" as="geometry"/>
  </mxCell>
  <mxCell id="e" source="a" target="b" edge="1" parent="1">
    <mxGeometry relative="1" as="geometry"/>
  </mxCell>
</root></mxGraphModel>"""


class GuiHelperTests(unittest.TestCase):
    def test_friendly_and_safe_names(self):
        self.assertEqual("quy-trinh", source_stem(Path("quy-trinh.drawio.xml")))
        self.assertEqual("Trang_1", safe_filename('Trang:1'))
        self.assertEqual("_CON", safe_filename("CON"))

    def test_multiple_pages_create_multiple_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "flow.drawio.xml"
            source.write_text(
                f'<mxfile><diagram name="Trang 1">{GRAPH}</diagram>'
                f'<diagram name="Trang:2">{GRAPH}</diagram></mxfile>',
                encoding="utf-8",
            )
            results, errors = convert_inputs([source], root / "output")
            self.assertFalse(errors)
            self.assertEqual(2, len(results))
            self.assertEqual({"flow__Trang 1.mmd", "flow__Trang_2.mmd"}, {r.output.name for r in results})
            self.assertTrue(all(result.output.exists() for result in results))

    def test_existing_output_is_not_overwritten_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "flow.xml"
            source.write_text(GRAPH, encoding="utf-8")
            output = root / "output"
            output.mkdir()
            original = output / "flow.mmd"
            original.write_text("keep me", encoding="utf-8")
            results, errors = convert_inputs([source], output)
            self.assertFalse(errors)
            self.assertEqual("keep me", original.read_text(encoding="utf-8"))
            self.assertEqual("flow_2.mmd", results[0].output.name)


if __name__ == "__main__":
    unittest.main()
