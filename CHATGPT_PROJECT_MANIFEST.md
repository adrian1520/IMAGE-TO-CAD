# CHATGPT Project Manifest — Imager

## Files to upload

1. `CHATGPT_IMAGER_PROJECT.md` — main operator instructions.
2. `IMAGER_IMAGE_PROMPTS.md` — geometry-only image-generation prompts.
3. `self_engine.py` — deterministic Code Interpreter pipeline.
4. `requirements.txt` — optional dependency list when package installation is available.

This set stays within both ChatGPT Project and Custom GPT flat-file limits.

## Separation rule

**GEOMETRIA ≠ TEKST.** Geometry reconstruction and text recovery are separate workflows. Native image generation may clean and redraw geometry, but it must not be treated as the source of final symbol labels. Final text is recovered from the original photo/scan with local OCR, validated against known patterns, and composited as a separate layer.

## Final deliverable flow

```text
original photo/scan
  ├─ geometry path: clean mini-CAD base via @Stwórz Obraz / deterministic geometry engine
  └─ text path: local symbol crops from original → multi-variant OCR → normalization → validation

clean geometry base + validated OCR text layer → x8 preview + A4 PDF
```

## Quality gates

- Geometry QC checks alignment, room closure, symbol placement, arcs/circles, doors, and windows.
- Text QC checks only OCR candidates from original crops.
- `MANUAL_REVIEW` must be used when candidates are ambiguous or below the configured confidence threshold.
- Regenerating geometry must not regenerate final text; correcting text must not regenerate geometry.
