# Repository instructions for Codex

## Primary goal

Turn scanned or photographed technical drawings into clean CAD-style geometry with the highest possible geometric fidelity.

## Important rules

- Prefer geometry reconstruction over pixel vectorization.
- Do not use contour tracing as the final representation for architectural plans.
- Keep walls orthogonal unless the source clearly shows otherwise.
- Preserve circles, arcs, door swings, and repeated symbols as semantic primitives.
- Separate OCR from geometry extraction.
- Keep the pipeline deterministic and testable.

## Expected workflow

1. Inspect the source and determine whether it is a photo, scan, or PDF.
2. Correct perspective and deskew before geometry extraction.
3. Extract line segments, junctions, arcs, and circles.
4. Snap and align the drawing to a CAD-like coordinate system.
5. Export both a clean preview and a structured vector format.
6. Add or update tests for any algorithmic change.
7. Document assumptions, thresholds, and failure modes.

## Implementation preferences

- Small, explicit functions.
- Configuration-driven thresholds.
- Clear intermediate artifacts for debugging.
- Avoid overfitting to a single sample drawing.
- Preserve source scale where possible.

## Validation

When changing the geometry pipeline, verify the result against:
- straightness of major walls,
- closure of rooms and corridors,
- correctness of door/window symbols,
- text placement accuracy,
- visual alignment with the original plan.

## Notes

If a task asks for "1:1" fidelity, interpret that as geometric fidelity first, not raster similarity.
