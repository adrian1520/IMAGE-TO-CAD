#!/usr/bin/env python3
"""Self Engine: single-file image/PDF to clean vector document pipeline.

This module is designed for direct execution in notebook-like environments such
as the ChatGPT Python Tool.  It keeps every stage in one file while preserving a
modular architecture so OCR, geometry reconstruction, and export backends can be
swapped later without changing the public API.

Typical use:

    from self_engine import SelfEngine

    engine = SelfEngine()
    result = engine.run(
        image="/mnt/data/photo.jpg",
        paper="A3",
        dpi=1200,
        output=["png", "svg", "pdf", "docx"],
    )
    print(result["artifacts"])

The implementation intentionally treats heavy third-party libraries as optional.
When OpenCV, PaddleOCR, Tesseract, ReportLab, python-docx, or svgwrite are not
installed, Self Engine falls back to pure-Python/Pillow/Numpy paths where
possible and records capability warnings in runtime/report.json.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class EngineConfig:
    """Runtime configuration for SelfEngine."""

    paper: str = "A3"
    dpi: int = 1200
    output: Sequence[str] = ("png", "svg", "pdf", "docx")
    runtime_dir: str = "/mnt/data/runtime"
    render_max_pixels: int = 140_000_000
    simplify_tolerance: float = 2.0
    min_component_area: int = 24
    ocr_languages: str = "en"
    background: str = "white"
    foreground: str = "black"


@dataclass
class CleanImage:
    """Cleaned document image plus processing metadata."""

    source_path: str
    image_path: str
    width: int
    height: int
    mode: str
    threshold: int
    warnings: List[str] = field(default_factory=list)


@dataclass
class GeometryPrimitive:
    """Serializable vector primitive reconstructed from document content."""

    kind: str
    points: List[Tuple[float, float]]
    bbox: Tuple[float, float, float, float]
    stroke: str = "#000000"
    fill: str = "none"
    stroke_width: float = 1.0
    confidence: float = 0.5


@dataclass
class TextBlock:
    """OCR text block."""

    text: str
    bbox: Tuple[float, float, float, float]
    confidence: float
    font_size: float = 12.0
    font_family: str = "DejaVu Sans"


@dataclass
class RenderResult:
    """Paths and metadata produced by the renderer/exporters."""

    runtime_dir: str
    artifacts: Dict[str, str]
    geometry_count: int
    text_count: int
    warnings: List[str]


class OptionalModules:
    """Lazy optional dependency registry that avoids import-time failures."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def load(self, module_name: str) -> Any:
        if module_name not in self._cache:
            parts = module_name.split(".")
            available = True
            for index in range(1, len(parts) + 1):
                if importlib.util.find_spec(".".join(parts[:index])) is None:
                    available = False
                    break
            self._cache[module_name] = importlib.import_module(module_name) if available else None
        return self._cache[module_name]

    def available(self, module_name: str) -> bool:
        return self.load(module_name) is not None


