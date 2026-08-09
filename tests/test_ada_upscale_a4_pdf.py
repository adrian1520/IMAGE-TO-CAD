from pathlib import Path

from PIL import Image

from ada_upscale_a4_pdf import build_a4_pdf, make_a4_pdf, upscale_x8


def test_upscale_x8_preserves_ratio(tmp_path: Path) -> None:
    source = tmp_path / "mini_cad.png"
    Image.new("RGB", (12, 8), "white").save(source)

    out = upscale_x8(source)

    image = Image.open(out)
    assert image.size == (96, 64)


def test_build_a4_pdf_creates_png_and_pdf(tmp_path: Path) -> None:
    source = tmp_path / "generated.png"
    Image.new("RGB", (20, 10), "white").save(source)

    result = build_a4_pdf(source)

    assert Path(result["png_x8"]).exists()
    assert Path(result["pdf_a4"]).exists()


def test_make_a4_pdf_accepts_portrait(tmp_path: Path) -> None:
    source = tmp_path / "portrait.png"
    Image.new("RGB", (10, 20), "white").save(source)

    pdf = make_a4_pdf(source)

    assert pdf.exists()
    assert pdf.suffix == ".pdf"
