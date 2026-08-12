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


def test_photo_preprocessing_suppresses_fold_shadow_band(tmp_path):
    pytest = __import__("pytest")
    np = pytest.importorskip("numpy")
    from self_engine import ImageCleaner

    cleaner = ImageCleaner(OptionalModules(), EngineConfig(fold_line_suppression_width=6))
    binary = np.full((60, 120), 255, dtype=np.uint8)
    binary[29:32, ::4] = 0
    binary[:, 20] = 0
    warnings = []

    cleaned = cleaner._suppress_fold_shadows(binary, None, np, warnings)

    assert cleaned[30, 60] == 255
    assert cleaned[10, 20] == 0
    assert any("fold/shadow" in warning for warning in warnings)


def test_geometry_engine_detects_parallel_window_semantic_primitive():
    config = EngineConfig(window_parallel_distance=8, min_line_length=10, angle_tolerance_degrees=3)
    engine = GeometryEngine(OptionalModules(), config)
    lines = [
        GeometryPrimitive("line", [(10, 20), (80, 20)], (10, 20, 80, 20), "Walls"),
        GeometryPrimitive("line", [(12, 25), (82, 25)], (12, 25, 82, 25), "Walls"),
    ]

    windows = engine._detect_windows(lines)

    assert len(windows) == 1
    assert windows[0].kind == "window"
    assert windows[0].layer == "Windows"
def test_mvp_02_preserves_opening_gap_and_builds_endpoint_graph():
    from self_engine import EngineConfig, GeometryPrimitive, SnapEngine

    config = EngineConfig(merge_distance=8, min_line_length=5, opening_min_width=20, opening_max_width=60)
    lines = [
        GeometryPrimitive("line", [(0, 0), (50, 0)], (0, 0, 50, 0), "Walls"),
        GeometryPrimitive("line", [(80, 0), (130, 0)], (80, 0, 130, 0), "Walls"),
    ]

    merged, metrics = SnapEngine(config).snap_and_merge(lines)

    assert len(merged) == 2
    assert metrics["preserved_openings"] == 1
    assert all(line.metadata["semantic"] == "wall_centerline" for line in merged)


def test_mvp_02_detects_openings_coordinate_frame_and_report(tmp_path):
    from self_engine import CleanImage, EngineConfig, GeometryEngine, GeometryPrimitive, OptionalModules, QualityEngine

    config = EngineConfig(runtime_dir=str(tmp_path), debug=True, opening_min_width=20, opening_max_width=60, merge_distance=8)
    engine = GeometryEngine(OptionalModules(), config)
    lines = [
        GeometryPrimitive("line", [(0, 0), (50, 0)], (0, 0, 50, 0), "Walls"),
        GeometryPrimitive("line", [(80, 0), (130, 0)], (80, 0, 130, 0), "Walls"),
    ]
    openings = engine._detect_openings(lines)
    graph = engine._endpoint_graph(lines)
    clean = CleanImage("source.png", "clean.png", "threshold.png", 140, 80, "RGB", 128)
    assert len(openings) == 1
    assert openings[0].kind == "window"
    assert len(graph) == 4

    from self_engine import GeometryResult
    result = GeometryResult(lines + openings, lines, [], [], {"door_count": 0, "window_count": 1, "endpoint_node_count": 4}, engine._coordinate_frame(lines, clean), graph)
    report = QualityEngine().report(clean, result, [], [], 0.1, config)
    assert report["mvp_version"] == "0.2"
    assert report["coordinate_frame"]["unit"] == "px"
    assert report["window_count"] == 1
