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


## ChatGPT Projects / Custom GPT deployment

For the final **Imager** workflow in ChatGPT Projects or a Custom GPT, use the flat-file bundle described in `CHATGPT_PROJECT_MANIFEST.md`. The production conversation flow is:

1. inspect the uploaded photo, scan, or PDF,
2. generate a clean 1:1 mini-CAD visual with native `@Stwórz Obraz` using `IMAGER_IMAGE_PROMPTS.md`,
3. run `ada_upscale_a4_pdf.py` in Ada/Code Interpreter to create a deterministic x8 PNG and A4 PDF,
4. optionally run `self_engine.py` when deterministic geometry artifacts such as SVG, DXF, JSON, and debug overlays are required.

The bundle stays compatible with flat ChatGPT project storage: no nested folders are required for runtime use, and the required files are below the 20/25-file limits described in the deployment manifest.

## Runtime outputs

By default artifacts are written to `/mnt/data/runtime`:

- `preview_8k.png`, `preview_16k.png`
- `drawing.svg` with semantic layers: Geometry, Walls, Doors, Windows, Symbols, Text, Debug
- `drawing.dxf` containing LINE, LWPOLYLINE, CIRCLE, ARC/TEXT where present
- `drawing.pdf`, `drawing.docx`
- `geometry.json`, `text.json`, `report.json`
- debug artifacts: `threshold.png`, `lines.png`, `junctions.png`, `snap.png`, `segments.json`

## Dependencies

The only hard runtime dependency for fallback operation is Pillow.  Production-quality reconstruction benefits from OpenCV and NumPy; exporters and OCR backends remain optional and are loaded lazily.

```bash
python -m pip install -r requirements.txt
```

## MVP geometry strategy

The engine avoids contour-polygon tracing as the primary representation.  With OpenCV installed it uses LSD or Probabilistic Hough line detection, then constrains angles, snaps endpoints, merges collinear/overlapping fragments, computes junctions, and recognizes rooms and simple primitives.  Without OpenCV it falls back to projection-based line extraction so the pipeline remains runnable in restricted notebook environments.