class ImageCleaner:
    """Input detector and document cleanup stage."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules = modules
        self.config = config

    def clean_image(self, image_path: str, runtime_dir: Path) -> CleanImage:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        source = Path(image_path)
        if not source.exists():
            raise FileNotFoundError(f"Input file does not exist: {image_path}")

        prepared = self._prepare_input(source, runtime_dir)
        pil = self._load_pillow(prepared).convert("RGB")
        cleaned, threshold, warnings = self._binarize_and_deskew(pil)
        out_path = runtime_dir / "clean.png"
        cleaned.save(out_path)
        return CleanImage(str(source), str(out_path), cleaned.width, cleaned.height, cleaned.mode, threshold, warnings)

    def _prepare_input(self, source: Path, runtime_dir: Path) -> Path:
        suffix = source.suffix.lower()
        if suffix == ".pdf":
            return self._pdf_to_image(source, runtime_dir)
        return source

    def _pdf_to_image(self, source: Path, runtime_dir: Path) -> Path:
        pdf2image = self.modules.load("pdf2image")
        if pdf2image:
            pages = pdf2image.convert_from_path(str(source), first_page=1, last_page=1, dpi=300)
            out_path = runtime_dir / "pdf_page_1.png"
            pages[0].save(out_path)
            return out_path
        raise RuntimeError("PDF input requires pdf2image/poppler in this environment.")

    def _load_pillow(self, source: Path) -> Any:
        pillow = self.modules.load("PIL.Image")
        if pillow is None:
            raise RuntimeError("Pillow is required to load raster images.")
        return pillow.open(source)

    def _binarize_and_deskew(self, pil_image: Any) -> Tuple[Any, int, List[str]]:
        warnings: List[str] = []
        np = self.modules.load("numpy")
        cv2 = self.modules.load("cv2")
        image_mod = self.modules.load("PIL.Image")
        image_ops = self.modules.load("PIL.ImageOps")
        image_filter = self.modules.load("PIL.ImageFilter")
        if np is not None and cv2 is not None:
            arr = np.array(pil_image)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            gray = cv2.fastNlMeansDenoising(gray, None, 12, 7, 21)
            gray = cv2.medianBlur(gray, 3)
            threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            binary = self._deskew_cv2(binary, cv2, np, warnings)
            return image_mod.fromarray(binary).convert("1").convert("RGB"), int(threshold), warnings

        warnings.append("OpenCV/NumPy not available; using Pillow-only cleanup.")
        gray = image_ops.grayscale(pil_image).filter(image_filter.MedianFilter(size=3))
        hist = gray.histogram()
        total = sum(hist)
        weighted = sum(i * count for i, count in enumerate(hist))
        threshold = int(weighted / max(total, 1))
        cleaned = gray.point(lambda p: 255 if p > threshold else 0).convert("1").convert("RGB")
        return cleaned, threshold, warnings

    def _deskew_cv2(self, binary: Any, cv2: Any, np: Any, warnings: List[str]) -> Any:
        coords = np.column_stack(np.where(binary < 255))
        if coords.size == 0:
            return binary
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        if abs(angle) < 0.2 or abs(angle) > 20:
            return binary
        h, w = binary.shape[:2]
        matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        warnings.append(f"Deskewed document by {angle:.2f} degrees.")
        return cv2.warpAffine(binary, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


class GeometryEngine:
    """Raster-to-vector reconstruction with OpenCV and Pillow fallbacks."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules = modules
        self.config = config

    def reconstruct_geometry(self, clean: CleanImage) -> List[GeometryPrimitive]:
        cv2 = self.modules.load("cv2")
        np = self.modules.load("numpy")
        if cv2 is not None and np is not None:
            return self._contours_with_cv2(clean, cv2, np)
        return self._components_with_pillow(clean)

    def _contours_with_cv2(self, clean: CleanImage, cv2: Any, np: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.image_path, cv2.IMREAD_GRAYSCALE)
        _, inv = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY_INV)
        contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        primitives: List[GeometryPrimitive] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.config.min_component_area:
                continue
            epsilon = self.config.simplify_tolerance
            approx = cv2.approxPolyDP(contour, epsilon, True)
            points = [(float(p[0][0]), float(p[0][1])) for p in approx]
            x, y, w, h = cv2.boundingRect(approx)
            kind = "polygon" if len(points) > 2 else "polyline"
            primitives.append(
                GeometryPrimitive(kind, points, (float(x), float(y), float(x + w), float(y + h)), confidence=min(0.99, area / 10_000.0))
            )
        primitives.sort(key=lambda p: (p.bbox[1], p.bbox[0]))
        return primitives

    def _components_with_pillow(self, clean: CleanImage) -> List[GeometryPrimitive]:
        image_mod = self.modules.load("PIL.Image")
        image = image_mod.open(clean.image_path).convert("1")
        width, height = image.size
        pix = image.load()
        visited = set()
        primitives: List[GeometryPrimitive] = []
        for y in range(height):
            for x in range(width):
                if pix[x, y] != 0 or (x, y) in visited:
                    continue
                bbox, count = self._flood_component(pix, width, height, x, y, visited)
                if count < self.config.min_component_area:
                    continue
                x0, y0, x1, y1 = bbox
                points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                primitives.append(GeometryPrimitive("polygon", points, bbox, confidence=0.35))
        return primitives

    def _flood_component(self, pix: Any, width: int, height: int, x: int, y: int, visited: set) -> Tuple[Tuple[float, float, float, float], int]:
        stack = [(x, y)]
        x0 = x1 = x
        y0 = y1 = y
        count = 0
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in visited or cx < 0 or cy < 0 or cx >= width or cy >= height or pix[cx, cy] != 0:
                continue
            visited.add((cx, cy))
            count += 1
            x0, x1 = min(x0, cx), max(x1, cx)
            y0, y1 = min(y0, cy), max(y1, cy)
            stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))
        return (float(x0), float(y0), float(x1 + 1), float(y1 + 1)), count


