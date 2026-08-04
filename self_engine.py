#!/usr/bin/env python3
"""Self Engine MVP: scanned/photo architectural drawing to CAD-style outputs.

The module is intentionally self-contained for notebook and ChatGPT Python Tool
usage, but the internal architecture remains modular: each stage is represented
by a class that can be replaced through dependency injection later.
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
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class EngineConfig:
    """Runtime configuration and geometric thresholds."""

    paper: str = "A3"
    dpi: int = 1200
    output: Sequence[str] = ("png", "svg", "pdf", "docx", "dxf")
    runtime_dir: str = "/mnt/data/runtime"
    render_max_pixels: int = 140_000_000
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
    adaptive_block_size: int = 41
    adaptive_c: int = 11
    clahe_clip_limit: float = 2.0
    morphology_kernel: int = 3
    hough_threshold: int = 45
    hough_min_line_length: int = 28
    hough_max_line_gap: int = 12
    snap_distance: float = 10.0
    angle_tolerance_degrees: float = 4.0
    merge_distance: float = 8.0
    min_line_length: float = 14.0
    room_close_tolerance: float = 12.0
    circle_min_radius: int = 8
    circle_max_radius: int = 240
    debug: bool = True


@dataclass
class CleanImage:
    source_path: str
    image_path: str
    threshold_path: str
    """Cleaned document image plus processing metadata."""

    source_path: str
    image_path: str
    width: int
    height: int
    mode: str
    threshold: int
    perspective_corrected: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class GeometryPrimitive:
    """Semantic CAD primitive used by exporters."""

    kind: str
    points: List[Point]
    bbox: BBox
    layer: str = "Geometry"
    """Serializable vector primitive reconstructed from document content."""

    kind: str
    points: List[Tuple[float, float]]
    bbox: Tuple[float, float, float, float]
    stroke: str = "#000000"
    fill: str = "none"
    stroke_width: float = 1.0
    confidence: float = 0.5
    radius: Optional[float] = None
    center: Optional[Point] = None
    start_angle: Optional[float] = None
    end_angle: Optional[float] = None
    label: Optional[str] = None


@dataclass
class GeometryResult:
    primitives: List[GeometryPrimitive]
    raw_segments: List[GeometryPrimitive]
    junctions: List[Point]
    rooms: List[GeometryPrimitive]
    metrics: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


@dataclass
class TextBlock:
    text: str
    bbox: BBox
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
    """Lazy optional dependency registry."""
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


class GeometryMath:
    @staticmethod
    def length(points: Sequence[Point]) -> float:
        if len(points) < 2:
            return 0.0
        return math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1])

    @staticmethod
    def bbox(points: Sequence[Point]) -> BBox:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def angle(points: Sequence[Point]) -> float:
        if len(points) < 2:
            return 0.0
        dx = points[-1][0] - points[0][0]
        dy = points[-1][1] - points[0][1]
        return math.degrees(math.atan2(dy, dx)) % 180.0

    @staticmethod
    def distance(a: Point, b: Point) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def line_intersection(a: GeometryPrimitive, b: GeometryPrimitive) -> Optional[Point]:
        if len(a.points) < 2 or len(b.points) < 2:
            return None
        (x1, y1), (x2, y2) = a.points[0], a.points[-1]
        (x3, y3), (x4, y4) = b.points[0], b.points[-1]
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(den) < 1e-6:
            return None
        px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
        py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
        if GeometryMath._inside(px, py, a.bbox, 2.0) and GeometryMath._inside(px, py, b.bbox, 2.0):
            return (px, py)
        return None

    @staticmethod
    def _inside(x: float, y: float, bbox: BBox, pad: float) -> bool:
        return bbox[0] - pad <= x <= bbox[2] + pad and bbox[1] - pad <= y <= bbox[3] + pad


class ImageCleaner:
    """Input detection, perspective correction, thresholding, and deskew."""
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
        cleaned, threshold_image, threshold, corrected, warnings = self._preprocess(pil)
        clean_path = runtime_dir / "clean.png"
        threshold_path = runtime_dir / "threshold.png"
        cleaned.save(clean_path)
        threshold_image.save(threshold_path)
        return CleanImage(str(source), str(clean_path), str(threshold_path), cleaned.width, cleaned.height, cleaned.mode, threshold, corrected, warnings)

    def _prepare_input(self, source: Path, runtime_dir: Path) -> Path:
        if source.suffix.lower() == ".pdf":
            pdf2image = self.modules.load("pdf2image")
            if pdf2image:
                pages = pdf2image.convert_from_path(str(source), first_page=1, last_page=1, dpi=300)
                out = runtime_dir / "pdf_page_1.png"
                pages[0].save(out)
                return out
            raise RuntimeError("PDF input requires pdf2image/poppler in this environment.")
        return source

    def _load_pillow(self, source: Path) -> Any:
        image_mod = self.modules.load("PIL.Image")
        if image_mod is None:
            raise RuntimeError("Pillow is required to load raster images.")
        return image_mod.open(source)

    def _preprocess(self, pil_image: Any) -> Tuple[Any, Any, int, bool, List[str]]:

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
        warnings: List[str] = []
        if np is None or cv2 is None:
            warnings.append("OpenCV/NumPy not available; using Pillow-only thresholding without perspective correction.")
            gray = image_ops.grayscale(pil_image).filter(image_filter.MedianFilter(size=3))
            hist = gray.histogram()
            threshold = int(sum(i * c for i, c in enumerate(hist)) / max(sum(hist), 1))
            binary = gray.point(lambda p: 255 if p > threshold else 0).convert("1").convert("RGB")
            return binary, binary, threshold, False, warnings

        arr = np.array(pil_image)
        corrected, perspective_corrected = self._perspective_correct(arr, cv2, np, warnings)
        gray = cv2.cvtColor(corrected, cv2.COLOR_RGB2GRAY)
        gray = cv2.fastNlMeansDenoising(gray, None, 7, 7, 21)
        clahe = cv2.createCLAHE(clipLimit=self.config.clahe_clip_limit, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        block = self.config.adaptive_block_size + (1 - self.config.adaptive_block_size % 2)
        binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, self.config.adaptive_c)
        kernel_size = max(1, self.config.morphology_kernel)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)
        cleaned = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
        cleaned = self._deskew(cleaned, cv2, np, warnings)
        threshold = int(cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
        return image_mod.fromarray(cleaned).convert("RGB"), image_mod.fromarray(cleaned).convert("RGB"), threshold, perspective_corrected, warnings

    def _perspective_correct(self, arr: Any, cv2: Any, np: Any, warnings: List[str]) -> Tuple[Any, bool]:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blur, 50, 150)
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return arr, False
        contours = sorted(contours, key=cv2.contourArea, reverse=True)[:8]
        h, w = arr.shape[:2]
        for contour in contours:
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
            area = cv2.contourArea(approx)
            if len(approx) == 4 and area > 0.20 * w * h:
                pts = approx.reshape(4, 2).astype("float32")
                rect = self._order_points(pts, np)
                width_a = np.linalg.norm(rect[2] - rect[3])
                width_b = np.linalg.norm(rect[1] - rect[0])
                height_a = np.linalg.norm(rect[1] - rect[2])
                height_b = np.linalg.norm(rect[0] - rect[3])
                max_w = int(max(width_a, width_b))
                max_h = int(max(height_a, height_b))
                if max_w < 20 or max_h < 20:
                    continue
                dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
                matrix = cv2.getPerspectiveTransform(rect, dst)
                warnings.append("Perspective corrected and cropped to detected page border.")
                return cv2.warpPerspective(arr, matrix, (max_w, max_h)), True
        return arr, False

    def _order_points(self, pts: Any, np: Any) -> Any:
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    def _deskew(self, binary: Any, cv2: Any, np: Any, warnings: List[str]) -> Any:
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
        if abs(angle) < 0.2 or abs(angle) > 15:
        if abs(angle) < 0.2 or abs(angle) > 20:
            return binary
        h, w = binary.shape[:2]
        matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        warnings.append(f"Deskewed document by {angle:.2f} degrees.")
        return cv2.warpAffine(binary, matrix, (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)


class SnapEngine:
    """Snapping, angle constraints, and line merge operations."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    def snap_and_merge(self, lines: List[GeometryPrimitive]) -> Tuple[List[GeometryPrimitive], Dict[str, int]]:
        metrics = {"input_lines": len(lines), "snapped_vertices": 0, "merged_lines": 0, "removed_gaps": 0}
        constrained = [self._constrain_angle(line) for line in lines if GeometryMath.length(line.points) >= self.config.min_line_length]
        snapped, snap_count = self._snap_endpoints(constrained)
        metrics["snapped_vertices"] = snap_count
        merged = self._merge_lines(snapped, metrics)
        metrics["output_lines"] = len(merged)
        return merged, metrics

    def _constrain_angle(self, line: GeometryPrimitive) -> GeometryPrimitive:
        p0, p1 = line.points[0], line.points[-1]
        angle = GeometryMath.angle(line.points)
        targets = [0.0, 45.0, 90.0, 135.0]
        target = min(targets, key=lambda a: min(abs(angle - a), 180 - abs(angle - a)))
        if min(abs(angle - target), 180 - abs(angle - target)) > self.config.angle_tolerance_degrees:
            return line
        length = GeometryMath.length(line.points)
        rad = math.radians(target)
        cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        dx, dy = math.cos(rad) * length / 2, math.sin(rad) * length / 2
        pts = [(cx - dx, cy - dy), (cx + dx, cy + dy)]
        return GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=line.confidence)

    def _snap_endpoints(self, lines: List[GeometryPrimitive]) -> Tuple[List[GeometryPrimitive], int]:
        cell = max(1.0, self.config.snap_distance)
        buckets: Dict[Tuple[int, int], List[Point]] = defaultdict(list)
        for line in lines:
            for point in (line.points[0], line.points[-1]):
                buckets[(int(point[0] // cell), int(point[1] // cell))].append(point)
        snapped_points: Dict[Point, Point] = {}
        changed = 0
        for key, points in list(buckets.items()):
            neighborhood: List[Point] = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighborhood.extend(buckets.get((key[0] + dx, key[1] + dy), []))
            for p in points:
                cluster = [q for q in neighborhood if GeometryMath.distance(p, q) <= self.config.snap_distance]
                if cluster:
                    centroid = (sum(q[0] for q in cluster) / len(cluster), sum(q[1] for q in cluster) / len(cluster))
                    snapped_points[p] = centroid
                    if GeometryMath.distance(p, centroid) > 0.1:
                        changed += 1
        out: List[GeometryPrimitive] = []
        for line in lines:
            pts = [snapped_points.get(line.points[0], line.points[0]), snapped_points.get(line.points[-1], line.points[-1])]
            out.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), line.layer, confidence=line.confidence))
        return out, changed

    def _merge_lines(self, lines: List[GeometryPrimitive], metrics: Dict[str, int]) -> List[GeometryPrimitive]:
        groups: Dict[Tuple[int, int], List[GeometryPrimitive]] = defaultdict(list)
        for line in lines:
            angle_bin = int(round(GeometryMath.angle(line.points) / max(self.config.angle_tolerance_degrees, 1.0)))
            p0, p1 = line.points[0], line.points[-1]
            if abs(p1[0] - p0[0]) >= abs(p1[1] - p0[1]):
                offset = int(round(((p0[1] + p1[1]) / 2) / max(self.config.merge_distance, 1.0)))
            else:
                offset = int(round(((p0[0] + p1[0]) / 2) / max(self.config.merge_distance, 1.0)))
            groups[(angle_bin, offset)].append(line)
        merged: List[GeometryPrimitive] = []
        for group in groups.values():
            merged.extend(self._merge_group(group, metrics))
        return merged

    def _merge_group(self, group: List[GeometryPrimitive], metrics: Dict[str, int]) -> List[GeometryPrimitive]:
        if not group:
            return []
        horizontal = abs(math.cos(math.radians(GeometryMath.angle(group[0].points)))) >= abs(math.sin(math.radians(GeometryMath.angle(group[0].points))))
        key_index = 0 if horizontal else 1
        group = sorted(group, key=lambda line: min(line.points[0][key_index], line.points[-1][key_index]))
        out: List[GeometryPrimitive] = []
        current = group[0]
        for line in group[1:]:
            c0, c1 = sorted([current.points[0][key_index], current.points[-1][key_index]])
            l0, l1 = sorted([line.points[0][key_index], line.points[-1][key_index]])
            if l0 <= c1 + self.config.merge_distance:
                pts = current.points + line.points
                start = min(pts, key=lambda p: p[key_index])
                end = max(pts, key=lambda p: p[key_index])
                fixed = (sum(p[1 - key_index] for p in pts) / len(pts))
                if horizontal:
                    new_pts = [(start[0], fixed), (end[0], fixed)]
                else:
                    new_pts = [(fixed, start[1]), (fixed, end[1])]
                current = GeometryPrimitive("line", new_pts, GeometryMath.bbox(new_pts), "Walls", confidence=max(current.confidence, line.confidence))
                metrics["merged_lines"] += 1
                if l0 > c1:
                    metrics["removed_gaps"] += 1
            else:
                out.append(current)
                current = line
        out.append(current)
        return out


