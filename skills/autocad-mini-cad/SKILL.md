## name: autocad\-mini\-cad description: Reconstruct architectural and technical drawings from scans or photos into CAD\-style geometry\. Use when the user asks to vectorize, straighten, snap, align, trace, or clean floor plans, technical plans, blueprints, or MiniCAD/AutoCAD\-style drawings\.

# AutoCAD / MiniCAD geometry reconstruction skill

Use this skill for scanned plans, photographed drawings, technical schematics, and floor plans that should be converted into clean CAD\-like geometry\.

## Core objective

Do **not** trace image contours as a vectorization shortcut\. Preserve geometry instead:

- recover straight walls as perfect horizontal / vertical / constrained\-angle segments,
- preserve circles, arcs, door swings, and symbol primitives,
- snap nearby segments to shared intersections,
- align the final result to an orthogonal or measured CAD coordinate system,
- keep the output 1:1 in geometry, not in pixel outlines\.

## Preferred workflow

1. **Preprocess the source**
  - correct perspective,
  - deskew,
  - normalize contrast,
  - denoise only enough to improve line detection,
  - avoid blur that destroys thin strokes\.
2. **Extract geometry**
  - detect line segments first,
  - cluster collinear fragments,
  - merge overlapping or adjacent segments,
  - infer junctions and intersections,
  - detect circles and arcs separately,
  - identify door swings, windows, fixtures, and symbols as semantic primitives\.
3. **Snap and align**
  - snap to shared endpoints,
  - enforce 90° / 45° where the source supports it,
  - keep measured offsets consistent,
  - avoid arbitrary contour simplification\.
4. **Handle text separately**
  - run OCR independently of geometry,
  - place text blocks after geometry reconstruction,
  - do not use text boxes as shape boundaries\.
5. **Export**
  - prefer SVG for debug and DXF\-like geometry fidelity,
  - keep a clean layered structure,
  - include a QA report with warnings, missing detections, and confidence notes\.

## Output rules

When generating code or edits for this repository:

- preserve geometric fidelity over visual similarity,
- favor deterministic algorithms and explicit thresholds,
- keep functions small and testable,
- expose parameters for line tolerance, snap tolerance, angle tolerance, and minimum component size,
- add debug artifacts for line map, junction map, and snap decisions when useful\.

## Quality checks

Before finishing, verify:

- walls are straight,
- room boundaries are closed where expected,
- intersections are consistent,
- arcs are circular rather than polygonal approximations,
- preview output matches the source layout,
- the result is suitable for CAD import or further manual cleanup\.