class OCREngine:
    """OCR wrapper using PaddleOCR first and Tesseract as fallback."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules = modules
        self.config = config

    def reconstruct_text(self, clean: CleanImage) -> Tuple[List[TextBlock], List[str]]:
        warnings: List[str] = []
        paddleocr = self.modules.load("paddleocr")
        if paddleocr is not None:
            return self._paddle(clean, paddleocr, warnings), warnings
        pytesseract = self.modules.load("pytesseract")
        if pytesseract is not None and shutil.which("tesseract"):
            return self._tesseract(clean, pytesseract, warnings), warnings
        warnings.append("No OCR backend available (PaddleOCR or Tesseract); text.json will be empty.")
        return [], warnings

    def _paddle(self, clean: CleanImage, paddleocr: Any, warnings: List[str]) -> List[TextBlock]:
        ocr = paddleocr.PaddleOCR(use_angle_cls=True, lang=self.config.ocr_languages, show_log=False)
        results = ocr.ocr(clean.image_path, cls=True)
        blocks: List[TextBlock] = []
        for page in results or []:
            for item in page or []:
                box, payload = item
                text, confidence = payload
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                blocks.append(TextBlock(str(text), (min(xs), min(ys), max(xs), max(ys)), float(confidence)))
        return blocks

    def _tesseract(self, clean: CleanImage, pytesseract: Any, warnings: List[str]) -> List[TextBlock]:
        image_mod = self.modules.load("PIL.Image")
        image = image_mod.open(clean.image_path)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        blocks: List[TextBlock] = []
        for i, text in enumerate(data.get("text", [])):
            text = text.strip()
            if not text:
                continue
            conf = float(data["conf"][i]) if str(data["conf"][i]).replace(".", "", 1).lstrip("-").isdigit() else 0.0
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            blocks.append(TextBlock(text, (float(x), float(y), float(x + w), float(y + h)), max(0.0, conf / 100.0)))
        return blocks


class FontEngine:
    """Simple font inference for reconstructed OCR blocks."""

    def enrich(self, blocks: List[TextBlock]) -> List[TextBlock]:
        for block in blocks:
            x0, y0, x1, y1 = block.bbox
            block.font_size = max(8.0, min(72.0, (y1 - y0) * 0.85))
            block.font_family = "DejaVu Sans"
        return blocks


class SVGExporter:
    """SVG writer that uses only the standard library for maximum portability."""

    def export(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> None:
        def esc(value: str) -> str:
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="white"/>',
            '<g id="geometry" fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round">',
        ]
        for primitive in geometry:
            if not primitive.points:
                continue
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in primitive.points)
            if primitive.kind == "polygon":
                lines.append(f'<polygon points="{points}" stroke-width="{primitive.stroke_width:.2f}"/>')
            else:
                lines.append(f'<polyline points="{points}" stroke-width="{primitive.stroke_width:.2f}"/>')
        lines.append("</g>")
        lines.append('<g id="text" fill="black" stroke="none">')
        for block in text:
            x0, y0, _x1, y1 = block.bbox
            lines.append(
                f'<text x="{x0:.2f}" y="{y1:.2f}" font-family="{esc(block.font_family)}" font-size="{block.font_size:.2f}">{esc(block.text)}</text>'
            )
        lines.extend(["</g>", "</svg>"])
        path.write_text("\n".join(lines), encoding="utf-8")


class Renderer:
    """High-resolution preview renderer backed by Pillow."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules = modules
        self.config = config

    def render_png(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock], target_long_edge: int) -> Tuple[int, int]:
        image_mod = self.modules.load("PIL.Image")
        image_draw = self.modules.load("PIL.ImageDraw")
        image_font = self.modules.load("PIL.ImageFont")
        if image_mod is None or image_draw is None:
            raise RuntimeError("Pillow is required for PNG rendering.")
        scale = target_long_edge / max(width, height)
        out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
        if out_w * out_h > self.config.render_max_pixels:
            scale = math.sqrt(self.config.render_max_pixels / (width * height))
            out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
        canvas = image_mod.new("RGB", (out_w, out_h), "white")
        draw = image_draw.Draw(canvas)
        for primitive in geometry:
            pts = [(x * scale, y * scale) for x, y in primitive.points]
            if len(pts) >= 2:
                draw.line(pts + ([pts[0]] if primitive.kind == "polygon" else []), fill="black", width=max(1, int(primitive.stroke_width * scale)))
        for block in text:
            x0, y0, _x1, y1 = block.bbox
            font = image_font.load_default() if image_font else None
            draw.text((x0 * scale, y0 * scale), block.text, fill="black", font=font)
        canvas.save(path)
        return out_w, out_h


