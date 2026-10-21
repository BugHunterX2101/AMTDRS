"""Model-written diffs are rarely numerically exact. These cases are the shapes
Nemotron actually produced against a live run, reduced to their essentials."""

import pytest

from principal.diffs import canonicalise, parse_diff
from principal.errors import DiffUnparseable
from principal.gates.apply import apply_to_text

ORIGINAL = (
    '"""Order pricing."""\n'
    "\n"
    "TAX_RATE = 0.2\n"
    "\n"
    "def calc_total(items, tax=TAX_RATE):\n"
    '    """Line prices summed, plus tax."""\n'
    "    subtotal = sum(price * qty for price, qty in items)\n"
    "    return round(subtotal * (1 + tax), 2)\n"
)


def _wrap(body: str) -> str:
    return f"<<<DIFF\n--- a/src/pricing.py\n+++ b/src/pricing.py\n{body}\nDIFF>>>"


def _apply(raw: str) -> str:
    diff = canonicalise(parse_diff(raw), "src/pricing.py", ORIGINAL)
    return apply_to_text(ORIGINAL, list(diff.patch)[0]).new_text


def test_bare_hunk_header_is_accepted():
    raw = _wrap(
        "@@\n"
        "-def calc_total(items, tax=TAX_RATE):\n"
        "+def calc_total(items: list, tax: float = TAX_RATE) -> float:\n"
        '     """Line prices summed, plus tax."""'
    )
    assert "def calc_total(items: list, tax: float = TAX_RATE) -> float:" in _apply(raw)


def test_wrong_line_numbers_are_relocated_by_content():
    raw = _wrap(
        "@@ -90,1 +90,1 @@\n"
        "-TAX_RATE = 0.2\n"
        "+TAX_RATE: float = 0.2\n"
    )
    assert "TAX_RATE: float = 0.2\n" in _apply(raw)


def test_canonical_text_is_a_git_applicable_unified_diff():
    raw = _wrap("@@\n-TAX_RATE = 0.2\n+TAX_RATE: float = 0.2")
    text = canonicalise(parse_diff(raw), "src/pricing.py", ORIGINAL).text
    assert text.startswith("--- a/src/pricing.py\n+++ b/src/pricing.py\n@@ -")


def test_a_closing_marker_the_model_indented_does_not_become_a_context_line():
    """Seen live from Nano: ` DIFF>>>` with a leading space. The space was kept as
    the hunk's last line, an empty context line that matches nothing in the file,
    so a correct diff was rejected as a context mismatch."""
    raw = (
        "<<<DIFF\n--- a/src/pricing.py\n+++ b/src/pricing.py\n@@\n"
        "-    subtotal = sum(price * qty for price, qty in items)\n"
        "+    subtotal = sum(p * q for p, q in items)\n DIFF>>>"
    )
    assert "sum(p * q for p, q in items)" in _apply(raw)


def test_a_real_blank_context_line_before_the_marker_is_kept():
    raw = _wrap("@@\n-TAX_RATE = 0.2\n+TAX_RATE: float = 0.2\n ")
    assert "TAX_RATE: float = 0.2\n\ndef calc_total" in _apply(raw)


def test_a_bare_closing_marker_is_accepted_when_the_full_one_is_missing():
    """Seen live from Super: `>>>` alone, then the repairer's KIND line. A line
    that is exactly `>>>` is not a valid diff line, so it can only be the end."""
    raw = (
        "<<<DIFF\n--- a/src/pricing.py\n+++ b/src/pricing.py\n@@\n"
        "-TAX_RATE = 0.2\n+TAX_RATE: float = 0.2\n>>>\n\nKIND: other"
    )
    assert "TAX_RATE: float = 0.2\n" in _apply(raw)


def test_context_that_is_not_in_the_file_is_rejected():
    raw = _wrap("@@\n-TAX_RATE = 0.99\n+TAX_RATE = 0.5")
    with pytest.raises(DiffUnparseable):
        canonicalise(parse_diff(raw), "src/pricing.py", ORIGINAL)


def test_a_hunk_that_changes_nothing_is_rejected():
    raw = _wrap(
        "@@ -1,3 +1,3 @@\n"
        ' """Order pricing."""\n'
        " \n"
        " TAX_RATE = 0.2"
    )
    with pytest.raises(DiffUnparseable):
        canonicalise(parse_diff(raw), "src/pricing.py", ORIGINAL)


def test_blank_context_line_without_leading_space_is_tolerated():
    raw = _wrap(
        "@@\n"
        ' """Order pricing."""\n'
        "\n"
        "-TAX_RATE = 0.2\n"
        "+TAX_RATE: float = 0.2"
    )
    assert "TAX_RATE: float = 0.2" in _apply(raw)


def test_file_without_trailing_newline_still_yields_applicable_diff():
    original = "a = 1\nb = 2"
    raw = "<<<DIFF\n--- a/x.py\n+++ b/x.py\n@@\n-a = 1\n+a = 10\nDIFF>>>"
    diff = canonicalise(parse_diff(raw), "x.py", original)
    assert "a = 10" in apply_to_text(original, list(diff.patch)[0]).new_text