class GeometryEngine:
    """CAD-oriented line/symbol reconstruction, not contour tracing."""
        return cv2.warpAffine(binary, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


class GeometryEngine:
    """Raster-to-vector reconstruction with OpenCV and Pillow fallbacks."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules = modules
        self.config = config
        self.snapper = SnapEngine(config)

    def reconstruct_geometry(self, clean: CleanImage, runtime_dir: Optional[Path] = None) -> GeometryResult:
        cv2 = self.modules.load("cv2")
        np = self.modules.load("numpy")
        warnings: List[str] = []
        if cv2 is not None and np is not None:
            raw = self._detect_lines_cv(clean, cv2, np)
            circles = self._detect_circles_cv(clean, cv2, np)
        else:
            warnings.append("OpenCV/NumPy not available; using projection-based fallback geometry.")
            raw = self._detect_lines_fallback(clean)
            circles = []
        merged, snap_metrics = self.snapper.snap_and_merge(raw)
        junctions = self._compute_junctions(merged)
        rooms = self._recognize_rooms(merged)
        doors, windows, symbols = self._recognize_symbols(merged, circles)
        primitives = merged + rooms + doors + windows + circles + symbols
        metrics = {**snap_metrics, "line_count": len(merged), "junction_count": len(junctions), "room_count": len(rooms), "circle_count": len(circles)}
        if runtime_dir and self.config.debug:
            self._write_segments(runtime_dir, raw, merged, junctions)
        return GeometryResult(primitives, raw, junctions, rooms, metrics, warnings)

    def _detect_lines_cv(self, clean: CleanImage, cv2: Any, np: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.threshold_path, cv2.IMREAD_GRAYSCALE)
        inv = 255 - img
        lsd = cv2.createLineSegmentDetector(0) if hasattr(cv2, "createLineSegmentDetector") else None
        segments = []
        if lsd is not None:
            detected = lsd.detect(inv)[0]
            if detected is not None:
                for row in detected.reshape(-1, 4):
                    segments.append(tuple(float(v) for v in row))
        if not segments:
            lines = cv2.HoughLinesP(inv, 1, math.pi / 180, self.config.hough_threshold, minLineLength=self.config.hough_min_line_length, maxLineGap=self.config.hough_max_line_gap)
            if lines is not None:
                for row in lines.reshape(-1, 4):
                    segments.append(tuple(float(v) for v in row))
        primitives: List[GeometryPrimitive] = []
        for x1, y1, x2, y2 in segments:
            pts = [(x1, y1), (x2, y2)]
            if GeometryMath.length(pts) >= self.config.min_line_length:
                primitives.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=0.85))
        return primitives

    def _detect_lines_fallback(self, clean: CleanImage) -> List[GeometryPrimitive]:
        image_mod = self.modules.load("PIL.Image")
        image = image_mod.open(clean.threshold_path).convert("1")
        w, h = image.size
        pix = image.load()
        lines: List[GeometryPrimitive] = []
        for y in range(h):
            run_start = None
            for x in range(w):
                black = pix[x, y] == 0
                if black and run_start is None:
                    run_start = x
                if (not black or x == w - 1) and run_start is not None:
                    end = x if black else x - 1
                    if end - run_start >= self.config.hough_min_line_length:
                        pts = [(float(run_start), float(y)), (float(end), float(y))]
                        lines.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=0.45))
                    run_start = None
        for x in range(w):
            run_start = None
            for y in range(h):
                black = pix[x, y] == 0
                if black and run_start is None:
                    run_start = y
                if (not black or y == h - 1) and run_start is not None:
                    end = y if black else y - 1
                    if end - run_start >= self.config.hough_min_line_length:
                        pts = [(float(x), float(run_start)), (float(x), float(end))]
                        lines.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=0.45))
                    run_start = None
        return lines

    def _detect_circles_cv(self, clean: CleanImage, cv2: Any, np: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.threshold_path, cv2.IMREAD_GRAYSCALE)
        circles = cv2.HoughCircles(img, cv2.HOUGH_GRADIENT, dp=1.2, minDist=24, param1=80, param2=24, minRadius=self.config.circle_min_radius, maxRadius=self.config.circle_max_radius)
        out: List[GeometryPrimitive] = []
        if circles is not None:
            for x, y, r in np.round(circles[0, :]).astype("int"):
                out.append(GeometryPrimitive("circle", [], (float(x - r), float(y - r), float(x + r), float(y + r)), "Symbols", confidence=0.75, radius=float(r), center=(float(x), float(y))))
        return out

    def _compute_junctions(self, lines: List[GeometryPrimitive]) -> List[Point]:
        cell = max(self.config.merge_distance * 8, 32.0)
        buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for idx, line in enumerate(lines):
            x0, y0, x1, y1 = line.bbox
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                    buckets[(gx, gy)].append(idx)
        seen = set()
        junctions: List[Point] = []
        for ids in buckets.values():
            for i, a_idx in enumerate(ids):
                for b_idx in ids[i + 1 :]:
                    key = (min(a_idx, b_idx), max(a_idx, b_idx))
                    if key in seen:
                        continue
                    seen.add(key)
                    p = GeometryMath.line_intersection(lines[a_idx], lines[b_idx])
                    if p is not None:
                        junctions.append(p)
        return junctions

    def _recognize_rooms(self, lines: List[GeometryPrimitive]) -> List[GeometryPrimitive]:
        horizontals = [l for l in lines if min(GeometryMath.angle(l.points), abs(180 - GeometryMath.angle(l.points))) <= self.config.angle_tolerance_degrees]
        verticals = [l for l in lines if abs(GeometryMath.angle(l.points) - 90) <= self.config.angle_tolerance_degrees]
        rooms: List[GeometryPrimitive] = []
        for top in horizontals:
            for bottom in horizontals:
                if bottom.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance:
                    continue
                x0 = max(top.bbox[0], bottom.bbox[0])
                x1 = min(top.bbox[2], bottom.bbox[2])
                if x1 - x0 < 40:
                    continue
                lefts = [v for v in verticals if abs(v.bbox[0] - x0) <= self.config.room_close_tolerance and v.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance and v.bbox[3] >= bottom.bbox[1] - self.config.room_close_tolerance]
                rights = [v for v in verticals if abs(v.bbox[0] - x1) <= self.config.room_close_tolerance and v.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance and v.bbox[3] >= bottom.bbox[1] - self.config.room_close_tolerance]
                if lefts and rights:
                    pts = [(x0, top.bbox[1]), (x1, top.bbox[1]), (x1, bottom.bbox[1]), (x0, bottom.bbox[1])]
                    rooms.append(GeometryPrimitive("room", pts, GeometryMath.bbox(pts), "Debug", stroke="#00aa00", confidence=0.7, label="closed_room"))
        return rooms[:200]

    def _recognize_symbols(self, lines: List[GeometryPrimitive], circles: List[GeometryPrimitive]) -> Tuple[List[GeometryPrimitive], List[GeometryPrimitive], List[GeometryPrimitive]]:
        doors: List[GeometryPrimitive] = []
        windows: List[GeometryPrimitive] = []
        symbols: List[GeometryPrimitive] = []
        for line in lines:
            length = GeometryMath.length(line.points)
            if 18 <= length <= 90:
                angle = GeometryMath.angle(line.points)
                if abs(angle - 45) <= 12 or abs(angle - 135) <= 12:
                    doors.append(GeometryPrimitive("door", line.points, line.bbox, "Doors", stroke="#8a4b08", confidence=0.45))
                elif abs(angle) <= 6 or abs(angle - 90) <= 6:
                    windows.append(GeometryPrimitive("window", line.points, line.bbox, "Windows", stroke="#0070c0", confidence=0.4))
        for circle in circles:
            symbols.append(GeometryPrimitive("symbol", [], circle.bbox, "Symbols", confidence=circle.confidence, center=circle.center, radius=circle.radius, label="circle_symbol"))
        return doors[:300], windows[:300], symbols[:300]

    def _write_segments(self, runtime_dir: Path, raw: List[GeometryPrimitive], merged: List[GeometryPrimitive], junctions: List[Point]) -> None:
        data = {"raw_segments": [asdict(p) for p in raw], "merged_segments": [asdict(p) for p in merged], "junctions": junctions}
        (runtime_dir / "segments.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


class OCREngine:
    """OCR wrapper; OCR data is separate from geometry."""

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
            return self._paddle(clean, paddleocr), warnings
        pytesseract = self.modules.load("pytesseract")
        if pytesseract is not None and shutil.which("tesseract"):
            return self._tesseract(clean, pytesseract), warnings
        warnings.append("No OCR backend available (PaddleOCR or Tesseract); text.json will be empty.")
        return [], warnings

    def _paddle(self, clean: CleanImage, paddleocr: Any) -> List[TextBlock]:
        ocr = paddleocr.PaddleOCR(use_angle_cls=True, lang=self.config.ocr_languages, show_log=False)
        blocks: List[TextBlock] = []
        for page in ocr.ocr(clean.image_path, cls=True) or []:
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

    def _tesseract(self, clean: CleanImage, pytesseract: Any) -> List[TextBlock]:
    def _tesseract(self, clean: CleanImage, pytesseract: Any, warnings: List[str]) -> List[TextBlock]:
        image_mod = self.modules.load("PIL.Image")
        image = image_mod.open(clean.image_path)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        blocks: List[TextBlock] = []
        for i, text in enumerate(data.get("text", [])):
            text = text.strip()
            if not text:
                continue
            conf_raw = str(data["conf"][i])
            conf = float(conf_raw) / 100.0 if conf_raw.replace(".", "", 1).lstrip("-").isdigit() else 0.0
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            blocks.append(TextBlock(text, (float(x), float(y), float(x + w), float(y + h)), max(0.0, conf)))
            conf = float(data["conf"][i]) if str(data["conf"][i]).replace(".", "", 1).lstrip("-").isdigit() else 0.0
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            blocks.append(TextBlock(text, (float(x), float(y), float(x + w), float(y + h)), max(0.0, conf / 100.0)))
        return blocks


class FontEngine:
    def enrich(self, blocks: List[TextBlock]) -> List[TextBlock]:
        for block in blocks:
            block.font_size = max(8.0, min(72.0, (block.bbox[3] - block.bbox[1]) * 0.85))
    """Simple font inference for reconstructed OCR blocks."""

    def enrich(self, blocks: List[TextBlock]) -> List[TextBlock]:
        for block in blocks:
            x0, y0, x1, y1 = block.bbox
            block.font_size = max(8.0, min(72.0, (y1 - y0) * 0.85))
            block.font_family = "DejaVu Sans"
        return blocks


class SVGExporter:
    layers = ["Geometry", "Walls", "Doors", "Windows", "Symbols", "Text", "Debug"]
    """SVG writer that uses only the standard library for maximum portability."""

    def export(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> None:
        def esc(value: str) -> str:
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        by_layer: Dict[str, List[GeometryPrimitive]] = defaultdict(list)
        for primitive in geometry:
            by_layer[primitive.layer or "Geometry"].append(primitive)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>']
        for layer in self.layers:
            if layer == "Text":
                lines.append('<g id="Text" inkscape:groupmode="layer" inkscape:label="Text" fill="black" stroke="none" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">')
                for block in text:
                    lines.append(f'<text x="{block.bbox[0]:.2f}" y="{block.bbox[3]:.2f}" font-family="{esc(block.font_family)}" font-size="{block.font_size:.2f}">{esc(block.text)}</text>')
                lines.append("</g>")
                continue
            lines.append(f'<g id="{layer}" inkscape:groupmode="layer" inkscape:label="{layer}" fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">')
            for primitive in by_layer.get(layer, []):
                lines.append(self._primitive_svg(primitive))
            lines.append("</g>")
        lines.append("</svg>")
        path.write_text("\n".join(lines), encoding="utf-8")

    def _primitive_svg(self, primitive: GeometryPrimitive) -> str:
        stroke = primitive.stroke
        if primitive.kind in {"line", "door", "window"} and len(primitive.points) >= 2:
            p0, p1 = primitive.points[0], primitive.points[-1]
            return f'<line x1="{p0[0]:.2f}" y1="{p0[1]:.2f}" x2="{p1[0]:.2f}" y2="{p1[1]:.2f}" stroke="{stroke}" stroke-width="{primitive.stroke_width:.2f}"/>'
        if primitive.kind in {"room", "polyline"} and primitive.points:
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in primitive.points)
            return f'<polyline points="{points} {primitive.points[0][0]:.2f},{primitive.points[0][1]:.2f}" stroke="{stroke}" stroke-width="{primitive.stroke_width:.2f}"/>'
        if primitive.kind in {"circle", "symbol"} and primitive.center and primitive.radius:
            return f'<circle cx="{primitive.center[0]:.2f}" cy="{primitive.center[1]:.2f}" r="{primitive.radius:.2f}" stroke="{stroke}" stroke-width="{primitive.stroke_width:.2f}"/>'
        if primitive.points:
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in primitive.points)
            return f'<polyline points="{points}" stroke="{stroke}" stroke-width="{primitive.stroke_width:.2f}"/>'
        return f'<!-- unsupported primitive {primitive.kind} -->'


class Renderer:

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

    def render_png(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock], target_long_edge: int, junctions: Optional[List[Point]] = None) -> Tuple[int, int]:
    def render_png(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock], target_long_edge: int) -> Tuple[int, int]:
        image_mod = self.modules.load("PIL.Image")
        image_draw = self.modules.load("PIL.ImageDraw")
        image_font = self.modules.load("PIL.ImageFont")
        if image_mod is None or image_draw is None:
            raise RuntimeError("Pillow is required for PNG rendering.")
        scale = target_long_edge / max(width, height)
        if width * height * scale * scale > self.config.render_max_pixels:
            scale = math.sqrt(self.config.render_max_pixels / max(width * height, 1))
        out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
        canvas = image_mod.new("RGB", (out_w, out_h), "white")
        draw = image_draw.Draw(canvas)
        colors = {"Walls": "black", "Doors": "#8a4b08", "Windows": "#0070c0", "Symbols": "#555555", "Debug": "#00aa00"}
        for primitive in geometry:
            color = colors.get(primitive.layer, "black")
            if primitive.kind in {"line", "door", "window"} and len(primitive.points) >= 2:
                pts = [(x * scale, y * scale) for x, y in primitive.points]
                draw.line(pts, fill=color, width=max(1, int(primitive.stroke_width * scale)))
            elif primitive.kind in {"room", "polyline"} and primitive.points:
                pts = [(x * scale, y * scale) for x, y in primitive.points]
                draw.line(pts + [pts[0]], fill=color, width=max(1, int(scale)))
            elif primitive.kind in {"circle", "symbol"} and primitive.center and primitive.radius:
                x, y = primitive.center
                r = primitive.radius
                draw.ellipse(((x - r) * scale, (y - r) * scale, (x + r) * scale, (y + r) * scale), outline=color, width=max(1, int(scale)))
        if junctions:
            for x, y in junctions:
                r = max(2, int(3 * scale))
                draw.ellipse((x * scale - r, y * scale - r, x * scale + r, y * scale + r), fill="red")
        font = image_font.load_default() if image_font else None
        for block in text:
            draw.text((block.bbox[0] * scale, block.bbox[1] * scale), block.text, fill="black", font=font)
        canvas.save(path)
        return out_w, out_h

    def render_debug_from_clean(self, path: Path, clean_path: str, overlay: List[GeometryPrimitive], junctions: Optional[List[Point]] = None) -> None:
        image_mod = self.modules.load("PIL.Image")
        image_draw = self.modules.load("PIL.ImageDraw")
        image = image_mod.open(clean_path).convert("RGB")
        draw = image_draw.Draw(image)
        for primitive in overlay:
            if len(primitive.points) >= 2:
                draw.line(primitive.points, fill="red" if primitive.layer == "Walls" else "blue", width=2)
        if junctions:
            for x, y in junctions:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="lime")
        image.save(path)


class PDFExporter:
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


class DXFExporter:
    """DXF export with ezdxf, falling back to minimal ASCII DXF."""

    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> Optional[str]:
        ezdxf = self.modules.load("ezdxf")
        if ezdxf is not None:
            doc = ezdxf.new("R2010")
            msp = doc.modelspace()
            for primitive in geometry:
                self._add_ezdxf(msp, primitive)
            for block in text:
                msp.add_text(block.text, dxfattribs={"height": block.font_size, "layer": "Text"}).set_placement((block.bbox[0], block.bbox[1]))
            doc.saveas(path)
            return None
        self._write_ascii_dxf(path, geometry, text)
        return "ezdxf not available; wrote minimal ASCII DXF fallback."

    def _add_ezdxf(self, msp: Any, primitive: GeometryPrimitive) -> None:
        attrs = {"layer": primitive.layer}
        if primitive.kind in {"line", "door", "window"} and len(primitive.points) >= 2:
            msp.add_line(primitive.points[0], primitive.points[-1], dxfattribs=attrs)
        elif primitive.kind in {"room", "polyline"} and primitive.points:
            msp.add_lwpolyline(primitive.points, close=True, dxfattribs=attrs)
        elif primitive.kind in {"circle", "symbol"} and primitive.center and primitive.radius:
            msp.add_circle(primitive.center, primitive.radius, dxfattribs=attrs)
        elif primitive.kind == "arc" and primitive.center and primitive.radius:
            msp.add_arc(primitive.center, primitive.radius, primitive.start_angle or 0, primitive.end_angle or 0, dxfattribs=attrs)

    def _write_ascii_dxf(self, path: Path, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> None:
        lines = ["0", "SECTION", "2", "ENTITIES"]
        for primitive in geometry:
            if primitive.kind in {"line", "door", "window"} and len(primitive.points) >= 2:
                p0, p1 = primitive.points[0], primitive.points[-1]
                lines += ["0", "LINE", "8", primitive.layer, "10", f"{p0[0]:.3f}", "20", f"{-p0[1]:.3f}", "11", f"{p1[0]:.3f}", "21", f"{-p1[1]:.3f}"]
            elif primitive.kind in {"room", "polyline"} and primitive.points:
                lines += ["0", "LWPOLYLINE", "8", primitive.layer, "90", str(len(primitive.points)), "70", "1"]
                for x, y in primitive.points:
                    lines += ["10", f"{x:.3f}", "20", f"{-y:.3f}"]
            elif primitive.kind in {"circle", "symbol"} and primitive.center and primitive.radius:
                lines += ["0", "CIRCLE", "8", primitive.layer, "10", f"{primitive.center[0]:.3f}", "20", f"{-primitive.center[1]:.3f}", "40", f"{primitive.radius:.3f}"]
        for block in text:
            lines += ["0", "TEXT", "8", "Text", "10", f"{block.bbox[0]:.3f}", "20", f"{-block.bbox[1]:.3f}", "40", f"{block.font_size:.3f}", "1", block.text]
        lines += ["0", "ENDSEC", "0", "EOF"]
        path.write_text("\n".join(lines), encoding="utf-8")


class QualityEngine:
    def report(self, clean: CleanImage, geometry: GeometryResult, text: List[TextBlock], warnings: List[str], elapsed: float) -> Dict[str, Any]:
        lengths = [GeometryMath.length(p.points) for p in geometry.primitives if p.kind == "line"]
        return {
            "source": clean.source_path,
            "clean_image": clean.image_path,
            "threshold_image": clean.threshold_path,
            "width": clean.width,
            "height": clean.height,
            "threshold": clean.threshold,
            "perspective_corrected": clean.perspective_corrected,
            "geometry_precision": {"snap_distance": self._round_metric(lengths), "angle_tolerance_degrees": None},
            "line_count": geometry.metrics.get("line_count", 0),
            "merged_lines": geometry.metrics.get("merged_lines", 0),
            "snapped_vertices": geometry.metrics.get("snapped_vertices", 0),
            "junction_count": geometry.metrics.get("junction_count", 0),
            "room_count": geometry.metrics.get("room_count", 0),
            "ocr_confidence": round(sum(t.confidence for t in text) / max(len(text), 1), 4),
            "text_count": len(text),
            "warnings": warnings,
            "processing_time_seconds": round(elapsed, 3),
            "metrics": geometry.metrics,
        }

    def _round_metric(self, values: List[float]) -> Optional[float]:
        if not values:
            return None
        return round(sum(values) / len(values), 3)


class SelfEngine:
    """Facade orchestrating the full image/PDF to CAD-style document pipeline."""
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
        self.dxf_exporter = DXFExporter(self.modules)
        self.quality_engine = QualityEngine()
        self._ocr_warnings: List[str] = []
        self._render_warnings: List[str] = []

    def run(self, image: Optional[str] = None, image_path: Optional[str] = None, paper: Optional[str] = None, dpi: Optional[int] = None, output: Optional[Sequence[str]] = None) -> Dict[str, Any]:
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
        clean = self.clean_image(source)
        geometry = self.reconstruct_geometry(clean)
        text = self.font_engine.enrich(self.reconstruct_text(clean))
        artifacts = self.render(geometry, text, clean)
        geometry_json = runtime_dir / "geometry.json"
        text_json = runtime_dir / "text.json"
        report_json = runtime_dir / "report.json"
        geometry_json.write_text(json.dumps([asdict(p) for p in geometry.primitives], ensure_ascii=False, indent=2), encoding="utf-8")
        text_json.write_text(json.dumps([asdict(t) for t in text], ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["geometry"] = str(geometry_json)
        artifacts["text"] = str(text_json)
        warnings = clean.warnings + geometry.warnings + self._ocr_warnings + self._render_warnings
        report = self.quality_engine.report(clean, geometry, text, warnings, time.time() - start)
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["report"] = str(report_json)
        return asdict(RenderResult(str(runtime_dir), artifacts, len(geometry.primitives), len(text), warnings))

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

    def reconstruct_geometry(self, clean: CleanImage) -> GeometryResult:
        return self.geometry_engine.reconstruct_geometry(clean, Path(self.config.runtime_dir))
    def reconstruct_geometry(self, clean: CleanImage) -> List[GeometryPrimitive]:
        return self.geometry_engine.reconstruct_geometry(clean)

    def reconstruct_text(self, clean: CleanImage) -> List[TextBlock]:
        text, warnings = self.ocr_engine.reconstruct_text(clean)
        self._ocr_warnings = warnings
        return text

    def render(self, geometry: GeometryResult | List[GeometryPrimitive], text: List[TextBlock], clean: CleanImage) -> Dict[str, str]:
        if isinstance(geometry, list):
            geometry = GeometryResult(geometry, geometry, [], [], {"line_count": len([p for p in geometry if p.kind == "line"])})
        runtime_dir = Path(self.config.runtime_dir)
        requested = {item.lower() for item in self.config.output}
        artifacts: Dict[str, str] = {}
        self._render_warnings = []
        if "svg" in requested:
            svg_path = runtime_dir / "drawing.svg"
            self.svg_exporter.export(svg_path, clean.width, clean.height, geometry.primitives, text)
            artifacts["svg"] = str(svg_path)
        png_path = runtime_dir / "preview_8k.png"
        if {"png", "pdf", "docx"} & requested:
            self.renderer.render_png(png_path, clean.width, clean.height, geometry.primitives, text, 8192)
            artifacts["png_8k"] = str(png_path)
            png16_path = runtime_dir / "preview_16k.png"
            self.renderer.render_png(png16_path, clean.width, clean.height, geometry.primitives, text, 16384)
            artifacts["png_16k"] = str(png16_path)
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
                self._render_warnings.append(warning)
            if pdf_path.exists():
                artifacts["pdf"] = str(pdf_path)
                warnings.append(warning)
            if pdf_path.exists():
                artifacts["pdf"] = str(pdf_path)

        if "docx" in requested:
            docx_path = runtime_dir / "drawing.docx"
            warning = self.docx_exporter.export(docx_path, png_path, text)
            if warning:
                self._render_warnings.append(warning)
            if docx_path.exists():
                artifacts["docx"] = str(docx_path)
        if "dxf" in requested:
            dxf_path = runtime_dir / "drawing.dxf"
            warning = self.dxf_exporter.export(dxf_path, geometry.primitives, text)
            if warning:
                self._render_warnings.append(warning)
            if dxf_path.exists():
                artifacts["dxf"] = str(dxf_path)
        if self.config.debug:
            self._write_debug_artifacts(runtime_dir, clean, geometry, artifacts)
        return artifacts

    def _write_debug_artifacts(self, runtime_dir: Path, clean: CleanImage, geometry: GeometryResult, artifacts: Dict[str, str]) -> None:
        lines_path = runtime_dir / "lines.png"
        junctions_path = runtime_dir / "junctions.png"
        snap_path = runtime_dir / "snap.png"
        self.renderer.render_debug_from_clean(lines_path, clean.image_path, geometry.raw_segments)
        self.renderer.render_debug_from_clean(junctions_path, clean.image_path, geometry.primitives, geometry.junctions)
        self.renderer.render_debug_from_clean(snap_path, clean.image_path, [p for p in geometry.primitives if p.kind == "line"])
        artifacts["debug_lines"] = str(lines_path)
        artifacts["debug_junctions"] = str(junctions_path)
        artifacts["debug_snap"] = str(snap_path)
        artifacts["threshold"] = clean.threshold_path
        artifacts["segments"] = str(runtime_dir / "segments.json")


                warnings.append(warning)
            if docx_path.exists():
                artifacts["docx"] = str(docx_path)

        return artifacts


# Backward-compatible alias with the requested minimal class shape in mind.
Engine = SelfEngine


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self Engine image/PDF architectural reconstruction pipeline")
    parser.add_argument("image", nargs="?", help="Input raster image or PDF")
    parser.add_argument("--paper", default="A3", help="Paper preset metadata, e.g. A3 or A4")
    parser.add_argument("--dpi", type=int, default=1200, help="Target logical DPI")
    parser.add_argument("--output", nargs="+", default=["png", "svg", "pdf", "docx", "dxf"], help="Artifacts to export")
    parser.add_argument("--runtime-dir", default="/mnt/data/runtime", help="Output directory")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug artifacts")
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
    engine = SelfEngine(EngineConfig(paper=args.paper, dpi=args.dpi, output=tuple(args.output), runtime_dir=args.runtime_dir, debug=not args.no_debug))
    print(json.dumps(engine.run(image=args.image), ensure_ascii=False, indent=2))
    engine = SelfEngine(EngineConfig(paper=args.paper, dpi=args.dpi, output=tuple(args.output), runtime_dir=args.runtime_dir))
    result = engine.run(image=args.image)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
