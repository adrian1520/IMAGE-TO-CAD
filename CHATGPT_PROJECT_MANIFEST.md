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
# Imager — manifest plików do ChatGPT Projects / Custom GPT

Środowisko ChatGPT Projects na iOS traktuj jako płaską przestrzeń plików. Do projektu wgraj maksymalnie poniższe pliki — bez folderów:

1. `CHATGPT_PROJECT_INSTRUCTIONS.md` — główne instrukcje projektu.
2. `IMAGER_IMAGE_PROMPTS.md` — szablony dla natywnego @Stwórz Obraz.
3. `ada_upscale_a4_pdf.py` — kod Ada/Code Interpreter do upscale x8 i PDF A4.
4. `self_engine.py` — opcjonalny deterministyczny silnik geometrii, jeśli środowisko pozwala na Python + Pillow/OpenCV.
5. `README.md` — opis techniczny i lokalne uruchomienie.

Custom GPT nie wymaga OpenAPI Actions dla podstawowego wariantu, ponieważ proces działa na plikach użytkownika, natywnym generowaniu obrazów i Code Interpreter. Jeśli dodasz własny publiczny endpoint, dopiero wtedy dołącz schemat Actions.
