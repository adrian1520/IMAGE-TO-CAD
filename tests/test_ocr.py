from self_engine import CleanImage, EngineConfig, OCREngine, OptionalModules


def test_ocr_returns_blocks_and_warnings_separately_when_backend_missing(tmp_path):
    clean = CleanImage("input.png", "clean.png", "threshold.png", 100, 100, "RGB", 128)
    text, warnings = OCREngine(OptionalModules(), EngineConfig(runtime_dir=str(tmp_path))).reconstruct_text(clean)
    assert isinstance(text, list)
    assert isinstance(warnings, list)


def test_symbol_text_normalization_is_pattern_limited(tmp_path):
    from self_engine import SymbolTextEngine

    engine = SymbolTextEngine(OptionalModules(), EngineConfig(runtime_dir=str(tmp_path)))

    assert engine.normalize_symbol_text("RG-F19") == ("RG/F19", r"^RG/F\d{2}$", True)
    assert engine.normalize_symbol_text("RG/F l6") == ("RG/F16", r"^RG/F\d{2}$", True)
    assert engine.normalize_symbol_text("HC O20") == ("HC Ø20", r"^HC Ø\d+$", True)
    assert engine.normalize_symbol_text("unknown") == ("UNKNOWN", None, False)


def test_symbol_text_ranking_uses_manual_review_for_ambiguity(tmp_path):
    from self_engine import OCRCandidate, SymbolTextEngine

    engine = SymbolTextEngine(OptionalModules(), EngineConfig(runtime_dir=str(tmp_path), symbol_text_confidence_threshold=0.80))
    selected, status, reason = engine._select_candidate([
        OCRCandidate("RG/F19", "gray_x8", 0.91, "RG/F19", r"^RG/F\d{2}$", True, False),
        OCRCandidate("RG/F16", "threshold", 0.90, "RG/F16", r"^RG/F\d{2}$", True, False),
    ])

    assert selected is None
    assert status == "MANUAL_REVIEW"
    assert reason == "ambiguous_top_candidates"
