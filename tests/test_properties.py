"""Property-based tests (Hypothesis) for the invariants the agent's safety rests on.

Example tests check the cases we thought of; these check the stated property over
generated inputs. Each test names the invariant it protects. When one fails,
Hypothesis prints the minimal counterexample: turn it into a regular regression test
too, then fix the code.
"""
import json
import math

from hypothesis import given, settings
from hypothesis import strategies as st

from mos import maiui, safety

DIM = st.integers(min_value=1, max_value=4000)
ANY_FLOAT = st.floats(allow_nan=True, allow_infinity=True)


# --- coordinate mapping -------------------------------------------------------

@given(x=ANY_FLOAT, y=ANY_FLOAT, w=DIM, h=DIM)
def test_to_pixels_never_leaves_the_screen(x, y, w, h):
    """Invariant: whatever the model emits, a tap lands on the screen (or the step is refused)."""
    try:
        px, py = maiui.to_pixels(maiui._point([x, y]), w, h)
    except maiui.ParseError:
        assert not (math.isfinite(x) and math.isfinite(y))
        return
    assert 0 <= px <= w - 1 and 0 <= py <= h - 1


@given(x=st.integers(0, 999), y=st.integers(0, 999), w=DIM, h=DIM)
def test_to_pixels_is_monotonic(x, y, w, h):
    """Invariant: moving right/down in model space never moves left/up on screen."""
    ax, ay = maiui.to_pixels([x, y], w, h)
    bx, by = maiui.to_pixels([min(x + 1, 999), min(y + 1, 999)], w, h)
    assert bx >= ax and by >= ay


JUNK = st.one_of(st.none(), st.text(max_size=5), st.booleans(), st.lists(st.integers(), max_size=2),
                 st.floats(allow_nan=True, allow_infinity=True))


@given(coord=st.lists(JUNK, min_size=0, max_size=5))
def test_bad_coordinates_raise_parse_error_not_crash(coord):
    """Invariant: malformed model output is a ParseError (a clean, logged stop), never a TypeError."""
    try:
        p = maiui._point(coord)
    except maiui.ParseError:
        return
    assert len(p) == 2 and all(isinstance(v, float) and math.isfinite(v) for v in p)


@given(thinking=st.text(max_size=200).filter(lambda t: "</thinking>" not in t and "<tool_call>" not in t),
       x=st.integers(0, 999), y=st.integers(0, 999))
def test_format_then_parse_round_trips(thinking, x, y):
    """Invariant: history replay (format_response) is read back exactly by parse_response."""
    args = {"action": "click", "coordinate": [float(x), float(y)]}
    t, a = maiui.parse_response(maiui.format_response(thinking, args))
    assert a == args and t == thinking.strip()


# --- safety gate ----------------------------------------------------------------

RISKY = ["Send", "Pay now", "Delete", "Place order", "Transfer", "Uninstall", "Log out"]


@given(word=st.sampled_from(RISKY), prefix=st.sampled_from(["", "Tap to ", "OK, "]),
       x1=st.integers(0, 500), y1=st.integers(0, 500), dw=st.integers(1, 500), dh=st.integers(1, 500),
       fx=st.floats(0, 1), fy=st.floats(0, 1))
def test_tapping_a_risky_button_always_asks(word, prefix, x1, y1, dw, dh, fx, fy):
    """Invariant: a tap anywhere inside a Send/Pay/Delete-like element needs the user's OK."""
    el = {"text": prefix + word, "bounds": [x1, y1, x1 + dw, y1 + dh]}
    x, y = x1 + round(fx * dw), y1 + round(fy * dh)
    assert safety.needs_confirmation({"type": "click", "x": x, "y": y}, [el]) is not None


@given(digits=st.text(alphabet="0123456789", min_size=16, max_size=16),
       sep=st.sampled_from(["", " ", "-", "  "]))
def test_card_numbers_are_flagged_however_formatted(digits, sep):
    """Invariant: typing a 16-digit card number asks first, with or without separators."""
    text = sep.join(digits[i:i + 4] for i in range(0, 16, 4))
    assert safety.needs_confirmation({"type": "type", "text": text}, []) is not None


@settings(max_examples=50)
@given(text=st.text(max_size=40))
def test_needs_confirmation_never_crashes_on_type(text):
    safety.needs_confirmation({"type": "type", "text": text}, [])


def test_round_trip_survives_unicode_text_action():
    args = {"action": "type", "text": "안녕 👋 \"quoted\""}
    _, a = maiui.parse_response(maiui.format_response("t", args))
    assert a == args and json.dumps(a, ensure_ascii=False)


# --- regressions found by the properties above ------------------------------------

def test_regression_dashed_card_number_needs_confirmation():
    assert safety.needs_confirmation({"type": "type", "text": "4111-1111-1111-1111"}, []) is not None


def test_regression_null_coordinate_is_parse_error():
    import pytest
    with pytest.raises(maiui.ParseError):
        maiui.parse_response('<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[null,null]}}</tool_call>')
    with pytest.raises(maiui.ParseError):
        maiui.parse_response('<tool_call>{"name":"mobile_use","arguments":{"action":"click","coordinate":[NaN,5]}}</tool_call>')
