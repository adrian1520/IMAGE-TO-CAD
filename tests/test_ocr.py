from self_engine import CleanImage, EngineConfig, OCREngine, OptionalModules


def test_ocr_returns_blocks_and_warnings_separately_when_backend_missing(tmp_path):
    clean = CleanImage("input.png", "clean.png", "threshold.png", 100, 100, "RGB", 128)
    text, warnings = OCREngine(OptionalModules(), EngineConfig(runtime_dir=str(tmp_path))).reconstruct_text(clean)
    assert isinstance(text, list)
    assert isinstance(warnings, list)
