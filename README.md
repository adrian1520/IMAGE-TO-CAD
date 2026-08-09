# IMAGE-TO-CAD SelfEngine MVP

SelfEngine converts scanned or photographed architectural drawings into clean CAD-style artifacts.  The implementation is intentionally kept in `self_engine.py` so it can be copied into the ChatGPT Python Tool, while preserving a modular architecture for production hardening.

## Pipeline

```text
photo / scan / PDF
  -> ImageCleaner       perspective correction, deskew, adaptive threshold, CLAHE, morphology
  -> GeometryEngine     line detection, snapping, merging, junctions, room/symbol recognition
  -> OCREngine          PaddleOCR first, Tesseract fallback, future backend boundary
  -> Renderer/Exporters SVG, DXF, PDF, DOCX, PNG previews, JSON/debug artifacts
```

## Quick start

```python
from self_engine import SelfEngine

engine = SelfEngine()
result = engine.run(
    image="/mnt/data/photo.jpg",
    paper="A3",
    dpi=1200,
    output=["png", "svg", "pdf", "docx", "dxf"],
)
print(result["artifacts"])
```

CLI:

```bash
python self_engine.py /mnt/data/photo.jpg --paper A3 --dpi 1200 --output png svg pdf docx dxf
```

## Runtime outputs

By default artifacts are written to `/mnt/data/runtime`:

- `preview_8k.png`, `preview_16k.png`, `preview_x8.png`
- `drawing.svg` with semantic layers: Geometry, Walls, Doors, Windows, Symbols, Text, Debug
- `drawing.dxf` containing LINE, LWPOLYLINE, CIRCLE, ARC/TEXT where present
- `drawing.pdf` fitted to A4 by default, `drawing.docx`
- `geometry.json`, `text.json`, `symbol_text.json`, `report.json`
- debug artifacts: `threshold.png`, `lines.png`, `junctions.png`, `snap.png`, `segments.json`

## Dependencies

The only hard runtime dependency for fallback operation is Pillow.  Production-quality reconstruction benefits from OpenCV and NumPy; exporters and OCR backends remain optional and are loaded lazily.

```bash
python -m pip install -r requirements.txt
```

## MVP geometry strategy

The engine avoids contour-polygon tracing as the primary representation.  With OpenCV installed it uses LSD or Probabilistic Hough line detection, then constrains angles, snaps endpoints, merges collinear/overlapping fragments, computes junctions, and recognizes rooms and simple primitives.  Without OpenCV it falls back to projection-based line extraction so the pipeline remains runnable in restricted notebook environments.


## ChatGPT Imager production package

For deployment in `ChatGPT/projects/Imager` or a Custom GPT with a flat file limit, upload only the production set:

1. `CHATGPT_IMAGER_PROJECT.md` as the main instructions.
2. `CHATGPT_PROJECT_MANIFEST.md` as the upload manifest and separation contract.
3. `IMAGER_IMAGE_PROMPTS.md` as the geometry-only image-generation prompts.
4. `self_engine.py` as the deterministic Advanced Data Analysis / Code Interpreter reconstruction tool.
5. `requirements.txt` when the environment supports dependency installation.

The ChatGPT flow is: generate a clean 1:1 mini-CAD preview with `@Stwórz Obraz`, run `self_engine.py` in Code Interpreter, create the x8 upscale artifact, and export the final A4 PDF.


Symbol labels are recovered from original-image local OCR crops and written to `symbol_text.json`; generated image text is never treated as final truth.
