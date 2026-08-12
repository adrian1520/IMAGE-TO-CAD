#!/usr/bin/env python3
"""Deterministic MVP for converting technical drawing images/PDFs to CAD artifacts.

The pipeline favors semantic geometry reconstruction (orthogonal lines, circles,
junctions, rooms, text blocks) over contour tracing.  Heavy dependencies are
optional and loaded lazily so the MVP remains usable in restricted runtimes.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import shutil
import sys
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]


@dataclass
class EngineConfig:
    """Configuration-driven thresholds for deterministic reconstruction."""

    paper: str = "A3"
    dpi: int = 1200
    output: Sequence[str] = ("png", "svg", "pdf", "docx", "dxf")
    runtime_dir: str = "/mnt/data/runtime"
    render_max_pixels: int = 140_000_000
    min_component_area: int = 24
    ocr_languages: str = "en"
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
    photo_shadow_kernel_ratio: float = 0.035
    fold_line_suppression_width: int = 9
    door_arc_min_radius: int = 12
    door_arc_max_radius: int = 120
    window_parallel_distance: float = 10.0
    circle_min_radius: int = 8
    circle_max_radius: int = 240
    debug: bool = True
    upscale_factor: int = 8
    pdf_paper_size: str = "A4"
    symbol_text_confidence_threshold: float = 0.82
    symbol_text_crop_margin: int = 48


@dataclass
class CleanImage:
    """Cleaned image plus metadata from preprocessing."""

    source_path: str
    image_path: str
    threshold_path: str
    width: int
    height: int
    mode: str
    threshold: int
    perspective_corrected: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class GeometryPrimitive:
    """Serializable CAD primitive."""

    kind: str
    points: List[Point]
    bbox: BBox
    layer: str = "Geometry"
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
    confidence: float
    font_size: float = 12.0
    font_family: str = "DejaVu Sans"


@dataclass
class OCRCandidate:
    text: str
    variant: str
    confidence: float
    normalized_text: str
    pattern: Optional[str] = None
    dictionary_match: bool = False
    corrected: bool = False


@dataclass
class SymbolTextResult:
    symbol_id: str
    bbox: BBox
    text_crop_bbox: BBox
    ocr_candidates: List[OCRCandidate]
    selected_text: Optional[str]
    pattern: Optional[str]
    qc_status: str
    rejection_reason: Optional[str] = None


@dataclass
class RenderResult:
    runtime_dir: str
    artifacts: Dict[str, str]
    geometry_count: int
    text_count: int
    warnings: List[str]


class OptionalModules:
    """Lazy optional dependency registry."""

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def load(self, module_name: str) -> Any:
        if module_name not in self._cache:
            available = True
            parts = module_name.split(".")
            for index in range(1, len(parts) + 1):
                if importlib.util.find_spec(".".join(parts[:index])) is None:
                    available = False
                    break
            if available:
                try:
                    self._cache[module_name] = importlib.import_module(module_name)
                except Exception:
                    self._cache[module_name] = None
            else:
                self._cache[module_name] = None
        return self._cache[module_name]


class GeometryMath:
    @staticmethod
    def length(points: Sequence[Point]) -> float:
        return math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]) if len(points) >= 2 else 0.0

    @staticmethod
    def bbox(points: Sequence[Point]) -> BBox:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def angle(points: Sequence[Point]) -> float:
        if len(points) < 2:
            return 0.0
        return math.degrees(math.atan2(points[-1][1] - points[0][1], points[-1][0] - points[0][0])) % 180.0

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
        if a.bbox[0] - 2 <= px <= a.bbox[2] + 2 and a.bbox[1] - 2 <= py <= a.bbox[3] + 2 and b.bbox[0] - 2 <= px <= b.bbox[2] + 2 and b.bbox[1] - 2 <= py <= b.bbox[3] + 2:
            return (px, py)
        return None


class ImageCleaner:
    """Input detection, optional perspective correction, deskewing, thresholding."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules, self.config = modules, config

    def clean_image(self, image_path: str, runtime_dir: Path) -> CleanImage:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        source = Path(image_path)
        if not source.exists():
            raise FileNotFoundError(f"Input file does not exist: {image_path}")
        prepared = self._prepare_input(source, runtime_dir)
        image_mod = self.modules.load("PIL.Image")
        image_ops = self.modules.load("PIL.ImageOps")
        image_filter = self.modules.load("PIL.ImageFilter")
        if image_mod is None:
            raise RuntimeError("Pillow is required to load raster images.")
        pil = image_mod.open(prepared).convert("RGB")
        np, cv2 = self.modules.load("numpy"), self.modules.load("cv2")
        warnings: List[str] = []
        corrected = False
        if np is not None and cv2 is not None:
            clean, threshold_img, threshold, corrected = self._preprocess_cv(pil, cv2, np, warnings)
        else:
            warnings.append("OpenCV/NumPy not available; using deterministic Pillow cleanup without perspective correction.")
            gray = image_ops.grayscale(pil).filter(image_filter.MedianFilter(size=3))
            hist = gray.histogram()
            threshold = int(sum(i * c for i, c in enumerate(hist)) / max(sum(hist), 1))
            threshold_img = gray.point(lambda p: 255 if p > threshold else 0).convert("1").convert("RGB")
            clean = threshold_img
        clean_path, threshold_path = runtime_dir / "clean.png", runtime_dir / "threshold.png"
        clean.save(clean_path)
        threshold_img.save(threshold_path)
        return CleanImage(str(source), str(clean_path), str(threshold_path), clean.width, clean.height, clean.mode, threshold, corrected, warnings)

    def _prepare_input(self, source: Path, runtime_dir: Path) -> Path:
        if source.suffix.lower() != ".pdf":
            return source
        pdf2image = self.modules.load("pdf2image")
        if pdf2image is None:
            raise RuntimeError("PDF input requires pdf2image/poppler in this environment.")
        out = runtime_dir / "pdf_page_1.png"
        pdf2image.convert_from_path(str(source), first_page=1, last_page=1, dpi=300)[0].save(out)
        return out

    def _preprocess_cv(self, pil: Any, cv2: Any, np: Any, warnings: List[str]) -> Tuple[Any, Any, int, bool]:
        image_mod = self.modules.load("PIL.Image")
        arr = np.array(pil)
        corrected_arr, corrected = self._perspective_correct(arr, cv2, np, warnings)
        gray = cv2.cvtColor(corrected_arr, cv2.COLOR_RGB2GRAY)
        gray = self._normalize_photo_background(gray, cv2, np, warnings)
        gray = cv2.fastNlMeansDenoising(gray, None, 7, 7, 21)
        enhanced = cv2.createCLAHE(clipLimit=self.config.clahe_clip_limit, tileGridSize=(8, 8)).apply(gray)
        block = self.config.adaptive_block_size + (1 - self.config.adaptive_block_size % 2)
        binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, self.config.adaptive_c)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, self.config.morphology_kernel), max(1, self.config.morphology_kernel)))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=1)
        binary = self._suppress_fold_shadows(binary, cv2, np, warnings)
        binary = self._deskew(binary, cv2, np, warnings)
        threshold = int(cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[0])
        out = image_mod.fromarray(binary).convert("RGB")
        return out, out, threshold, corrected

    def _normalize_photo_background(self, gray: Any, cv2: Any, np: Any, warnings: List[str]) -> Any:
        """Flatten uneven illumination from photographed or folded plans.

        The reference inputs have broad gray bands and paper gradients. A large
        morphological background estimate removes those low-frequency shadows
        before thresholding while preserving thin ink strokes for later semantic
        geometry reconstruction.
        """
        h, w = gray.shape[:2]
        kernel_size = max(15, int(min(h, w) * self.config.photo_shadow_kernel_ratio) | 1)
        background = cv2.medianBlur(gray, kernel_size)
        flattened = cv2.divide(gray, background, scale=255)
        if float(np.std(background)) > 6.0:
            warnings.append("Normalized uneven photo/scan illumination before thresholding.")
        return flattened

    def _suppress_fold_shadows(self, binary: Any, cv2: Any, np: Any, warnings: List[str]) -> Any:
        """Remove page-fold bands that cross rooms but are not CAD geometry."""
        black = binary < 128
        h, w = black.shape[:2]
        row_density = black.mean(axis=1)
        candidates = np.where((row_density > 0.02) & (row_density < 0.35))[0]
        if candidates.size == 0:
            return binary
        runs: List[Tuple[int, int]] = []
        start = int(candidates[0])
        prev = start
        for y in candidates[1:]:
            y = int(y)
            if y == prev + 1:
                prev = y
            else:
                runs.append((start, prev)); start = prev = y
        runs.append((start, prev))
        out = binary.copy()
        removed = 0
        for y0, y1 in runs:
            if y1 - y0 <= self.config.fold_line_suppression_width and w * 0.25 <= black[y0:y1 + 1].sum() <= w * max(1, y1 - y0 + 1) * 0.35:
                out[max(0, y0 - 1):min(h, y1 + 2), :] = 255
                removed += 1
        if removed:
            warnings.append(f"Suppressed {removed} likely fold/shadow band(s) after thresholding.")
        return out

    def _perspective_correct(self, arr: Any, cv2: Any, np: Any, warnings: List[str]) -> Tuple[Any, bool]:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        contours, _ = cv2.findContours(cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h, w = arr.shape[:2]
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
            approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
            if len(approx) == 4 and cv2.contourArea(approx) > 0.20 * w * h:
                pts = approx.reshape(4, 2).astype("float32")
                s, d = pts.sum(axis=1), np.diff(pts, axis=1)
                rect = np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")
                max_w = int(max(np.linalg.norm(rect[2] - rect[3]), np.linalg.norm(rect[1] - rect[0])))
                max_h = int(max(np.linalg.norm(rect[1] - rect[2]), np.linalg.norm(rect[0] - rect[3])))
                if max_w > 20 and max_h > 20:
                    dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype="float32")
                    warnings.append("Perspective corrected to detected page border.")
                    return cv2.warpPerspective(arr, cv2.getPerspectiveTransform(rect, dst), (max_w, max_h)), True
        return arr, False

    def _deskew(self, binary: Any, cv2: Any, np: Any, warnings: List[str]) -> Any:
        coords = np.column_stack(np.where(binary < 255))
        if coords.size == 0:
            return binary
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        if abs(angle) < 0.2 or abs(angle) > 20:
            return binary
        h, w = binary.shape[:2]
        warnings.append(f"Deskewed document by {angle:.2f} degrees.")
        return cv2.warpAffine(binary, cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0), (w, h), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)


