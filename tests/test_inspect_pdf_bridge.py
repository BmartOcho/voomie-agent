"""
Test harness for the inspect_pdf MCP bridge.

Each test spawns its own MCP server subprocess via lib.mcp_bridge.MCPBridge,
makes a real fitz call against a programmatically-generated PDF fixture,
and tears the bridge down on teardown. No mocks, no committed binary
fixtures — every PDF is built in tmp_path before the test runs.

Mirrors the structure of test_parse_shoptalk_bridge.py /
test_registry_bridge.py / test_mongodb_bridge.py: same fixture pattern,
same assertion style.

Run:  pytest tests/test_inspect_pdf_bridge.py -v
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import fitz
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from lib.mcp_bridge import MCPBridge  # noqa: E402

SERVER_PATH = REPO_ROOT / "tools" / "inspect_pdf_server.py"


# ---------------------------------------------------------------------------
# Bridge fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge():
    """Per-test bridge: spawn the MCP server, yield, tear it down."""
    with MCPBridge(
        server_command=sys.executable,
        server_args=[str(SERVER_PATH)],
    ) as b:
        yield b


def _structured(result: dict) -> dict:
    """Helper: extract the structured response from a bridge call_tool result."""
    structured = result["structured"]
    assert structured is not None, (
        f"No structured response from MCP server. Raw text was: {result['text']!r}"
    )
    assert isinstance(structured, dict), (
        f"Expected dict from MCP, got {type(structured).__name__}: {structured!r}"
    )
    return structured


def _inspect(bridge: MCPBridge, file_path: str) -> dict:
    return _structured(bridge.call_tool("inspect_pdf", {"file_path": file_path}))


# ---------------------------------------------------------------------------
# Fixture builders — each returns a path to a freshly-generated PDF.
# ---------------------------------------------------------------------------


def _cmyk_jpeg_bytes(color: tuple[int, int, int, int] = (255, 0, 0, 0)) -> bytes:
    """A tiny 50×50 CMYK JPEG. fitz reports cs-name='ICCBased(CMYK,...)'."""
    img = Image.new("CMYK", (50, 50), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _rgb_jpeg_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """A tiny 50×50 RGB JPEG. fitz reports cs-name='DeviceRGB' or similar."""
    img = Image.new("RGB", (50, 50), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_pdf_4x6_cmyk_with_bleed(path: Path) -> Path:
    """4×6in trim, 4.25×6.25in media (0.125in bleed on every side), one CMYK image."""
    doc = fitz.open()
    # 4.25 × 6.25 in points (1 in = 72 pt).
    page = doc.new_page(width=4.25 * 72, height=6.25 * 72)
    # TrimBox is 4×6 inset by 0.125 in on every side.
    page.set_trimbox(fitz.Rect(0.125 * 72, 0.125 * 72, 4.125 * 72, 6.125 * 72))
    page.insert_image(fitz.Rect(50, 50, 200, 200), stream=_cmyk_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_no_trimbox(path: Path) -> Path:
    """4×6 mediabox, no trimbox call (fitz returns mediabox as trimbox)."""
    doc = fitz.open()
    page = doc.new_page(width=4 * 72, height=6 * 72)
    page.insert_image(fitz.Rect(50, 50, 200, 200), stream=_cmyk_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_rgb_image(path: Path) -> Path:
    """4×6 PDF with a single RGB image and no trimbox/bleed."""
    doc = fitz.open()
    page = doc.new_page(width=4 * 72, height=6 * 72)
    page.insert_image(fitz.Rect(50, 50, 200, 200), stream=_rgb_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_mixed_colorspace(path: Path) -> Path:
    """4×6 PDF with both an RGB and a CMYK image — must yield mixed=true."""
    doc = fitz.open()
    page = doc.new_page(width=4 * 72, height=6 * 72)
    page.insert_image(fitz.Rect(20, 20, 130, 130), stream=_rgb_jpeg_bytes())
    page.insert_image(fitz.Rect(140, 20, 270, 130), stream=_cmyk_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_multipage(path: Path, page_count: int = 3) -> Path:
    """Multi-page PDF — each page 4×6 with a CMYK image."""
    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page(width=4 * 72, height=6 * 72)
        page.set_trimbox(
            fitz.Rect(0.125 * 72, 0.125 * 72, 3.875 * 72, 5.875 * 72)
        )
        page.insert_image(fitz.Rect(50, 50, 200, 200), stream=_cmyk_jpeg_bytes())
    # Above used 4×6 media — make sure mediabox is really 4×6 by re-verifying.
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_thin_bleed(path: Path) -> Path:
    """Bleed present but only ~0.05in on the top side — must trigger the
    'less than 0.125in' warning."""
    doc = fitz.open()
    # Mediabox 4.1 × 6.05 (0.1in side bleed, 0.05in top/bottom)
    page = doc.new_page(width=4.1 * 72, height=6.05 * 72)
    # Trimbox 4 × 6 centered → top/bottom gap = 0.025in (much less than 0.125)
    page.set_trimbox(fitz.Rect(0.05 * 72, 0.025 * 72, 4.05 * 72, 6.025 * 72))
    page.insert_image(fitz.Rect(20, 20, 100, 100), stream=_cmyk_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_encrypted(path: Path) -> Path:
    """Password-protected PDF — must surface as error_class='encrypted'."""
    doc = fitz.open()
    page = doc.new_page(width=4 * 72, height=6 * 72)
    page.insert_text((100, 100), "secret")
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    doc.close()
    return path


def _make_pdf_all_fonts_embedded(path: Path) -> Path:
    """PDF with no fonts at all (just an image) — vacuously all-embedded.

    A real PDF with embedded fonts requires an external font file (the
    standard Helvetica fitz uses by default reports ext='n/a' = NOT
    embedded). For this test we use the simpler path: zero fonts in the
    document means there is nothing whose embedding is unverified, so
    fonts.all_embedded must be true.
    """
    doc = fitz.open()
    page = doc.new_page(width=4 * 72, height=6 * 72)
    page.insert_image(fitz.Rect(50, 50, 200, 200), stream=_cmyk_jpeg_bytes())
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Sanity: the server advertises the inspect_pdf tool.
# ---------------------------------------------------------------------------


def test_server_advertises_inspect_pdf_tool(bridge):
    tools = set(bridge.list_tools())
    assert "inspect_pdf" in tools, f"Expected inspect_pdf in {tools}"


# ---------------------------------------------------------------------------
# Success-path tests
# ---------------------------------------------------------------------------


def test_clean_4x6_cmyk_pdf_with_bleed(bridge, tmp_path):
    path = _make_pdf_4x6_cmyk_with_bleed(tmp_path / "clean.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["page_count"] == 1
    # Trim should be 4×6 in (the inner trimbox we set).
    assert result["trim_size"]["width_in"] == 4.0
    assert result["trim_size"]["height_in"] == 6.0
    # Bleed: present and adequate (0.125in on every side).
    assert result["bleed"]["present"] is True
    assert result["bleed"]["adequate"] is True
    for side in ("top", "bottom", "left", "right"):
        assert result["bleed"][f"{side}_in"] == pytest.approx(0.125, abs=1e-3)
    # Color space: CMYK dominant.
    assert result["color_space"]["dominant"] == "CMYK"
    assert result["color_space"]["has_cmyk"] is True
    assert result["color_space"]["has_rgb"] is False
    assert result["color_space"]["mixed"] is False
    assert result["file_health"]["encrypted"] is False
    assert result["file_health"]["corrupted"] is False


def test_pdf_without_trimbox_reports_no_bleed_and_warns(bridge, tmp_path):
    path = _make_pdf_no_trimbox(tmp_path / "no-tb.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["bleed"]["present"] is False
    assert result["bleed"]["adequate"] is False
    # Warning text must reference TrimBox so the agent can phrase the
    # follow-up question coherently.
    assert any("TrimBox" in w for w in result["warnings"]), result["warnings"]


def test_pdf_with_rgb_images_flags_rgb(bridge, tmp_path):
    path = _make_pdf_rgb_image(tmp_path / "rgb.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["color_space"]["has_rgb"] is True
    assert result["color_space"]["has_cmyk"] is False
    assert result["color_space"]["dominant"] == "RGB"


def test_pdf_with_mixed_rgb_and_cmyk_flags_mixed(bridge, tmp_path):
    path = _make_pdf_mixed_colorspace(tmp_path / "mixed.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["color_space"]["has_rgb"] is True
    assert result["color_space"]["has_cmyk"] is True
    assert result["color_space"]["mixed"] is True
    # Warning must mention mixed/RGB so the agent can surface it.
    assert any(
        "mixed" in w.lower() or "rgb" in w.lower() for w in result["warnings"]
    ), result["warnings"]


def test_multipage_pdf_returns_correct_page_count(bridge, tmp_path):
    path = _make_pdf_multipage(tmp_path / "multi.pdf", page_count=3)
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["page_count"] == 3
    # Trim size from page 0 — same fixture creates identical pages.
    assert result["trim_size"]["width_in"] == pytest.approx(3.75, abs=1e-3)
    assert result["trim_size"]["height_in"] == pytest.approx(5.75, abs=1e-3)


def test_pdf_with_no_fonts_reports_all_embedded(bridge, tmp_path):
    path = _make_pdf_all_fonts_embedded(tmp_path / "no-fonts.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["fonts"]["all_embedded"] is True
    assert result["fonts"]["unembedded_names"] == []


def test_thin_bleed_emits_inadequate_bleed_warning(bridge, tmp_path):
    path = _make_pdf_thin_bleed(tmp_path / "thin.pdf")
    result = _inspect(bridge, str(path))

    assert result["ok"] is True, result
    assert result["bleed"]["present"] is True
    assert result["bleed"]["adequate"] is False
    # Warning must mention "0.125" so the agent can quote the standard.
    assert any("0.125" in w for w in result["warnings"]), result["warnings"]


# ---------------------------------------------------------------------------
# Error-path tests
# ---------------------------------------------------------------------------


def test_non_pdf_extension_returns_not_pdf(bridge, tmp_path):
    # Write a real-ish .jpg file. The tool must reject it on extension
    # without ever opening it with fitz.
    jpg_path = tmp_path / "art.jpg"
    Image.new("RGB", (10, 10)).save(str(jpg_path), format="JPEG")

    result = _inspect(bridge, str(jpg_path))
    assert result["ok"] is False, result
    assert result["error_class"] == "not_pdf"
    assert ".jpg" in result["message"].lower() or "jpg" in result["message"].lower()


def test_nonexistent_path_returns_not_found(bridge, tmp_path):
    missing = tmp_path / "does-not-exist.pdf"
    result = _inspect(bridge, str(missing))
    assert result["ok"] is False, result
    assert result["error_class"] == "not_found"


def test_encrypted_pdf_returns_encrypted_error(bridge, tmp_path):
    path = _make_pdf_encrypted(tmp_path / "locked.pdf")
    result = _inspect(bridge, str(path))
    assert result["ok"] is False, result
    assert result["error_class"] == "encrypted"
