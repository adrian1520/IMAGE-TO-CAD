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