class SnapEngine:
    def __init__(self, config: EngineConfig) -> None:
        self.config = config

    def snap_and_merge(self, lines: List[GeometryPrimitive]) -> Tuple[List[GeometryPrimitive], Dict[str, int]]:
        metrics = {"input_lines": len(lines), "snapped_vertices": 0, "merged_lines": 0, "removed_gaps": 0}
        constrained = [self._constrain_angle(l) for l in lines if GeometryMath.length(l.points) >= self.config.min_line_length]
        merged = self._merge_lines(constrained, metrics)
        metrics["output_lines"] = len(merged)
        return merged, metrics

    def _constrain_angle(self, line: GeometryPrimitive) -> GeometryPrimitive:
        p0, p1 = line.points[0], line.points[-1]
        angle = GeometryMath.angle(line.points)
        target = min([0.0, 45.0, 90.0, 135.0], key=lambda a: min(abs(angle - a), 180 - abs(angle - a)))
        if min(abs(angle - target), 180 - abs(angle - target)) > self.config.angle_tolerance_degrees:
            return line
        length, rad = GeometryMath.length(line.points), math.radians(target)
        cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        pts = [(cx - math.cos(rad) * length / 2, cy - math.sin(rad) * length / 2), (cx + math.cos(rad) * length / 2, cy + math.sin(rad) * length / 2)]
        return GeometryPrimitive("line", pts, GeometryMath.bbox(pts), line.layer, confidence=line.confidence)

    def _merge_lines(self, lines: List[GeometryPrimitive], metrics: Dict[str, int]) -> List[GeometryPrimitive]:
        groups: Dict[Tuple[int, int], List[GeometryPrimitive]] = defaultdict(list)
        for line in lines:
            angle = GeometryMath.angle(line.points)
            horizontal = abs(math.cos(math.radians(angle))) >= abs(math.sin(math.radians(angle)))
            offset = int(round(((line.points[0][1] + line.points[-1][1]) / 2 if horizontal else (line.points[0][0] + line.points[-1][0]) / 2) / max(self.config.merge_distance, 1)))
            groups[(0 if horizontal else 1, offset)].append(line)
        out: List[GeometryPrimitive] = []
        for group in groups.values():
            horizontal = abs(group[0].points[-1][0] - group[0].points[0][0]) >= abs(group[0].points[-1][1] - group[0].points[0][1])
            axis = 0 if horizontal else 1
            current = None
            for line in sorted(group, key=lambda l: min(l.points[0][axis], l.points[-1][axis])):
                if current is None:
                    current = line
                    continue
                c0, c1 = sorted([current.points[0][axis], current.points[-1][axis]])
                l0, l1 = sorted([line.points[0][axis], line.points[-1][axis]])
                if l0 <= c1 + self.config.merge_distance:
                    pts = current.points + line.points
                    start, end = min(pts, key=lambda p: p[axis]), max(pts, key=lambda p: p[axis])
                    fixed = sum(p[1 - axis] for p in pts) / len(pts)
                    new_pts = [(start[0], fixed), (end[0], fixed)] if horizontal else [(fixed, start[1]), (fixed, end[1])]
                    current = GeometryPrimitive("line", new_pts, GeometryMath.bbox(new_pts), "Walls", confidence=max(current.confidence, line.confidence))
                    metrics["merged_lines"] += 1
                    if l0 > c1:
                        metrics["removed_gaps"] += 1
                else:
                    out.append(current)
                    current = line
            if current is not None:
                out.append(current)
        return out


