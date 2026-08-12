import json


def test_pipeline_end_to_end_generates_core_artifacts(tmp_path):
    pytest = __import__("pytest")
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    from self_engine import EngineConfig, SelfEngine

    image_path = tmp_path / "plan.png"
    img = Image.new("RGB", (220, 160), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 30, 190, 130), outline="black", width=3)
    draw.line((110, 30, 110, 130), fill="black", width=2)
    draw.text((45, 45), "101", fill="black")
    img.save(image_path)

    runtime = tmp_path / "runtime"
    engine = SelfEngine(EngineConfig(runtime_dir=str(runtime), output=("png", "svg", "dxf"), debug=True, render_max_pixels=8_000_000, hough_min_line_length=20))
    result = engine.run(image=str(image_path), paper="A3", dpi=1200)
    artifacts = result["artifacts"]
    for key in ["svg", "dxf", "png_8k", "png_x8", "geometry", "text", "symbol_text", "report", "threshold", "segments", "overlay_qc"]:
        assert key in artifacts
    for value in artifacts.values():
        assert (runtime / value.split("/")[-1]).exists() or value
    report = json.loads((runtime / "report.json").read_text())
    assert report["line_count"] >= 1
    assert "processing_time_seconds" in report


def test_pdf_exporter_creates_a4_page(tmp_path):
    pytest = __import__("pytest")
    Image = pytest.importorskip("PIL.Image")
    from self_engine import OptionalModules, PDFExporter

    source = tmp_path / "preview.png"
    target = tmp_path / "drawing.pdf"
    Image.new("RGB", (100, 50), "white").save(source)

    warning = PDFExporter(OptionalModules()).export(target, source, paper_size="A4", dpi=72)

    assert warning is None
    assert target.exists()
    assert target.stat().st_size > 0