class PDFExporter:
    """PDF export through ReportLab when installed, otherwise a Pillow PDF."""

    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, png_path: Path) -> Optional[str]:
        reportlab_canvas = self.modules.load("reportlab.pdfgen.canvas")
        image_reader = self.modules.load("reportlab.lib.utils")
        image_mod = self.modules.load("PIL.Image")
        if reportlab_canvas is not None and image_reader is not None and image_mod is not None:
            img = image_mod.open(png_path)
            canvas = reportlab_canvas.Canvas(str(path), pagesize=img.size)
            canvas.drawImage(image_reader.ImageReader(img), 0, 0, width=img.width, height=img.height)
            canvas.save()
            return None
        if image_mod is not None:
            image_mod.open(png_path).convert("RGB").save(path, "PDF", resolution=300.0)
            return "ReportLab not available; wrote raster PDF via Pillow."
        return "PDF export skipped because neither ReportLab nor Pillow PDF is available."


class DOCXExporter:
    """DOCX export through python-docx."""

    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, png_path: Path, text: List[TextBlock]) -> Optional[str]:
        docx = self.modules.load("docx")
        if docx is None:
            return "python-docx not available; DOCX export skipped."
        document = docx.Document()
        document.add_heading("Self Engine reconstruction", level=1)
        document.add_picture(str(png_path), width=docx.shared.Inches(6.5))
        if text:
            document.add_heading("Recognized text", level=2)
            for block in text:
                document.add_paragraph(block.text)
        document.save(str(path))
        return None


class QualityEngine:
    """Produces a compact quality report for the run."""

    def report(self, clean: CleanImage, geometry: List[GeometryPrimitive], text: List[TextBlock], warnings: List[str], elapsed: float) -> Dict[str, Any]:
        return {
            "source": clean.source_path,
            "clean_image": clean.image_path,
            "width": clean.width,
            "height": clean.height,
            "threshold": clean.threshold,
            "geometry_count": len(geometry),
            "text_count": len(text),
            "average_geometry_confidence": round(sum(p.confidence for p in geometry) / max(len(geometry), 1), 4),
            "average_text_confidence": round(sum(t.confidence for t in text) / max(len(text), 1), 4),
            "warnings": warnings,
            "elapsed_seconds": round(elapsed, 3),
        }


