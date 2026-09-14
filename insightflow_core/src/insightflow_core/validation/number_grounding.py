"""Number grounding -- the check behind "LLMs understand; deterministic systems calculate" for LLM prose.

An LLM writing text around verified results (a chat explanation, a dashboard narrative) can still state a
number nobody computed: on real Olist data, asked for customers in three states, the explanation summed three
verified counts into a total the engine never produced. Instructions not to do arithmetic aren't a guardrail;
this is. Callers compare the prose against the exact text the LLM was shown and withhold prose that fails.
"""
import math
import re

# A number as written in prose: "16,008,872.12", "-10.1%", "16.0 million", "99.4k".
_NUMBER = re.compile(r"(?<![\w.])-?(\d[\d,]*(?:\.\d+)?)(\s*%|\s?(?:million|billion|thousand)\b|[kKmMbB]\b)?", re.IGNORECASE)
_SCALES = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9}
_SMALL_INT = 10


def _numbers_in(text: str) -> list[tuple[str, float, float, bool]]:
    """(as written, magnitude, rounding tolerance, is_percent) for each number written in `text`."""
    found = []
    for match in _NUMBER.finditer(text):
        digits, suffix = match.group(1).replace(",", ""), (match.group(2) or "").strip().lower()
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        scale = _SCALES.get(suffix, 1.0)
        found.append((match.group(0).strip(), abs(float(digits)) * scale, 0.5 * 10**-decimals * scale, suffix == "%"))
    return found


def ungrounded_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that don't match, to their written precision, any number in `source`.

    Percentages match a fraction or a percentage in the source ("1.9%" matches 0.0187 and "1.9%"). Small whole
    numbers (up to 10) are ignored: "the top 3" or "2 regions" carry no computed figure.
    """
    known = [v for _, value, _, is_percent in _numbers_in(source) for v in ((value / 100, value) if is_percent else (value,))]
    ungrounded = []
    for written, value, tolerance, is_percent in _numbers_in(text):
        if not is_percent and tolerance < 1 and value <= _SMALL_INT and value.is_integer():
            continue
        candidates = [(value / 100, tolerance / 100), (value, tolerance)] if is_percent else [(value, tolerance)]
        if not any(math.isclose(c, k, abs_tol=t * 1.000001) for c, t in candidates for k in known):
            ungrounded.append(written)
    return ungrounded
