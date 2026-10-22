"""Label-blind automatic window selection for global trace alignment.

The selector deliberately never opens ``labels.npy`` and never computes TVLA.
It chooses one coarse and one nested fine window from waveform repeatability,
correlation-peak uniqueness, and boundary-hit rate.  The returned parameters
can be passed directly to :func:`tvla_streaming.compute_global_alignment_shifts`.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _open_verified_trace_memmap(path: Path) -> np.ndarray:
    """Reject cleaned/truncated NPY placeholders before asking Windows to map them."""
    with path.open("rb") as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
        else:
            shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
        data_offset = int(stream.tell())
    expected = data_offset + int(np.prod(shape, dtype=np.int64)) * int(np.dtype(dtype).itemsize)
    actual = int(path.stat().st_size)
    if actual < expected:
        raise RuntimeError(
            f"traces.npy was cleaned or truncated: actual={actual} expected={expected} bytes"
        )
    try:
        return np.load(path, mmap_mode="r")
    except OSError as exc:
        raise RuntimeError(f"cannot memory-map saved traces: {path}: {exc}") from exc


def _read_json(path: Path) -> Dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _calibration_indices(trace_count: int, requested: int) -> np.ndarray:
    count = min(int(trace_count), max(8, int(requested)))
    indices = np.linspace(0, int(trace_count) - 1, count, dtype=np.int64)
    return np.unique(indices)


def _rms_envelope(row: np.ndarray, factor: int) -> np.ndarray:
    usable = (int(row.size) // int(factor)) * int(factor)
    values = np.asarray(row[:usable], dtype=np.float64).reshape(-1, int(factor))
    return np.sqrt(np.mean(values * values, axis=1))


def _block_mean(row: np.ndarray, factor: int) -> np.ndarray:
    usable = (int(row.size) // int(factor)) * int(factor)
    values = np.asarray(row[:usable], dtype=np.float64).reshape(-1, int(factor))
    return np.mean(values, axis=1)


def _correlations(rows: np.ndarray, reference: np.ndarray) -> np.ndarray:
    ref = np.asarray(reference, dtype=np.float64)
    ref = ref - np.mean(ref, dtype=np.float64)
    ref_norm = float(np.sqrt(np.dot(ref, ref)))
    if ref_norm <= 0.0:
        return np.zeros(int(rows.shape[0]), dtype=np.float64)
    centered = np.asarray(rows, dtype=np.float64) - np.mean(rows, axis=1)[:, None]
    norms = np.sqrt(np.einsum("ij,ij->i", centered, centered, optimize=False))
    result = np.zeros(int(rows.shape[0]), dtype=np.float64)
    np.divide(centered @ ref, norms * ref_norm, out=result, where=norms > 0.0)
    return result


def _sliding_normalized_correlation(reference: np.ndarray, extended: np.ndarray) -> np.ndarray:
    ref = np.asarray(reference, dtype=np.float64)
    ext = np.asarray(extended, dtype=np.float64)
    ref = ref - np.mean(ref, dtype=np.float64)
    ref_norm = float(np.sqrt(np.dot(ref, ref)))
    valid = int(ext.size) - int(ref.size) + 1
    if ref_norm <= 0.0 or valid <= 0:
        return np.zeros(max(0, valid), dtype=np.float64)
    numerators = np.correlate(ext, ref, mode="valid")
    prefix = np.empty(int(ext.size) + 1, dtype=np.float64)
    prefix_sq = np.empty(int(ext.size) + 1, dtype=np.float64)
    prefix[0] = 0.0
    prefix_sq[0] = 0.0
    np.cumsum(ext, dtype=np.float64, out=prefix[1:])
    np.cumsum(ext * ext, dtype=np.float64, out=prefix_sq[1:])
    width = int(ref.size)
    sums = prefix[width:] - prefix[:-width]
    energies = prefix_sq[width:] - prefix_sq[:-width] - sums * sums / float(width)
    np.maximum(energies, 0.0, out=energies)
    result = np.zeros(valid, dtype=np.float64)
    np.divide(numerators, ref_norm * np.sqrt(energies), out=result, where=energies > 0.0)
    return result


def _candidate_reference(rows: np.ndarray) -> Tuple[int, np.ndarray, np.ndarray]:
    template = np.median(rows, axis=0)
    correlations = _correlations(rows, template)
    local_index = int(np.argmax(correlations))
    return local_index, np.asarray(rows[local_index], dtype=np.float64), correlations


def _alignment_quality(
    data: np.ndarray,
    start: int,
    width: int,
    reference: np.ndarray,
    max_shift: int,
    ambiguity_exclusion: int,
) -> Dict[str, object]:
    best_correlations: List[float] = []
    ambiguity_gaps: List[float] = []
    shifts: List[int] = []
    boundary_hits = 0
    for row in data:
        extended = row[start - max_shift:start + width + max_shift]
        scores = _sliding_normalized_correlation(reference, extended)
        best_index = int(np.argmax(scores))
        best = float(scores[best_index])
        shift = best_index - int(max_shift)
        mask = np.ones(int(scores.size), dtype=bool)
        lo = max(0, best_index - int(ambiguity_exclusion))
        hi = min(int(scores.size), best_index + int(ambiguity_exclusion) + 1)
        mask[lo:hi] = False
        second = float(np.max(scores[mask])) if bool(np.any(mask)) else best
        best_correlations.append(best)
        ambiguity_gaps.append(max(0.0, best - second))
        shifts.append(shift)
        if abs(shift) >= int(max_shift):
            boundary_hits += 1
    corr = np.asarray(best_correlations, dtype=np.float64)
    gaps = np.asarray(ambiguity_gaps, dtype=np.float64)
    shift_values = np.asarray(shifts, dtype=np.int64)
    boundary_rate = float(boundary_hits / max(1, int(data.shape[0])))
    score = (
        0.55 * float(np.median(corr))
        + 0.25 * float(np.quantile(corr, 0.25))
        + 0.20 * float(np.median(gaps))
        - 0.75 * boundary_rate
    )
    return {
        "score": score,
        "median_correlation": float(np.median(corr)),
        "q25_correlation": float(np.quantile(corr, 0.25)),
        "median_ambiguity_gap": float(np.median(gaps)),
        "boundary_hit_rate": boundary_rate,
        "shift_min_bins": int(np.min(shift_values)),
        "shift_max_bins": int(np.max(shift_values)),
        "shift_median_bins": float(np.median(shift_values)),
        "shifts_bins": shifts,
    }


def _initial_candidates(
    data: np.ndarray,
    discovery_rows: np.ndarray,
    starts: Sequence[int],
    width: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    structure_values: List[float] = []
    for start in starts:
        window = data[discovery_rows, int(start):int(start) + int(width)]
        local_reference, reference, zero_corr = _candidate_reference(window)
        structure = float(np.std(reference, dtype=np.float64))
        structure_values.append(structure)
        rows.append({
            "start_bin": int(start),
            "reference_discovery_row": int(local_reference),
            "median_zero_lag_correlation": float(np.median(zero_corr)),
            "q25_zero_lag_correlation": float(np.quantile(zero_corr, 0.25)),
            "reference_std": structure,
        })
    if not rows:
        return []
    positive = np.asarray([value for value in structure_values if value > 0.0], dtype=np.float64)
    if positive.size == 0:
        return []
    floor = float(np.quantile(positive, 0.10))
    ceiling = max(floor, float(np.quantile(positive, 0.90)))
    span = max(ceiling - floor, np.finfo(np.float64).eps)
    for row in rows:
        structure = float(row["reference_std"])
        structure_score = min(1.0, max(0.0, (structure - floor) / span))
        row["structure_score"] = structure_score
        row["initial_score"] = (
            0.60 * float(row["median_zero_lag_correlation"])
            + 0.35 * float(row["q25_zero_lag_correlation"])
            + 0.05 * structure_score
        )
        row["flat_rejected"] = bool(structure < floor)
    return rows


def _derive_search_stop_us(time_us: np.ndarray, metadata: Dict[str, object]) -> float:
    interval_us = float(np.median(np.diff(time_us)))
    pre_trigger = int(metadata.get("pre_trigger", 0) or 0)
    minimum_pulse = int(metadata.get("pulse_width_min_samples", 0) or 0)
    if minimum_pulse > 0:
        end_index = min(int(time_us.size) - 1, pre_trigger + minimum_pulse - 1)
        return float(time_us[end_index])
    calibrated = float(metadata.get("calibrated_pulse_us", 0.0) or 0.0)
    if calibrated > 0.0:
        trigger_index = int(np.searchsorted(time_us, 0.0))
        end_index = min(int(time_us.size) - 1, trigger_index + int(calibrated / interval_us))
        return float(time_us[end_index])
    return float(time_us[-1])


def select_alignment_windows(
    dataset: Path,
    output_dir: Path,
    *,
    search_start_us: float = 20.0,
    search_stop_us: Optional[float] = None,
    coarse_window_us: float = 140.0,
    fine_window_us: float = 20.0,
    coarse_step_us: float = 5.0,
    fine_step_us: float = 1.0,
    calibration_traces: int = 48,
    coarse_scan_resolution_us: float = 0.2,
    fine_scan_resolution_us: float = 0.04,
    max_shift_us: float = 12.0,
    alignment_downsample: int = 10,
    refine_radius_samples: int = 20,
    shortlist_size: int = 16,
) -> Dict[str, object]:
    """Choose coarse/fine alignment windows without loading group labels."""
    dataset = Path(dataset).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    traces = _open_verified_trace_memmap(dataset / "traces.npy")
    time_us = np.asarray(np.load(dataset / "time_us.npy"), dtype=np.float64)
    if traces.ndim != 2 or int(traces.shape[1]) != int(time_us.size):
        raise RuntimeError(f"alignment dataset shape mismatch: traces={traces.shape} time={time_us.shape}")
    if int(time_us.size) < 3:
        raise RuntimeError("alignment dataset is too short")

    interval_us = float(np.median(np.diff(time_us)))
    if not math.isfinite(interval_us) or interval_us <= 0.0:
        raise RuntimeError("time_us.npy must be strictly increasing")
    metadata = _read_json(dataset / "metadata.json")
    derived_stop = _derive_search_stop_us(time_us, metadata)
    requested_stop = derived_stop if search_stop_us is None or float(search_stop_us) <= 0.0 else float(search_stop_us)
    legal_start = max(float(search_start_us), float(time_us[0]) + float(max_shift_us))
    legal_stop = min(requested_stop, float(time_us[-1]) - float(max_shift_us))
    if legal_stop - legal_start < float(coarse_window_us):
        raise RuntimeError(
            f"automatic alignment search region is too short: {legal_start:.3f}..{legal_stop:.3f} us"
        )

    indices = _calibration_indices(int(traces.shape[0]), int(calibration_traces))
    discovery_rows = np.arange(0, int(indices.size), 2, dtype=np.int64)
    validation_rows = np.arange(1, int(indices.size), 2, dtype=np.int64)
    if validation_rows.size < 4:
        validation_rows = discovery_rows.copy()

    coarse_factor = max(1, int(round(float(coarse_scan_resolution_us) / interval_us)))
    coarse_resolution = interval_us * float(coarse_factor)
    envelope_rows = []
    for trace_index in indices:
        envelope_rows.append(_rms_envelope(traces[int(trace_index)], coarse_factor))
    coarse_data = np.stack(envelope_rows, axis=0)
    coarse_time = time_us[np.arange(int(coarse_data.shape[1]), dtype=np.int64) * coarse_factor]
    max_shift_bins = max(1, int(round(float(max_shift_us) / coarse_resolution)))
    coarse_width = max(8, int(round(float(coarse_window_us) / coarse_resolution)))
    coarse_step = max(1, int(round(float(coarse_step_us) / coarse_resolution)))
    first_start = int(np.searchsorted(coarse_time, legal_start))
    last_stop = int(np.searchsorted(coarse_time, legal_stop, side="right"))
    first_start = max(first_start, max_shift_bins)
    last_start = min(int(coarse_data.shape[1]) - coarse_width - max_shift_bins, last_stop - coarse_width)
    starts = list(range(first_start, last_start + 1, coarse_step))
    if starts and starts[-1] != last_start:
        starts.append(last_start)
    candidates = _initial_candidates(coarse_data, discovery_rows, starts, coarse_width)
    viable = [row for row in candidates if not bool(row["flat_rejected"])]
    if not viable:
        raise RuntimeError("no non-flat automatic coarse alignment window was found")
    viable.sort(key=lambda row: float(row["initial_score"]), reverse=True)

    validated: List[Dict[str, object]] = []
    ambiguity_exclusion = max(1, int(round(1.0 / coarse_resolution)))
    for row in viable[:max(1, int(shortlist_size))]:
        start = int(row["start_bin"])
        discovery_window = coarse_data[discovery_rows, start:start + coarse_width]
        local_reference, reference, _ = _candidate_reference(discovery_window)
        quality = _alignment_quality(
            coarse_data[validation_rows], start, coarse_width, reference,
            max_shift_bins, ambiguity_exclusion,
        )
        candidate = dict(row)
        candidate.update({key: value for key, value in quality.items() if key != "shifts_bins"})
        candidate["reference_trace_index"] = int(indices[int(discovery_rows[local_reference])])
        candidate["start_us"] = float(coarse_time[start])
        candidate["stop_us"] = float(coarse_time[start + coarse_width - 1] + coarse_resolution)
        validated.append(candidate)
    validated.sort(key=lambda row: float(row["score"]), reverse=True)
    selected_coarse = validated[0]
    coarse_start_bin = int(selected_coarse["start_bin"])
    reference_index = int(selected_coarse["reference_trace_index"])

    reference_position = int(np.flatnonzero(indices == reference_index)[0])
    reference_coarse = coarse_data[reference_position, coarse_start_bin:coarse_start_bin + coarse_width]
    scan_resolution_quality = _alignment_quality(
        coarse_data, coarse_start_bin, coarse_width, reference_coarse,
        max_shift_bins, ambiguity_exclusion,
    )
    scan_resolution_quality.pop("shifts_bins")

    # Recompute calibration shifts at the exact downsample used by the real
    # aligner.  The broad search above stays cheap, while fine-window selection
    # is not polluted by the coarser search grid's quantization error.
    actual_factor = max(1, int(alignment_downsample))
    actual_resolution = interval_us * float(actual_factor)
    actual_max_shift_raw = int(round(float(max_shift_us) / interval_us))
    actual_max_shift_raw = max(actual_factor, (actual_max_shift_raw // actual_factor) * actual_factor)
    actual_max_shift_bins = actual_max_shift_raw // actual_factor
    coarse_start_raw = int(np.searchsorted(time_us, float(coarse_time[coarse_start_bin])))
    coarse_stop_requested = int(np.searchsorted(time_us, float(selected_coarse["stop_us"])))
    coarse_width_raw = (
        (coarse_stop_requested - coarse_start_raw) // actual_factor
    ) * actual_factor
    coarse_stop_raw = coarse_start_raw + coarse_width_raw
    actual_rows = []
    for trace_index in indices:
        actual_rows.append(_rms_envelope(
            traces[
                int(trace_index),
                coarse_start_raw - actual_max_shift_raw:coarse_stop_raw + actual_max_shift_raw,
            ],
            actual_factor,
        ))
    actual_data = np.stack(actual_rows, axis=0)
    actual_width = coarse_width_raw // actual_factor
    reference_actual = actual_data[
        reference_position,
        actual_max_shift_bins:actual_max_shift_bins + actual_width,
    ]
    actual_quality = _alignment_quality(
        actual_data,
        actual_max_shift_bins,
        actual_width,
        reference_actual,
        actual_max_shift_bins,
        max(1, int(round(1.0 / actual_resolution))),
    )
    coarse_shifts = np.asarray(actual_quality.pop("shifts_bins"), dtype=np.int64)

    fine_factor = max(1, int(round(float(fine_scan_resolution_us) / interval_us)))
    fine_resolution = interval_us * float(fine_factor)
    fine_rows = []
    for position, trace_index in enumerate(indices):
        shift_raw = int(coarse_shifts[position]) * actual_factor
        source_start = coarse_start_raw + shift_raw
        source_stop = source_start + coarse_width_raw
        fine_rows.append(_block_mean(traces[int(trace_index), source_start:source_stop], fine_factor))
    fine_data = np.stack(fine_rows, axis=0)
    fine_width = max(8, int(round(float(fine_window_us) / fine_resolution)))
    fine_step = max(1, int(round(float(fine_step_us) / fine_resolution)))
    fine_last_start = int(fine_data.shape[1]) - fine_width
    refine_bins = max(1, int(math.ceil(int(refine_radius_samples) / float(fine_factor))))
    first_fine_start = refine_bins
    last_fine_start = fine_last_start - refine_bins
    fine_starts = list(range(first_fine_start, last_fine_start + 1, fine_step))
    if fine_starts and fine_starts[-1] != last_fine_start:
        fine_starts.append(last_fine_start)
    fine_candidates = _initial_candidates(fine_data, discovery_rows, fine_starts, fine_width)
    fine_viable = [row for row in fine_candidates if not bool(row["flat_rejected"])]
    if not fine_viable:
        raise RuntimeError("no non-flat automatic fine alignment window was found")
    fine_viable.sort(key=lambda row: float(row["initial_score"]), reverse=True)
    fine_validated: List[Dict[str, object]] = []
    for row in fine_viable[:max(1, int(shortlist_size))]:
        start = int(row["start_bin"])
        discovery_window = fine_data[discovery_rows, start:start + fine_width]
        _, reference, _ = _candidate_reference(discovery_window)
        quality = _alignment_quality(
            fine_data[validation_rows], start, fine_width, reference,
            refine_bins, 1,
        )
        candidate = dict(row)
        candidate.update({key: value for key, value in quality.items() if key != "shifts_bins"})
        candidate["relative_start_us"] = float(start * fine_resolution)
        candidate["relative_stop_us"] = float((start + fine_width) * fine_resolution)
        fine_validated.append(candidate)
    fine_validated.sort(key=lambda row: float(row["score"]), reverse=True)
    selected_fine = fine_validated[0]

    align_start_us = float(coarse_time[coarse_start_bin])
    align_stop_us = float(align_start_us + coarse_width * coarse_resolution)
    fine_start_us = float(align_start_us + int(selected_fine["start_bin"]) * fine_resolution)
    fine_stop_us = float(fine_start_us + fine_width * fine_resolution)
    result: Dict[str, object] = {
        "method": "label_blind_held_out_waveform_repeatability_v1",
        "labels_loaded": False,
        "tvla_used_for_selection": False,
        "window_frozen_before_tvla_analysis": True,
        "dataset": str(dataset),
        "trace_shape": [int(traces.shape[0]), int(traces.shape[1])],
        "sample_interval_us": interval_us,
        "calibration_trace_indices": [int(value) for value in indices],
        "discovery_trace_indices": [int(indices[value]) for value in discovery_rows],
        "validation_trace_indices": [int(indices[value]) for value in validation_rows],
        "search_start_us": legal_start,
        "search_stop_us": legal_stop,
        "search_stop_source": "configured" if search_stop_us is not None and float(search_stop_us) > 0.0 else "minimum_trigger_pulse_or_trace_end",
        "reference_trace_index": reference_index,
        "align_start_us": align_start_us,
        "align_stop_us": align_stop_us,
        "fine_start_us": fine_start_us,
        "fine_stop_us": fine_stop_us,
        "max_shift_us": float(max_shift_us),
        "coarse_scan_resolution_us": coarse_resolution,
        "actual_alignment_coarse_resolution_us": actual_resolution,
        "fine_scan_resolution_us": fine_resolution,
        "selected_coarse": {key: value for key, value in selected_coarse.items() if key != "start_bin"},
        "selected_fine": {key: value for key, value in selected_fine.items() if key != "start_bin"},
        "selected_coarse_scan_resolution_quality": scan_resolution_quality,
        "selected_coarse_actual_alignment_quality": actual_quality,
        "coarse_shortlist": [
            {key: value for key, value in row.items() if key != "start_bin"}
            for row in validated[:10]
        ],
        "fine_shortlist": [
            {key: value for key, value in row.items() if key != "start_bin"}
            for row in fine_validated[:10]
        ],
    }
    (output_dir / "alignment_window_selection.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    del traces
    gc.collect()
    return result


def select_alignment_windows_from_config(
    dataset: Path,
    output_dir: Path,
    config: object,
) -> Dict[str, object]:
    """Small adapter that keeps selector configuration out of the pipeline."""
    configured_stop = float(getattr(config, "https_alignment_auto_search_stop_us", 0.0))
    return select_alignment_windows(
        dataset,
        output_dir,
        search_start_us=float(
            getattr(config, "https_alignment_auto_search_start_us", 20.0)
        ),
        search_stop_us=None if configured_stop <= 0.0 else configured_stop,
        coarse_window_us=float(
            getattr(config, "https_alignment_auto_coarse_window_us", 140.0)
        ),
        fine_window_us=float(
            getattr(config, "https_alignment_auto_fine_window_us", 20.0)
        ),
        coarse_step_us=float(
            getattr(config, "https_alignment_auto_coarse_step_us", 5.0)
        ),
        fine_step_us=float(
            getattr(config, "https_alignment_auto_fine_step_us", 1.0)
        ),
        calibration_traces=int(
            getattr(config, "https_alignment_auto_calibration_traces", 48)
        ),
        coarse_scan_resolution_us=float(
            getattr(config, "https_alignment_auto_coarse_scan_resolution_us", 0.2)
        ),
        fine_scan_resolution_us=float(
            getattr(config, "https_alignment_auto_fine_scan_resolution_us", 0.04)
        ),
        max_shift_us=float(getattr(config, "https_alignment_max_shift_us", 12.0)),
        alignment_downsample=int(getattr(config, "https_alignment_downsample", 10)),
        refine_radius_samples=int(
            getattr(config, "https_alignment_refine_radius_samples", 20)
        ),
        shortlist_size=int(
            getattr(config, "https_alignment_auto_shortlist_size", 16)
        ),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--search-start-us", type=float, default=20.0)
    parser.add_argument("--search-stop-us", type=float, default=0.0)
    parser.add_argument("--coarse-window-us", type=float, default=140.0)
    parser.add_argument("--fine-window-us", type=float, default=20.0)
    parser.add_argument("--calibration-traces", type=int, default=48)
    parser.add_argument("--max-shift-us", type=float, default=12.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = select_alignment_windows(
        args.dataset,
        args.output_dir,
        search_start_us=args.search_start_us,
        search_stop_us=(None if args.search_stop_us <= 0.0 else args.search_stop_us),
        coarse_window_us=args.coarse_window_us,
        fine_window_us=args.fine_window_us,
        calibration_traces=args.calibration_traces,
        max_shift_us=args.max_shift_us,
    )
    print(json.dumps({
        "reference_trace_index": result["reference_trace_index"],
        "align_start_us": result["align_start_us"],
        "align_stop_us": result["align_stop_us"],
        "fine_start_us": result["fine_start_us"],
        "fine_stop_us": result["fine_stop_us"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