class SelfEngine:
    """Facade that orchestrates cleaning, geometry, OCR, rendering, and export."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or EngineConfig()
        self.modules = OptionalModules()
        self.cleaner = ImageCleaner(self.modules, self.config)
        self.geometry_engine = GeometryEngine(self.modules, self.config)
        self.ocr_engine = OCREngine(self.modules, self.config)
        self.font_engine = FontEngine()
        self.renderer = Renderer(self.modules, self.config)
        self.svg_exporter = SVGExporter()
        self.pdf_exporter = PDFExporter(self.modules)
        self.docx_exporter = DOCXExporter(self.modules)
        self.quality_engine = QualityEngine()

    def run(self, image: Optional[str] = None, image_path: Optional[str] = None, paper: Optional[str] = None, dpi: Optional[int] = None, output: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Run the complete pipeline and write artifacts to runtime_dir.

        Args:
            image: Input image/PDF path. This keyword matches the requested API.
            image_path: Alias for image, useful for programmatic callers.
            paper: Paper preset label stored in configuration metadata.
            dpi: Target logical DPI; high values are accepted, with preview size
                guarded by render_max_pixels to fit notebook memory limits.
            output: Iterable containing any of: png, svg, pdf, docx.
        """
        start = time.time()
        source = image or image_path
        if not source:
            raise ValueError("Pass image='/path/to/photo.jpg' or image_path='/path/to/photo.jpg'.")
        if paper:
            self.config.paper = paper
        if dpi:
            self.config.dpi = int(dpi)
        if output:
            self.config.output = tuple(output)

        runtime_dir = Path(self.config.runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        warnings: List[str] = []

        clean = self.clean_image(source)
        geometry = self.reconstruct_geometry(clean)
        text = self.reconstruct_text(clean)
        text = self.font_engine.enrich(text)
        warnings.extend(clean.warnings)
        artifacts = self.render(geometry, text, clean)

        geometry_json = runtime_dir / "geometry.json"
        text_json = runtime_dir / "text.json"
        report_json = runtime_dir / "report.json"
        geometry_json.write_text(json.dumps([asdict(p) for p in geometry], ensure_ascii=False, indent=2), encoding="utf-8")
        text_json.write_text(json.dumps([asdict(t) for t in text], ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["geometry"] = str(geometry_json)
        artifacts["text"] = str(text_json)
        warnings.extend(self._last_render_warnings)
        report = self.quality_engine.report(clean, geometry, text, warnings, time.time() - start)
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["report"] = str(report_json)
        return asdict(RenderResult(str(runtime_dir), artifacts, len(geometry), len(text), warnings))

    def clean_image(self, image_path: str) -> CleanImage:
        return self.cleaner.clean_image(image_path, Path(self.config.runtime_dir))

    def reconstruct_geometry(self, clean: CleanImage) -> List[GeometryPrimitive]:
        return self.geometry_engine.reconstruct_geometry(clean)

    def reconstruct_text(self, clean: CleanImage) -> List[TextBlock]:
        text, warnings = self.ocr_engine.reconstruct_text(clean)
        self._ocr_warnings = warnings
        return text

    def render(self, geometry: List[GeometryPrimitive], text: List[TextBlock], clean: CleanImage) -> Dict[str, str]:
        runtime_dir = Path(self.config.runtime_dir)
        requested = {item.lower() for item in self.config.output}
        artifacts: Dict[str, str] = {}
        warnings: List[str] = list(getattr(self, "_ocr_warnings", []))
        self._last_render_warnings = warnings

        if "svg" in requested:
            svg_path = runtime_dir / "drawing.svg"
            self.svg_exporter.export(svg_path, clean.width, clean.height, geometry, text)
            artifacts["svg"] = str(svg_path)

        png_path = runtime_dir / "preview_8k.png"
        if "png" in requested or "pdf" in requested or "docx" in requested:
            self.renderer.render_png(png_path, clean.width, clean.height, geometry, text, 8192)
            artifacts["png_8k"] = str(png_path)
            png16_path = runtime_dir / "preview_16k.png"
            self.renderer.render_png(png16_path, clean.width, clean.height, geometry, text, 16384)
            artifacts["png_16k"] = str(png16_path)

        if "pdf" in requested:
            pdf_path = runtime_dir / "drawing.pdf"
            warning = self.pdf_exporter.export(pdf_path, png_path)
            if warning:
                warnings.append(warning)
            if pdf_path.exists():
                artifacts["pdf"] = str(pdf_path)

        if "docx" in requested:
            docx_path = runtime_dir / "drawing.docx"
            warning = self.docx_exporter.export(docx_path, png_path, text)
            if warning:
                warnings.append(warning)
            if docx_path.exists():
                artifacts["docx"] = str(docx_path)

        return artifacts


# Backward-compatible alias with the requested minimal class shape in mind.
Engine = SelfEngine


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self Engine image/PDF reconstruction pipeline")
    parser.add_argument("image", nargs="?", help="Input raster image or PDF")
    parser.add_argument("--paper", default="A3", help="Paper preset metadata, e.g. A3 or A4")
    parser.add_argument("--dpi", type=int, default=1200, help="Target logical DPI")
    parser.add_argument("--output", nargs="+", default=["png", "svg", "pdf", "docx"], help="Artifacts to export")
    parser.add_argument("--runtime-dir", default="/mnt/data/runtime", help="Output directory")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if not args.image:
        print("Provide an input file, for example: python self_engine.py /mnt/data/photo.jpg", file=sys.stderr)
        return 2
    engine = SelfEngine(EngineConfig(paper=args.paper, dpi=args.dpi, output=tuple(args.output), runtime_dir=args.runtime_dir))
    result = engine.run(image=args.image)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
