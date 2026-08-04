from self_engine import DXFExporter, GeometryPrimitive, OptionalModules, TextBlock


def test_dxf_exporter_writes_line_and_text(tmp_path):
    path = tmp_path / "drawing.dxf"
    geometry = [GeometryPrimitive("line", [(0, 0), (100, 0)], (0, 0, 100, 0), "Walls")]
    text = [TextBlock("A", (5, 5, 10, 15), 1.0)]
    DXFExporter(OptionalModules()).export(path, geometry, text)
    dxf = path.read_text(errors="ignore")
    assert "LINE" in dxf
    assert "TEXT" in dxf
    assert "Walls" in dxf
