"""Pure, hardware-independent pulse validation shared by capture and gates."""
from __future__ import annotations

import numpy as np

SCHEMA = "observed-trigger-edges/v2"


def _runs(mask: np.ndarray, minimum: int) -> list[tuple[int, int]]:
    # Scan every sample, but avoid padding/casting and two full-size edge scans.
    if mask.size == 0:
        return []
    changes = np.flatnonzero(mask[1:] != mask[:-1]) + 1
    bounds = np.concatenate(([0] if mask[0] else [], changes,
                             [mask.size] if mask[-1] else [])).astype(np.intp)
    starts, ends = bounds[::2], bounds[1::2]
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b - a >= minimum]


def inspect_trigger(samples, *, threshold: float, hysteresis: float,
                    min_stable_samples: int = 16, min_tail_samples: int = 5000,
                    expected_samples: int | None = None, overflow: int = 0) -> dict:
    """Require one debounced low/high/low pulse, with actual trailing samples.

    Edges are the first sample of a stable threshold run, not the last high
    sample. Short glitches are ignored. A record ending high is right censored.
    """
    x = np.asarray(samples)
    row = dict(valid=False, complete_rise_fall=0, reason="UNKNOWN",
               rise_index=None, fall_index=None, pulse_width_samples=None,
               tail_samples=0, sample_count=int(x.size), overflow=int(overflow))
    if x.ndim != 1 or x.size == 0:
        row["reason"] = "EMPTY_TRIGGER"
        return row
    if expected_samples is not None and x.size != expected_samples:
        row["reason"] = "SHORT_READ"
        return row
    if overflow:
        row["reason"] = "ADC_OVERFLOW"
        return row
    if min_stable_samples < 1 or min_tail_samples < min_stable_samples or hysteresis < 0:
        raise ValueError("invalid trigger validation limits")
    upper, lower = threshold + hysteresis / 2, threshold - hysteresis / 2
    # Integer ADC codes compared to fractional thresholds otherwise promote
    # every sample to float64. Ceil/floor give exactly the same predicates.
    if np.issubdtype(x.dtype, np.integer):
        limits = np.iinfo(x.dtype)
        if np.isfinite(upper) and limits.min <= upper <= limits.max:
            upper = int(np.ceil(upper))
        if np.isfinite(lower) and limits.min <= lower <= limits.max:
            lower = int(np.floor(lower))
    high = _runs(x >= upper, min_stable_samples)
    low = _runs(x <= lower, min_stable_samples)
    if not high:
        row["reason"] = "MISSING_RISE"
        return row
    rise = high[0][0]
    row["rise_index"] = rise
    if not any(b <= rise for a, b in low):
        row["reason"] = "LEFT_CENSORED"
        return row
    fall_run = next(((a, b) for a, b in low if a >= high[0][1]), None)
    if fall_run is None:
        row["reason"] = "RIGHT_CENSORED"
        return row
    fall, low_end = fall_run
    row.update(fall_index=fall, pulse_width_samples=fall-rise,
               tail_samples=low_end-fall, complete_rise_fall=1)
    if any(a >= fall for a, b in high):
        row["reason"] = "MULTIPLE_PULSES"
    elif low_end-fall < min_tail_samples:
        row["reason"] = "INSUFFICIENT_TAIL"
    else:
        row.update(valid=True, reason="OK")
    return row


def capture_quality_error(meta: dict, expected_traces: int | None = None) -> str | None:
    """Fail closed for old, partial, synthetic-edge or invalid acquisition data."""
    if meta.get("trigger_quality_schema") != SCHEMA:
        return "MISSING_OBSERVED_EDGE_EVIDENCE"
    count = int(meta.get("completed_traces", 0) or 0)
    if not meta.get("complete") or count <= 0:
        return str(meta.get("trigger_failure_reason") or "INCOMPLETE_CAPTURE")
    if expected_traces is not None and count != expected_traces:
        return "TRACE_COUNT_MISMATCH"
    if int(meta.get("valid_trigger_traces", 0)) != count or int(meta.get("invalid_trigger_traces", 0)):
        return "INVALID_TRIGGER_TRACES"
    if not meta.get("trigger_valid") or meta.get("complete_rise_fall") != 1:
        return "MISSING_COMPLETE_PULSE"
    return None
