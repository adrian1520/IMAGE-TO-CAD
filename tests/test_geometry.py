from self_engine import EngineConfig, GeometryEngine, GeometryPrimitive, OptionalModules, SnapEngine


def test_snap_engine_merges_fragmented_wall_segments():
    config = EngineConfig(snap_distance=6, merge_distance=10, min_line_length=5)
    snapper = SnapEngine(config)
    lines = [
        GeometryPrimitive("line", [(0, 0), (50, 1)], (0, 0, 50, 1), "Walls"),
        GeometryPrimitive("line", [(55, 0), (100, 0)], (55, 0, 100, 0), "Walls"),
    ]
    merged, metrics = snapper.snap_and_merge(lines)
    assert len(merged) == 1
    assert metrics["merged_lines"] >= 1
    assert merged[0].kind == "line"
    assert merged[0].layer == "Walls"


def test_geometry_engine_fallback_returns_semantic_lines(tmp_path):
    pytest = __import__("pytest")
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image_path = tmp_path / "drawing.png"
    img = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, 140, 100), outline="black", width=2)
    img.save(image_path)

    from self_engine import ImageCleaner

    config = EngineConfig(runtime_dir=str(tmp_path), output=("svg",), debug=False, hough_min_line_length=20)
    modules = OptionalModules()
    clean = ImageCleaner(modules, config).clean_image(str(image_path), tmp_path)
    result = GeometryEngine(modules, config).reconstruct_geometry(clean, tmp_path)
    assert result.metrics["line_count"] > 0
    assert all(p.kind != "polygon" for p in result.primitives if p.layer == "Walls")