class GeometryEngine:
    """CAD-oriented geometry reconstruction, never final contour tracing."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules, self.config, self.snapper = modules, config, SnapEngine(config)

    def reconstruct_geometry(self, clean: CleanImage, runtime_dir: Optional[Path] = None) -> GeometryResult:
        cv2, np = self.modules.load("cv2"), self.modules.load("numpy")
        warnings: List[str] = []
        if cv2 is not None and np is not None:
            raw, circles = self._detect_lines_cv(clean, cv2), self._detect_circles_cv(clean, cv2, np)
            arcs = self._detect_door_arcs_cv(clean, cv2, np)
            windows = self._detect_windows(raw)
        else:
            warnings.append("OpenCV/NumPy not available; using projection-based fallback geometry.")
            raw, circles, arcs, windows = self._detect_lines_fallback(clean), [], [], []
        merged, metrics = self.snapper.snap_and_merge(raw)
        junctions, rooms = self._compute_junctions(merged), self._recognize_rooms(merged)
        primitives = merged + rooms + circles + arcs + windows
        metrics.update({"line_count": len(merged), "junction_count": len(junctions), "room_count": len(rooms), "circle_count": len(circles), "door_arc_count": len(arcs), "window_count": len(windows)})
        if runtime_dir and self.config.debug:
            (runtime_dir / "segments.json").write_text(json.dumps({"raw_segments": [asdict(p) for p in raw], "merged_segments": [asdict(p) for p in merged], "junctions": junctions}, indent=2), encoding="utf-8")
        return GeometryResult(primitives, raw, junctions, rooms, metrics, warnings)

    def _detect_lines_cv(self, clean: CleanImage, cv2: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.threshold_path, cv2.IMREAD_GRAYSCALE)
        lines = cv2.HoughLinesP(255 - img, 1, math.pi / 180, self.config.hough_threshold, minLineLength=self.config.hough_min_line_length, maxLineGap=self.config.hough_max_line_gap)
        out: List[GeometryPrimitive] = []
        if lines is not None:
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                pts = [(float(x1), float(y1)), (float(x2), float(y2))]
                if GeometryMath.length(pts) >= self.config.min_line_length:
                    out.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=0.85))
        return out

    def _detect_lines_fallback(self, clean: CleanImage) -> List[GeometryPrimitive]:
        image_mod = self.modules.load("PIL.Image")
        image = image_mod.open(clean.threshold_path).convert("1")
        w, h, pix = image.width, image.height, image.load()
        out: List[GeometryPrimitive] = []
        for horizontal in (True, False):
            outer, inner = (h, w) if horizontal else (w, h)
            for a in range(outer):
                start = None
                for b in range(inner):
                    x, y = (b, a) if horizontal else (a, b)
                    black = pix[x, y] == 0
                    if black and start is None:
                        start = b
                    if (not black or b == inner - 1) and start is not None:
                        end = b if black else b - 1
                        if end - start >= self.config.hough_min_line_length:
                            pts = [(float(start), float(a)), (float(end), float(a))] if horizontal else [(float(a), float(start)), (float(a), float(end))]
                            out.append(GeometryPrimitive("line", pts, GeometryMath.bbox(pts), "Walls", confidence=0.45))
                        start = None
        return out

    def _detect_circles_cv(self, clean: CleanImage, cv2: Any, np: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.threshold_path, cv2.IMREAD_GRAYSCALE)
        circles = cv2.HoughCircles(img, cv2.HOUGH_GRADIENT, dp=1.2, minDist=24, param1=80, param2=24, minRadius=self.config.circle_min_radius, maxRadius=self.config.circle_max_radius)
        return [GeometryPrimitive("circle", [], (float(x-r), float(y-r), float(x+r), float(y+r)), "Symbols", confidence=0.75, radius=float(r), center=(float(x), float(y))) for x, y, r in np.round(circles[0, :]).astype("int")] if circles is not None else []

    def _detect_door_arcs_cv(self, clean: CleanImage, cv2: Any, np: Any) -> List[GeometryPrimitive]:
        img = cv2.imread(clean.threshold_path, cv2.IMREAD_GRAYSCALE)
        edges = cv2.Canny(255 - img, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        arcs: List[GeometryPrimitive] = []
        for contour in contours:
            if len(contour) < 12:
                continue
            pts = contour.reshape(-1, 2).astype("float32")
            (x, y), radius = cv2.minEnclosingCircle(pts)
            if not (self.config.door_arc_min_radius <= radius <= self.config.door_arc_max_radius):
                continue
            perimeter = cv2.arcLength(contour, False)
            coverage = perimeter / max(2 * math.pi * radius, 1.0)
            if 0.14 <= coverage <= 0.42:
                angles = [math.degrees(math.atan2(float(py - y), float(px - x))) % 360 for px, py in pts[::max(1, len(pts)//24)]]
                start, end = min(angles), max(angles)
                bbox = (float(x - radius), float(y - radius), float(x + radius), float(y + radius))
                arcs.append(GeometryPrimitive("door", [], bbox, "Doors", stroke="#8a4b08", confidence=0.62, radius=float(radius), center=(float(x), float(y)), start_angle=start, end_angle=end, label="swing_arc"))
        return arcs[:120]

    def _detect_windows(self, lines: List[GeometryPrimitive]) -> List[GeometryPrimitive]:
        windows: List[GeometryPrimitive] = []
        for i, a in enumerate(lines):
            aa = GeometryMath.angle(a.points)
            if min(aa, abs(aa - 90), abs(aa - 180)) > self.config.angle_tolerance_degrees:
                continue
            for b in lines[i + 1:]:
                if abs(GeometryMath.length(a.points) - GeometryMath.length(b.points)) > max(8.0, GeometryMath.length(a.points) * 0.25):
                    continue
                ba = GeometryMath.angle(b.points)
                if min(abs(aa - ba), 180 - abs(aa - ba)) > self.config.angle_tolerance_degrees:
                    continue
                if aa < 45 or aa > 135:
                    distance = abs(((a.points[0][1] + a.points[-1][1]) - (b.points[0][1] + b.points[-1][1])) / 2)
                    overlap = min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])
                else:
                    distance = abs(((a.points[0][0] + a.points[-1][0]) - (b.points[0][0] + b.points[-1][0])) / 2)
                    overlap = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
                if 2.0 <= distance <= self.config.window_parallel_distance and overlap >= self.config.min_line_length:
                    bbox = (min(a.bbox[0], b.bbox[0]), min(a.bbox[1], b.bbox[1]), max(a.bbox[2], b.bbox[2]), max(a.bbox[3], b.bbox[3]))
                    windows.append(GeometryPrimitive("window", [(bbox[0], bbox[1]), (bbox[2], bbox[3])], bbox, "Windows", stroke="#0070c0", confidence=0.58, label="parallel_pair"))
        return windows[:120]

    def _compute_junctions(self, lines: List[GeometryPrimitive]) -> List[Point]:
        points: List[Point] = []
        for i, a in enumerate(lines):
            for b in lines[i + 1 :]:
                p = GeometryMath.line_intersection(a, b)
                if p is not None:
                    points.append(p)
        return points[:2000]

    def _recognize_rooms(self, lines: List[GeometryPrimitive]) -> List[GeometryPrimitive]:
        hs = [l for l in lines if GeometryMath.angle(l.points) <= self.config.angle_tolerance_degrees or GeometryMath.angle(l.points) >= 180 - self.config.angle_tolerance_degrees]
        vs = [l for l in lines if abs(GeometryMath.angle(l.points) - 90) <= self.config.angle_tolerance_degrees]
        rooms: List[GeometryPrimitive] = []
        for top in hs:
            for bottom in hs:
                if bottom.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance:
                    continue
                x0, x1 = max(top.bbox[0], bottom.bbox[0]), min(top.bbox[2], bottom.bbox[2])
                if x1 - x0 < 40:
                    continue
                left = any(abs(v.bbox[0] - x0) <= self.config.room_close_tolerance and v.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance and v.bbox[3] >= bottom.bbox[1] - self.config.room_close_tolerance for v in vs)
                right = any(abs(v.bbox[0] - x1) <= self.config.room_close_tolerance and v.bbox[1] <= top.bbox[1] + self.config.room_close_tolerance and v.bbox[3] >= bottom.bbox[1] - self.config.room_close_tolerance for v in vs)
                if left and right:
                    pts = [(x0, top.bbox[1]), (x1, top.bbox[1]), (x1, bottom.bbox[1]), (x0, bottom.bbox[1])]
                    rooms.append(GeometryPrimitive("room", pts, GeometryMath.bbox(pts), "Debug", stroke="#00aa00", confidence=0.7, label="closed_room"))
        return rooms[:200]


class OCREngine:
    """OCR is intentionally separate from geometry extraction."""

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules, self.config = modules, config

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
            for box, payload in page or []:
                text, confidence = payload
                xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
                blocks.append(TextBlock(str(text), (min(xs), min(ys), max(xs), max(ys)), float(confidence)))
        return blocks

    def _tesseract(self, clean: CleanImage, pytesseract: Any) -> List[TextBlock]:
        image_mod = self.modules.load("PIL.Image")
        data = pytesseract.image_to_data(image_mod.open(clean.image_path), output_type=pytesseract.Output.DICT)
        blocks: List[TextBlock] = []
        for i, text in enumerate(data.get("text", [])):
            text = text.strip()
            if text:
                conf_raw = str(data["conf"][i])
                conf = float(conf_raw) / 100.0 if conf_raw.replace(".", "", 1).lstrip("-").isdigit() else 0.0
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                blocks.append(TextBlock(text, (float(x), float(y), float(x + w), float(y + h)), max(0.0, conf)))
        return blocks


class SymbolTextEngine:
    """Local OCR recovery for symbol labels using the original source as truth."""

    PATTERNS = (r"^RG/F\d{2}$", r"^HC$", r"^HC Ø\d+$")
    KNOWN_LABELS = {"RG/F06", "RG/F16", "RG/F19", "RG/F21", "RG/F22", "HC", "HC Ø20"}

    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules, self.config = modules, config

    def recover(self, clean: CleanImage, geometry: GeometryResult, runtime_dir: Path) -> Tuple[List[SymbolTextResult], List[TextBlock], List[str]]:
        symbols = [p for p in geometry.primitives if p.layer == "Symbols" or p.kind in {"circle", "symbol", "door", "window"}]
        warnings: List[str] = []
        if not symbols:
            return [], [], warnings
        image_mod = self.modules.load("PIL.Image")
        if image_mod is None:
            return [], [], ["Symbol OCR skipped because Pillow is unavailable."]
        source_path = Path(clean.source_path if Path(clean.source_path).suffix.lower() != ".pdf" else clean.image_path)
        source = image_mod.open(source_path).convert("RGB")
        crop_dir = runtime_dir / "symbol_text_crops"
        crop_dir.mkdir(parents=True, exist_ok=True)
        results: List[SymbolTextResult] = []
        overlay: List[TextBlock] = []
        for index, symbol in enumerate(symbols[:200], 1):
            crop_box = self._crop_bbox(symbol.bbox, source.width, source.height)
            crop = source.crop(tuple(int(v) for v in crop_box))
            variants = self._preprocess_variants(crop, image_mod)
            candidates = self._ocr_variants(variants)
            selected, status, reason = self._select_candidate(candidates)
            symbol_id = f"SYM-{index:03d}"
            for name, variant in variants.items():
                variant.save(crop_dir / f"{symbol_id}_{name}.png")
            result = SymbolTextResult(symbol_id, symbol.bbox, crop_box, candidates, selected.normalized_text if selected else None, selected.pattern if selected else None, status, reason)
            results.append(result)
            if selected and status in {"PASS", "CORRECTED"}:
                overlay.append(TextBlock(selected.normalized_text, crop_box, selected.confidence))
        return results, overlay, warnings

    def _crop_bbox(self, bbox: BBox, width: int, height: int) -> BBox:
        margin = max(0, int(self.config.symbol_text_crop_margin))
        x1, y1, x2, y2 = bbox
        return (max(0.0, x1 - margin), max(0.0, y1 - margin), min(float(width), x2 + margin * 2), min(float(height), y2 + margin))

    def _preprocess_variants(self, crop: Any, image_mod: Any) -> Dict[str, Any]:
        image_ops = self.modules.load("PIL.ImageOps")
        gray = image_ops.grayscale(crop) if image_ops is not None else crop.convert("L")
        contrast = image_ops.autocontrast(gray) if image_ops is not None else gray
        threshold = contrast.point(lambda p: 255 if p > 160 else 0).convert("L")
        resampling = getattr(image_mod, "Resampling", image_mod)
        return {
            "gray": gray,
            "contrast": contrast,
            "threshold": threshold,
            "gray_x4": gray.resize((gray.width * 4, gray.height * 4), resampling.LANCZOS),
            "gray_x8": gray.resize((gray.width * 8, gray.height * 8), resampling.LANCZOS),
        }

    def _ocr_variants(self, variants: Dict[str, Any]) -> List[OCRCandidate]:
        pytesseract = self.modules.load("pytesseract")
        if pytesseract is None or not shutil.which("tesseract"):
            return []
        candidates: List[OCRCandidate] = []
        for variant, image in variants.items():
            data = pytesseract.image_to_data(image, config="--psm 7", output_type=pytesseract.Output.DICT)
            raw = " ".join(t.strip() for t in data.get("text", []) if t.strip())
            confidences = []
            for conf in data.get("conf", []):
                value = str(conf)
                if value.replace(".", "", 1).lstrip("-").isdigit() and float(value) >= 0:
                    confidences.append(float(value) / 100.0)
            if raw:
                normalized, pattern, corrected = self.normalize_symbol_text(raw)
                candidates.append(OCRCandidate(raw, variant, sum(confidences) / max(len(confidences), 1), normalized, pattern, normalized in self.KNOWN_LABELS, corrected))
        return candidates

    def normalize_symbol_text(self, text: str) -> Tuple[str, Optional[str], bool]:
        original = text
        value = re.sub(r"\s+", " ", text.strip().upper()).replace("RG-F", "RG/F").replace("RG F", "RG/F")
        value = value.replace("RG/F I", "RG/F1").replace("RG/F L", "RG/F1")
        rg = re.search(r"RG/F\s*([0-9OILSGl]{1,2})", value)
        if rg:
            digits = self._normalize_digits(rg.group(1)).zfill(2)[-2:]
            value = f"RG/F{digits}"
        hc = re.search(r"HC\s*(?:Ø|O|0)?\s*([0-9OILSGl]+)?", value)
        if hc and value.startswith("HC"):
            digits = self._normalize_digits(hc.group(1) or "")
            value = f"HC Ø{digits}" if digits else "HC"
        pattern = next((p for p in self.PATTERNS if re.match(p, value)), None)
        return value, pattern, value != original.strip().upper()

    def _normalize_digits(self, value: str) -> str:
        return value.translate(str.maketrans({"O": "0", "I": "1", "L": "1", "l": "1", "S": "5", "G": "6"}))

    def _select_candidate(self, candidates: List[OCRCandidate]) -> Tuple[Optional[OCRCandidate], str, Optional[str]]:
        valid = [c for c in candidates if c.pattern and c.confidence >= self.config.symbol_text_confidence_threshold]
        if not valid:
            reason = "no_candidate_above_confidence_or_pattern_threshold" if candidates else "no_ocr_candidates"
            return None, "MANUAL_REVIEW", reason
        ranked = sorted(valid, key=lambda c: (1 if c.pattern else 0, c.confidence, 1 if c.dictionary_match else 0), reverse=True)
        if len(ranked) > 1 and ranked[0].normalized_text != ranked[1].normalized_text and abs(ranked[0].confidence - ranked[1].confidence) < 0.03:
            return None, "MANUAL_REVIEW", "ambiguous_top_candidates"
        return ranked[0], "CORRECTED" if ranked[0].corrected else "PASS", None


class FontEngine:
    def enrich(self, blocks: List[TextBlock]) -> List[TextBlock]:
        for block in blocks:
            block.font_size = max(8.0, min(72.0, (block.bbox[3] - block.bbox[1]) * 0.85))
            block.font_family = "DejaVu Sans"
        return blocks


class SVGExporter:
    layers = ["Geometry", "Walls", "Doors", "Windows", "Symbols", "Text", "Debug"]

    def export(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> None:
        def esc(value: str) -> str:
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        by_layer: Dict[str, List[GeometryPrimitive]] = defaultdict(list)
        for primitive in geometry:
            by_layer[primitive.layer or "Geometry"].append(primitive)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="white"/>']
        for layer in self.layers:
            lines.append(f'<g id="{layer}" fill="none" stroke="black" stroke-linecap="round" stroke-linejoin="round">')
            if layer == "Text":
                for block in text:
                    lines.append(f'<text x="{block.bbox[0]:.2f}" y="{block.bbox[3]:.2f}" font-family="{esc(block.font_family)}" font-size="{block.font_size:.2f}" fill="black" stroke="none">{esc(block.text)}</text>')
            else:
                for primitive in by_layer.get(layer, []):
                    lines.append(self._primitive_svg(primitive))
            lines.append("</g>")
        lines.append("</svg>")
        path.write_text("\n".join(lines), encoding="utf-8")

    def _primitive_svg(self, p: GeometryPrimitive) -> str:
        if p.kind in {"line", "door", "window"} and len(p.points) >= 2:
            a, b = p.points[0], p.points[-1]
            return f'<line x1="{a[0]:.2f}" y1="{a[1]:.2f}" x2="{b[0]:.2f}" y2="{b[1]:.2f}" stroke="{p.stroke}" stroke-width="{p.stroke_width:.2f}"/>'
        if p.kind == "door" and p.center and p.radius and p.start_angle is not None and p.end_angle is not None:
            start = (p.center[0] + math.cos(math.radians(p.start_angle)) * p.radius, p.center[1] + math.sin(math.radians(p.start_angle)) * p.radius)
            end = (p.center[0] + math.cos(math.radians(p.end_angle)) * p.radius, p.center[1] + math.sin(math.radians(p.end_angle)) * p.radius)
            large = 1 if abs(p.end_angle - p.start_angle) > 180 else 0
            return f'<path d="M {start[0]:.2f} {start[1]:.2f} A {p.radius:.2f} {p.radius:.2f} 0 {large} 1 {end[0]:.2f} {end[1]:.2f}" stroke="{p.stroke}" stroke-width="{p.stroke_width:.2f}" fill="none"/>'
        if p.kind in {"circle", "symbol"} and p.center and p.radius:
            return f'<circle cx="{p.center[0]:.2f}" cy="{p.center[1]:.2f}" r="{p.radius:.2f}" stroke="{p.stroke}" stroke-width="{p.stroke_width:.2f}"/>'
        if p.points:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in p.points)
            return f'<polyline points="{pts}" stroke="{p.stroke}" stroke-width="{p.stroke_width:.2f}" fill="none"/>'
        return f'<!-- unsupported primitive {p.kind} -->'


class Renderer:
    def __init__(self, modules: OptionalModules, config: EngineConfig) -> None:
        self.modules, self.config = modules, config

    def render_png(self, path: Path, width: int, height: int, geometry: List[GeometryPrimitive], text: List[TextBlock], target_long_edge: int, junctions: Optional[List[Point]] = None) -> Tuple[int, int]:
        image_mod, image_draw, image_font = self.modules.load("PIL.Image"), self.modules.load("PIL.ImageDraw"), self.modules.load("PIL.ImageFont")
        if image_mod is None or image_draw is None:
            raise RuntimeError("Pillow is required for PNG rendering.")
        scale = target_long_edge / max(width, height)
        if width * height * scale * scale > self.config.render_max_pixels:
            scale = math.sqrt(self.config.render_max_pixels / max(width * height, 1))
        out_w, out_h = max(1, int(width * scale)), max(1, int(height * scale))
        canvas = image_mod.new("RGB", (out_w, out_h), "white")
        draw = image_draw.Draw(canvas)
        colors = {"Walls": "black", "Doors": "#8a4b08", "Windows": "#0070c0", "Symbols": "#555555", "Debug": "#00aa00"}
        for p in geometry:
            color = colors.get(p.layer, "black")
            if p.kind == "door" and p.center and p.radius and p.start_angle is not None and p.end_angle is not None:
                bbox = ((p.center[0]-p.radius)*scale, (p.center[1]-p.radius)*scale, (p.center[0]+p.radius)*scale, (p.center[1]+p.radius)*scale)
                draw.arc(bbox, start=p.start_angle, end=p.end_angle, fill=color, width=max(1, int(p.stroke_width * scale)))
            elif len(p.points) >= 2:
                pts = [(x * scale, y * scale) for x, y in p.points]
                draw.line(pts + ([pts[0]] if p.kind == "room" else []), fill=color, width=max(1, int(p.stroke_width * scale)))
            elif p.center and p.radius:
                x, y, r = p.center[0], p.center[1], p.radius
                draw.ellipse(((x-r)*scale, (y-r)*scale, (x+r)*scale, (y+r)*scale), outline=color, width=max(1, int(scale)))
        if junctions:
            for x, y in junctions:
                r = max(2, int(3 * scale)); draw.ellipse((x*scale-r, y*scale-r, x*scale+r, y*scale+r), fill="red")
        font = image_font.load_default() if image_font else None
        for block in text:
            draw.text((block.bbox[0] * scale, block.bbox[1] * scale), block.text, fill="black", font=font)
        canvas.save(path)
        return out_w, out_h

    def render_upscaled(self, path: Path, source_path: Path, factor: int = 8) -> Tuple[int, int]:
        image_mod = self.modules.load("PIL.Image")
        if image_mod is None:
            raise RuntimeError("Pillow is required for x8 upscaling.")
        source = image_mod.open(source_path).convert("RGB")
        resampling = getattr(image_mod, "Resampling", image_mod)
        size = (max(1, source.width * factor), max(1, source.height * factor))
        source.resize(size, resampling.LANCZOS).save(path)
        return size

    def render_debug_from_clean(self, path: Path, clean_path: str, overlay: List[GeometryPrimitive], junctions: Optional[List[Point]] = None) -> None:
        image_mod, image_draw = self.modules.load("PIL.Image"), self.modules.load("PIL.ImageDraw")
        image = image_mod.open(clean_path).convert("RGB")
        draw = image_draw.Draw(image)
        for p in overlay:
            if len(p.points) >= 2:
                draw.line(p.points, fill="red", width=2)
        if junctions:
            for x, y in junctions:
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="lime")
        image.save(path)


class PDFExporter:
    """A4 PDF exporter for final ChatGPT/Code Interpreter delivery."""

    MM_PER_INCH = 25.4
    PAPER_MM = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "LETTER": (215.9, 279.4)}

    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, png_path: Path, paper_size: str = "A4", dpi: int = 300) -> Optional[str]:
        image_mod = self.modules.load("PIL.Image")
        if image_mod is None:
            return "PDF export skipped because Pillow PDF is unavailable."
        page_w, page_h = self._paper_pixels(paper_size, dpi)
        source = image_mod.open(png_path).convert("RGB")
        fitted = self._fit_to_page(source, page_w, page_h, image_mod)
        fitted.save(path, "PDF", resolution=float(dpi))
        return None

    def _paper_pixels(self, paper_size: str, dpi: int) -> Tuple[int, int]:
        width_mm, height_mm = self.PAPER_MM.get(paper_size.upper(), self.PAPER_MM["A4"])
        return int(round(width_mm / self.MM_PER_INCH * dpi)), int(round(height_mm / self.MM_PER_INCH * dpi))

    def _fit_to_page(self, source: Any, page_w: int, page_h: int, image_mod: Any) -> Any:
        margin = max(24, int(min(page_w, page_h) * 0.035))
        available_w, available_h = page_w - 2 * margin, page_h - 2 * margin
        scale = min(available_w / max(source.width, 1), available_h / max(source.height, 1))
        out_w, out_h = max(1, int(source.width * scale)), max(1, int(source.height * scale))
        resampling = getattr(image_mod, "Resampling", image_mod)
        resized = source.resize((out_w, out_h), resampling.LANCZOS)
        page = image_mod.new("RGB", (page_w, page_h), "white")
        page.paste(resized, ((page_w - out_w) // 2, (page_h - out_h) // 2))
        return page


class DOCXExporter:
    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, png_path: Path, text: List[TextBlock]) -> Optional[str]:
        docx = self.modules.load("docx")
        if docx is None:
            return "python-docx not available; DOCX export skipped."
        document = docx.Document(); document.add_heading("Self Engine reconstruction", level=1); document.add_picture(str(png_path), width=docx.shared.Inches(6.5))
        for block in text:
            document.add_paragraph(block.text)
        document.save(str(path)); return None


class DXFExporter:
    def __init__(self, modules: OptionalModules) -> None:
        self.modules = modules

    def export(self, path: Path, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> Optional[str]:
        self._write_ascii_dxf(path, geometry, text)
        return None

    def _write_ascii_dxf(self, path: Path, geometry: List[GeometryPrimitive], text: List[TextBlock]) -> None:
        lines = ["0", "SECTION", "2", "ENTITIES"]
        for p in geometry:
            if p.kind in {"line", "door", "window"} and len(p.points) >= 2:
                a, b = p.points[0], p.points[-1]
                lines += ["0", "LINE", "8", p.layer, "10", f"{a[0]:.3f}", "20", f"{-a[1]:.3f}", "11", f"{b[0]:.3f}", "21", f"{-b[1]:.3f}"]
            elif p.kind in {"room", "polyline"} and p.points:
                lines += ["0", "LWPOLYLINE", "8", p.layer, "90", str(len(p.points)), "70", "1"]
                for x, y in p.points:
                    lines += ["10", f"{x:.3f}", "20", f"{-y:.3f}"]
            elif p.kind == "door" and p.center and p.radius and p.start_angle is not None and p.end_angle is not None:
                lines += ["0", "ARC", "8", p.layer, "10", f"{p.center[0]:.3f}", "20", f"{-p.center[1]:.3f}", "40", f"{p.radius:.3f}", "50", f"{-p.end_angle:.3f}", "51", f"{-p.start_angle:.3f}"]
            elif p.kind in {"circle", "symbol"} and p.center and p.radius:
                lines += ["0", "CIRCLE", "8", p.layer, "10", f"{p.center[0]:.3f}", "20", f"{-p.center[1]:.3f}", "40", f"{p.radius:.3f}"]
        for block in text:
            lines += ["0", "TEXT", "8", "Text", "10", f"{block.bbox[0]:.3f}", "20", f"{-block.bbox[1]:.3f}", "40", f"{block.font_size:.3f}", "1", block.text]
        lines += ["0", "ENDSEC", "0", "EOF"]
        path.write_text("\n".join(lines), encoding="utf-8")


class QualityEngine:
    def report(self, clean: CleanImage, geometry: GeometryResult, text: List[TextBlock], warnings: List[str], elapsed: float, config: EngineConfig) -> Dict[str, Any]:
        return {"source": clean.source_path, "clean_image": clean.image_path, "threshold_image": clean.threshold_path, "width": clean.width, "height": clean.height, "paper": config.paper, "dpi": config.dpi, "threshold": clean.threshold, "perspective_corrected": clean.perspective_corrected, "line_count": geometry.metrics.get("line_count", 0), "merged_lines": geometry.metrics.get("merged_lines", 0), "snapped_vertices": geometry.metrics.get("snapped_vertices", 0), "junction_count": geometry.metrics.get("junction_count", 0), "room_count": geometry.metrics.get("room_count", 0), "circle_count": geometry.metrics.get("circle_count", 0), "text_count": len(text), "ocr_confidence": round(sum(t.confidence for t in text) / max(len(text), 1), 4), "warnings": warnings, "processing_time_seconds": round(elapsed, 3), "assumptions": ["orthogonal walls are preserved unless detected angle exceeds tolerance", "OCR output is not used to infer geometry"], "failure_modes": ["low contrast scans may under-detect lines", "PDF support requires pdf2image/poppler"], "metrics": geometry.metrics}


class SelfEngine:
    """Facade for cleaning, geometry, OCR, rendering, and export."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or EngineConfig(); self.modules = OptionalModules(); self.cleaner = ImageCleaner(self.modules, self.config); self.geometry_engine = GeometryEngine(self.modules, self.config); self.ocr_engine = OCREngine(self.modules, self.config); self.symbol_text_engine = SymbolTextEngine(self.modules, self.config); self.font_engine = FontEngine(); self.renderer = Renderer(self.modules, self.config); self.svg_exporter = SVGExporter(); self.pdf_exporter = PDFExporter(self.modules); self.docx_exporter = DOCXExporter(self.modules); self.dxf_exporter = DXFExporter(self.modules); self.quality_engine = QualityEngine(); self._ocr_warnings: List[str] = []; self._render_warnings: List[str] = []

    def run(self, image: Optional[str] = None, image_path: Optional[str] = None, paper: Optional[str] = None, dpi: Optional[int] = None, output: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        start = time.time(); source = image or image_path
        if not source:
            raise ValueError("Pass image='/path/to/photo.jpg' or image_path='/path/to/photo.jpg'.")
        if paper: self.config.paper = paper
        if dpi: self.config.dpi = int(dpi)
        if output: self.config.output = tuple(output)
        runtime_dir = Path(self.config.runtime_dir); runtime_dir.mkdir(parents=True, exist_ok=True)
        clean = self.clean_image(source); geometry = self.reconstruct_geometry(clean); text = self.font_engine.enrich(self.reconstruct_text(clean))
        symbol_text, symbol_overlay, symbol_warnings = self.symbol_text_engine.recover(clean, geometry, runtime_dir)
        text = self.font_engine.enrich(text + symbol_overlay); artifacts = self.render(geometry, text, clean)
        geometry_json, text_json, symbol_text_json, report_json = runtime_dir / "geometry.json", runtime_dir / "text.json", runtime_dir / "symbol_text.json", runtime_dir / "report.json"
        geometry_json.write_text(json.dumps([asdict(p) for p in geometry.primitives], ensure_ascii=False, indent=2), encoding="utf-8")
        text_json.write_text(json.dumps([asdict(t) for t in text], ensure_ascii=False, indent=2), encoding="utf-8")
        symbol_text_json.write_text(json.dumps([asdict(s) for s in symbol_text], ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.update({"geometry": str(geometry_json), "text": str(text_json), "symbol_text": str(symbol_text_json)})
        warnings = clean.warnings + geometry.warnings + self._ocr_warnings + symbol_warnings + self._render_warnings
        report_json.write_text(json.dumps(self.quality_engine.report(clean, geometry, text, warnings, time.time() - start, self.config), ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts["report"] = str(report_json)
        return asdict(RenderResult(str(runtime_dir), artifacts, len(geometry.primitives), len(text), warnings))

    def clean_image(self, image_path: str) -> CleanImage:
        return self.cleaner.clean_image(image_path, Path(self.config.runtime_dir))

    def reconstruct_geometry(self, clean: CleanImage) -> GeometryResult:
        return self.geometry_engine.reconstruct_geometry(clean, Path(self.config.runtime_dir))

    def reconstruct_text(self, clean: CleanImage) -> List[TextBlock]:
        text, self._ocr_warnings = self.ocr_engine.reconstruct_text(clean); return text

    def render(self, geometry: GeometryResult, text: List[TextBlock], clean: CleanImage) -> Dict[str, str]:
        runtime_dir, requested = Path(self.config.runtime_dir), {o.lower() for o in self.config.output}
        artifacts: Dict[str, str] = {}; self._render_warnings = []
        if "svg" in requested:
            path = runtime_dir / "drawing.svg"; self.svg_exporter.export(path, clean.width, clean.height, geometry.primitives, text); artifacts["svg"] = str(path)
        png_path = runtime_dir / "preview_8k.png"
        if {"png", "pdf", "docx"} & requested:
            self.renderer.render_png(png_path, clean.width, clean.height, geometry.primitives, text, 8192); artifacts["png_8k"] = str(png_path)
            png16 = runtime_dir / "preview_16k.png"; self.renderer.render_png(png16, clean.width, clean.height, geometry.primitives, text, 16384); artifacts["png_16k"] = str(png16)
            png_x8 = runtime_dir / "preview_x8.png"; self.renderer.render_upscaled(png_x8, Path(clean.image_path), max(1, int(self.config.upscale_factor))); artifacts["png_x8"] = str(png_x8)
        if "pdf" in requested:
            path = runtime_dir / "drawing.pdf"; warning = self.pdf_exporter.export(path, png_path, self.config.pdf_paper_size, 300)
            if warning: self._render_warnings.append(warning)
            if path.exists(): artifacts["pdf"] = str(path)
        if "docx" in requested:
            path = runtime_dir / "drawing.docx"; warning = self.docx_exporter.export(path, png_path, text)
            if warning: self._render_warnings.append(warning)
            if path.exists(): artifacts["docx"] = str(path)
        if "dxf" in requested:
            path = runtime_dir / "drawing.dxf"; warning = self.dxf_exporter.export(path, geometry.primitives, text)
            if warning: self._render_warnings.append(warning)
            if path.exists(): artifacts["dxf"] = str(path)
        if self.config.debug:
            self._write_debug_artifacts(runtime_dir, clean, geometry, artifacts)
        return artifacts

    def _write_debug_artifacts(self, runtime_dir: Path, clean: CleanImage, geometry: GeometryResult, artifacts: Dict[str, str]) -> None:
        lines_path, junctions_path, snap_path = runtime_dir / "lines.png", runtime_dir / "junctions.png", runtime_dir / "snap.png"
        self.renderer.render_debug_from_clean(lines_path, clean.image_path, geometry.raw_segments)
        self.renderer.render_debug_from_clean(junctions_path, clean.image_path, geometry.primitives, geometry.junctions)
        self.renderer.render_debug_from_clean(snap_path, clean.image_path, [p for p in geometry.primitives if p.kind == "line"])
        artifacts.update({"debug_lines": str(lines_path), "debug_junctions": str(junctions_path), "debug_snap": str(snap_path), "threshold": clean.threshold_path, "segments": str(runtime_dir / "segments.json")})


Engine = SelfEngine


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self Engine image/PDF architectural reconstruction pipeline")
    parser.add_argument("image", nargs="?", help="Input raster image or PDF")
    parser.add_argument("--paper", default="A3", help="Paper preset metadata")
    parser.add_argument("--dpi", type=int, default=1200, help="Target logical DPI")
    parser.add_argument("--output", nargs="+", default=["png", "svg", "pdf", "docx", "dxf"], help="Artifacts to export")
    parser.add_argument("--runtime-dir", default="/mnt/data/runtime", help="Output directory")
    parser.add_argument("--no-debug", action="store_true", help="Disable debug artifacts")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if not args.image:
        print("Provide an input file, for example: python self_engine.py /mnt/data/photo.jpg", file=sys.stderr); return 2
    engine = SelfEngine(EngineConfig(paper=args.paper, dpi=args.dpi, output=tuple(args.output), runtime_dir=args.runtime_dir, debug=not args.no_debug))
    print(json.dumps(engine.run(image=args.image), ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
