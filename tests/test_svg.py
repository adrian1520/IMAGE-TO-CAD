from self_engine import GeometryPrimitive, SVGExporter, TextBlock


def test_svg_exporter_writes_semantic_layers(tmp_path):
    path = tmp_path / "drawing.svg"
    geometry = [GeometryPrimitive("line", [(0, 0), (100, 0)], (0, 0, 100, 0), "Walls")]
    text = [TextBlock("ROOM", (10, 10, 40, 24), 0.9)]
    SVGExporter().export(path, 200, 100, geometry, text)
    svg = path.read_text()
    assert 'id="Walls"' in svg
    assert 'id="Doors"' in svg
    assert 'id="Windows"' in svg
    assert 'id="Text"' in svg
    assert "<line" in svg
    assert "ROOM" in svg
