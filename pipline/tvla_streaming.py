"""Streaming multi-view TVLA accumulators for large HTTPS trace datasets.

The trace file is stored row-major.  Reading a few complete trace rows at a
time is therefore substantially cheaper than repeatedly gathering every row
for hundreds of tiny column blocks.  All alignment views consume the same
in-memory row batch, so the backing ``traces.npy`` is scanned once for
first-order TVLA and once more only when same-point second-order TVLA is
enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Dict, Mapping, Optional

import numpy as np


def _rms_envelope(raw: np.ndarray, factor: int) -> np.ndarray:
    if raw.dtype == np.int16 and 0 < factor < 2**23:
        from tvla_acceleration import get_adc_envelope
        kernel = get_adc_envelope()
        if kernel is not None:
            return kernel(raw, factor)
    length = (int(len(raw)) // int(factor)) * int(factor)
    squared = np.square(raw[:length], dtype=np.float64).reshape(-1, int(factor))
    return np.sqrt(np.mean(squared, axis=1))


def compute_global_alignment_shifts(
    dataset: Path,
    output_dir: Path,
    *,
    reference_index: int,
    align_start_us: float,
    align_stop_us: float,
    fine_start_us: float,
    fine_stop_us: float,
    max_shift_us: float,
    downsample: int,
    refine_radius_samples: int,
    reference_dataset: Optional[Path] = None,
) -> Dict[str, object]:
    """Compute the existing coarse/fine global alignment without a TVLA scan.

    This preserves the original envelope correlation and fine raw-sample
    correlation, but hoists the reference FFT out of the per-trace loop and
    uses prefix sums plus ``np.correlate`` for the fine search.  TVLA is
    intentionally deferred to :func:`compute_streaming_tvla_views`, avoiding
    a redundant whole-file application pass.
    """
    started = time.perf_counter()
    dataset = Path(dataset).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    traces = np.load(dataset / "traces.npy", mmap_mode="r")
    labels = np.asarray(np.load(dataset / "labels.npy"), dtype=np.int8)
    time_us = np.asarray(np.load(dataset / "time_us.npy"), dtype=np.float64)
    if traces.ndim != 2 or traces.shape[0] != labels.size or traces.shape[1] != time_us.size:
        raise RuntimeError("trace/label/time shapes do not match")
    reference_traces = traces
    if reference_dataset is not None:
        reference_dataset = Path(reference_dataset).resolve()
        reference_traces = np.load(reference_dataset / "traces.npy", mmap_mode="r")
        reference_time = np.load(reference_dataset / "time_us.npy")
        if reference_traces.ndim != 2 or reference_traces.shape[1] != traces.shape[1] or not np.array_equal(reference_time, time_us):
            raise ValueError("independent alignment calibration has a different sampling grid")
    if not (0 <= int(reference_index) < int(reference_traces.shape[0])):
        raise ValueError("reference index is outside the dataset")

    interval_ns = float(np.median(np.diff(time_us)) * 1000.0)
    factor = max(1, int(downsample))
    max_shift_raw = int(round(float(max_shift_us) * 1000.0 / interval_ns))
    max_shift_raw = (max_shift_raw // factor) * factor
    max_shift_bins = max_shift_raw // factor
    align_start = int(np.searchsorted(time_us, float(align_start_us)))
    requested_stop = int(np.searchsorted(time_us, float(align_stop_us)))
    align_length = ((requested_stop - align_start) // factor) * factor
    align_stop = align_start + align_length
    if align_start - max_shift_raw < 0 or align_stop + max_shift_raw > traces.shape[1]:
        raise ValueError("alignment search window exceeds trace bounds")

    fine_start = int(np.searchsorted(time_us, float(fine_start_us)))
    fine_stop = int(np.searchsorted(time_us, float(fine_stop_us)))
    refine = max(0, int(refine_radius_samples))
    if fine_start < align_start or fine_stop > align_stop or fine_stop <= fine_start:
        raise ValueError("fine alignment window must be inside the coarse window")

    reference_raw = np.asarray(reference_traces[int(reference_index)], dtype=np.int16)
    reference_envelope = _rms_envelope(reference_raw[align_start:align_stop], factor)
    reference_centered = reference_envelope - np.mean(reference_envelope, dtype=np.float64)
    reference_norm = float(np.sqrt(np.dot(reference_centered, reference_centered)))
    if reference_norm == 0.0:
        raise RuntimeError("reference alignment window has zero variance")

    extended_envelope_length = int(reference_envelope.size) + 2 * max_shift_bins
    fft_length = 1 << (extended_envelope_length + int(reference_envelope.size) - 2).bit_length()
    reference_fft = np.fft.rfft(reference_centered[::-1], fft_length)

    reference_fine = np.asarray(reference_raw[fine_start:fine_stop], dtype=np.float64)
    reference_fine -= np.mean(reference_fine, dtype=np.float64)
    reference_fine_norm = float(np.sqrt(np.dot(reference_fine, reference_fine)))
    if reference_fine_norm == 0.0:
        raise RuntimeError("fine reference window has zero variance")

    shifts = np.empty(traces.shape[0], dtype=np.int32)
    correlations = np.empty(traces.shape[0], dtype=np.float64)
    for index in range(int(traces.shape[0])):
        extended_raw = np.asarray(
            traces[index, align_start - max_shift_raw:align_stop + max_shift_raw],
            dtype=np.int16,
        )
        extended = _rms_envelope(extended_raw, factor)
        convolution = np.fft.irfft(
            np.fft.rfft(extended, fft_length) * reference_fft,
            fft_length,
        )
        valid_length = int(extended.size) - int(reference_envelope.size) + 1
        numerator = convolution[
            int(reference_envelope.size) - 1:
            int(reference_envelope.size) - 1 + valid_length
        ]
        prefix = np.empty(int(extended.size) + 1, dtype=np.float64)
        prefix[0] = 0.0
        np.cumsum(extended, dtype=np.float64, out=prefix[1:])
        window_sums = prefix[int(reference_envelope.size):] - prefix[:-int(reference_envelope.size)]
        squared = np.square(extended, dtype=np.float64)
        prefix_sq = np.empty(int(extended.size) + 1, dtype=np.float64)
        prefix_sq[0] = 0.0
        np.cumsum(squared, dtype=np.float64, out=prefix_sq[1:])
        window_square_sums = (
            prefix_sq[int(reference_envelope.size):]
            - prefix_sq[:-int(reference_envelope.size)]
        )
        window_energy = np.maximum(
            window_square_sums
            - window_sums * window_sums / float(reference_envelope.size),
            0.0,
        )
        denominator = reference_norm * np.sqrt(window_energy)
        coarse_scores = np.zeros(valid_length, dtype=np.float64)
        np.divide(numerator, denominator, out=coarse_scores, where=denominator > 0.0)
        coarse_shift = (int(np.argmax(coarse_scores)) - max_shift_bins) * factor

        source_start = fine_start + coarse_shift - refine
        source_stop = fine_stop + coarse_shift + refine
        fine_extended = np.asarray(traces[index, source_start:source_stop], dtype=np.float64)
        fine_length = int(reference_fine.size)
        fine_prefix = np.empty(int(fine_extended.size) + 1, dtype=np.float64)
        fine_prefix[0] = 0.0
        np.cumsum(fine_extended, dtype=np.float64, out=fine_prefix[1:])
        fine_window_sums = fine_prefix[fine_length:] - fine_prefix[:-fine_length]
        fine_squared = np.square(fine_extended, dtype=np.float64)
        fine_prefix_sq = np.empty(int(fine_extended.size) + 1, dtype=np.float64)
        fine_prefix_sq[0] = 0.0
        np.cumsum(fine_squared, dtype=np.float64, out=fine_prefix_sq[1:])
        fine_window_square_sums = fine_prefix_sq[fine_length:] - fine_prefix_sq[:-fine_length]
        fine_energy = np.maximum(
            fine_window_square_sums - fine_window_sums * fine_window_sums / float(fine_length),
            0.0,
        )
        fine_numerator = np.correlate(fine_extended, reference_fine, mode="valid")
        fine_denominator = reference_fine_norm * np.sqrt(fine_energy)
        fine_scores = np.zeros(fine_numerator.size, dtype=np.float64)
        np.divide(
            fine_numerator,
            fine_denominator,
            out=fine_scores,
            where=fine_denominator > 0.0,
        )
        fine_offset = int(np.argmax(fine_scores)) - refine
        shifts[index] = coarse_shift + fine_offset
        correlations[index] = float(np.max(fine_scores))

    np.save(output_dir / "shifts_samples.npy", shifts)
    np.save(output_dir / "alignment_correlations.npy", correlations)
    crop = int(np.max(np.abs(shifts))) + 1
    result: Dict[str, object] = {
        "method": "one_global_reference_whole_trace_integer_shift",
        "analysis_io": "shifts_only_tvla_deferred_to_shared_stream",
        "reference_trace_index": int(reference_index),
        "reference_trace_label_recorded_only_not_used_for_grouping": int(labels[int(reference_index)]),
        "groups_aligned_separately": False,
        "alignment_used_labels": False,
        "align_start_us": float(time_us[align_start]),
        "align_stop_us": float(time_us[align_stop - 1]),
        "fine_start_us": float(time_us[fine_start]),
        "fine_stop_us": float(time_us[fine_stop - 1]),
        "max_search_shift_samples": int(max_shift_raw),
        "shift_min_samples": int(shifts.min()),
        "shift_max_samples": int(shifts.max()),
        "shift_median_samples": float(np.median(shifts)),
        "alignment_correlation_min": float(correlations.min()),
        "alignment_correlation_median": float(np.median(correlations)),
        "alignment_correlation_max": float(correlations.max()),
        "edge_crop_samples": crop,
        "n_fixed": int(np.count_nonzero(labels == 0)),
        "n_random": int(np.count_nonzero(labels == 1)),
        "elapsed_seconds": float(time.perf_counter() - started),
    }
    (output_dir / "alignment_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


@dataclass(frozen=True)
class StreamingTvlaView:
    """One label-independent integer-shift view of the same trace dataset."""

    shifts_path: Optional[Path] = None


@dataclass
class _PreparedView:
    name: str
    shifts: Optional[np.ndarray]
    crop: int
    width: int
    time_view: np.ndarray
    block_sums: np.ndarray
    block_square_sums: np.ndarray
    block_pooled_means: Optional[np.ndarray] = None
    full_pooled_mean: Optional[np.ndarray] = None
    tvla1_t: Optional[np.ndarray] = None
    stability_tvla1: Optional[list[np.ndarray]] = None
    tvla2_t: Optional[np.ndarray] = None
    stability_tvla2: Optional[list[np.ndarray]] = None


def _welch_from_sums(
    sum_fixed: np.ndarray,
    square_sum_fixed: np.ndarray,
    n_fixed: int,
    sum_random: np.ndarray,
    square_sum_random: np.ndarray,
    n_random: int,
) -> np.ndarray:
    """Compute Welch t from sufficient statistics, fixed minus random."""
    if int(n_fixed) < 2 or int(n_random) < 2:
        raise ValueError("Need at least two fixed and two random traces for Welch t-test")

    nf = float(n_fixed)
    nr = float(n_random)
    mean_fixed = sum_fixed / nf
    mean_random = sum_random / nr
    variance_fixed = (square_sum_fixed - (sum_fixed * sum_fixed) / nf) / (nf - 1.0)
    variance_random = (square_sum_random - (sum_random * sum_random) / nr) / (nr - 1.0)

    # Round-off can make a mathematically zero variance a tiny negative value.
    np.maximum(variance_fixed, 0.0, out=variance_fixed)
    np.maximum(variance_random, 0.0, out=variance_random)
    denominator = np.sqrt(variance_fixed / nf + variance_random / nr)
    t_values = np.zeros(sum_fixed.shape, dtype=np.float64)
    np.divide(
        mean_fixed - mean_random,
        denominator,
        out=t_values,
        where=denominator > 0.0,
    )
    return t_values.astype(np.float32, copy=False)


def _accumulate_partitioned(
    values: np.ndarray,
    keys: np.ndarray,
    sums: np.ndarray,
    square_sums: np.ndarray,
) -> None:
    """Accumulate one row batch into block/group arrays.

    ``keys`` is ``block_index * 2 + label``.  Each row is copied into exactly
    one small partition; reductions run over complete sample vectors and stay
    in NumPy's native loops.
    """
    for key in np.unique(keys):
        positions = np.flatnonzero(keys == key)
        if positions.size == 0:
            continue
        selected = values[positions]
        block_index = int(key) // 2
        group_index = int(key) & 1
        sums[block_index, group_index] += np.sum(selected, axis=0, dtype=np.float64)
        square_sums[block_index, group_index] += np.einsum(
            "ij,ij->j", selected, selected, optimize=False
        )


def _fill_aligned_workspace(
    destination: np.ndarray,
    raw_rows: np.ndarray,
    shifts: Optional[np.ndarray],
    crop: int,
    source_offset: int = 0,
) -> None:
    """Copy one view into a reusable float64 workspace."""
    width = int(destination.shape[1])
    if shifts is None:
        np.copyto(destination, raw_rows[:, source_offset:source_offset + width], casting="unsafe")
        return
    for row_index, shift in enumerate(shifts):
        source_start = int(crop) + int(shift) + int(source_offset)
        np.copyto(
            destination[row_index],
            raw_rows[row_index, source_start:source_start + width],
            casting="unsafe",
        )


def _make_trace_blocks(labels: np.ndarray, requested_blocks: int) -> tuple[np.ndarray, np.ndarray]:
    fixed_idx = np.flatnonzero(labels == 0)
    random_idx = np.flatnonzero(labels == 1)
    if fixed_idx.size < 2 or random_idx.size < 2:
        raise RuntimeError("TVLA needs at least two fixed and two random traces")

    requested_blocks = max(0, int(requested_blocks))
    if requested_blocks > 1:
        block_count = min(
            requested_blocks,
            int(fixed_idx.size // 2),
            int(random_idx.size // 2),
        )
    else:
        block_count = 0
    accumulator_blocks = block_count if block_count else 1
    trace_blocks = np.zeros(labels.size, dtype=np.int32)
    if block_count:
        for block_index, positions in enumerate(np.array_split(fixed_idx, block_count)):
            trace_blocks[positions] = block_index
        for block_index, positions in enumerate(np.array_split(random_idx, block_count)):
            trace_blocks[positions] = block_index

    counts = np.zeros((accumulator_blocks, 2), dtype=np.int64)
    np.add.at(counts, (trace_blocks, labels.astype(np.int64, copy=False)), 1)
    return trace_blocks, counts


def compute_streaming_tvla_views(
    dataset: Path,
    views: Mapping[str, StreamingTvlaView],
    *,
    second_order_enable: bool,
    stability_blocks: int = 0,
    trace_batch_size: int = 32,
) -> Dict[str, Dict[str, object]]:
    """Compute multiple aligned TVLA views with one shared row-wise file scan.

    Same-point second-order TVLA needs the pooled mean before its transform, so
    it performs a second shared scan.  Stability blocks retain their original
    block-local pooled means.
    """
    dataset = Path(dataset).resolve()
    traces = np.load(dataset / "traces.npy", mmap_mode="r")
    labels = np.asarray(np.load(dataset / "labels.npy"), dtype=np.int8)
    time_us = np.asarray(np.load(dataset / "time_us.npy"), dtype=np.float64)
    if traces.ndim != 2 or traces.shape[0] != labels.size or traces.shape[1] != time_us.size:
        raise RuntimeError(
            f"TVLA dataset shape mismatch: traces={traces.shape} labels={labels.shape} time={time_us.shape}"
        )
    if not views:
        return {}
    if not bool(np.all((labels == 0) | (labels == 1))):
        raise RuntimeError("TVLA labels must contain only 0 (fixed) and 1 (random)")

    trace_blocks, counts = _make_trace_blocks(labels, stability_blocks)
    block_count = int(counts.shape[0]) if int(stability_blocks) > 1 else 0
    accumulator_blocks = int(counts.shape[0])
    prepared: Dict[str, _PreparedView] = {}
    meta_path = dataset / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    max_width = 0
    for name, spec in views.items():
        shifts = None
        crop = 0
        width = int(traces.shape[1])
        time_view = time_us
        if spec.shifts_path is not None:
            shifts = np.asarray(np.load(Path(spec.shifts_path)), dtype=np.int64)
            if shifts.shape != (traces.shape[0],):
                raise RuntimeError(
                    f"alignment shift shape mismatch for {name}: {shifts.shape} != {(traces.shape[0],)}"
                )
            crop = int(np.max(np.abs(shifts))) + 1
            width = int(traces.shape[1]) - 2 * crop
            if width <= 0:
                raise RuntimeError(f"alignment shifts leave no samples for TVLA view {name}")
            if meta.get("trigger_quality_schema") == "observed-trigger-edges/v2":
                # For every shift, the retained source interval must cover the
                # complete trigger pulse. A small common crop alone is not enough.
                rise = meta.get("rise_index_min")
                fall = meta.get("fall_index_max")
                if rise is None or fall is None:
                    raise RuntimeError("Missing all-trace pulse bounds for alignment coverage audit")
                source_start_max = crop + int(shifts.max())
                source_stop_min = int(traces.shape[1]) - crop + int(shifts.min())
                if source_start_max > int(rise) or source_stop_min <= int(fall):
                    raise RuntimeError(f"alignment crop excludes trigger-active samples in view {name}")
            time_view = time_us[crop:traces.shape[1] - crop]
        prepared[name] = _PreparedView(
            name=name,
            shifts=shifts,
            crop=crop,
            width=width,
            time_view=time_view,
            block_sums=np.zeros((accumulator_blocks, 2, width), dtype=np.float64),
            block_square_sums=np.zeros((accumulator_blocks, 2, width), dtype=np.float64),
        )
        max_width = max(max_width, width)

    trace_batch_size = max(1, min(int(trace_batch_size), int(traces.shape[0])))
    # A full 6M-sample float64 workspace plus group selection copies evicts
    # the accumulators from cache on every batch. Tile columns within each
    # sequentially loaded row batch; all samples and shifts are unchanged.
    tile_width = min(max_width, 32768)
    workspace = np.empty((trace_batch_size, tile_width), dtype=np.float64)
    trace_keys = trace_blocks.astype(np.int64) * 2 + labels.astype(np.int64)
    adc_accumulator = None
    # Int16 ADC squares/sums are exactly representable in float64 below this
    # count bound. Floating/processed inputs retain the NumPy implementation.
    if traces.dtype == np.int16 and traces.shape[0] < 2**23:
        from tvla_acceleration import get_adc_accumulator
        adc_accumulator = get_adc_accumulator()
    zero_shifts = np.zeros(trace_batch_size, dtype=np.int64)

    # Pass 1: one shared sequential trace-file scan for first-order statistics
    # and all pooled means needed by the optional second-order pass.
    for row_start in range(0, int(traces.shape[0]), trace_batch_size):
        row_stop = min(int(traces.shape[0]), row_start + trace_batch_size)
        raw_rows = np.array(traces[row_start:row_stop, :], copy=True, order="C")
        batch_size = row_stop - row_start
        for view in prepared.values():
            batch_shifts = None if view.shifts is None else view.shifts[row_start:row_stop]
            if adc_accumulator is not None:
                adc_accumulator(raw_rows, zero_shifts[:batch_size] if batch_shifts is None else batch_shifts,
                                view.crop, trace_keys[row_start:row_stop],
                                view.block_sums, view.block_square_sums)
                continue
            for column in range(0, view.width, tile_width):
                end = min(view.width, column + tile_width)
                aligned = workspace[:batch_size, :end-column]
                _fill_aligned_workspace(aligned, raw_rows, batch_shifts, view.crop, column)
                _accumulate_partitioned(
                    aligned,
                    trace_keys[row_start:row_stop],
                    view.block_sums[:, :, column:end],
                    view.block_square_sums[:, :, column:end],
                )

    for view in prepared.values():
        full_sums = np.sum(view.block_sums, axis=0, dtype=np.float64)
        full_square_sums = np.sum(view.block_square_sums, axis=0, dtype=np.float64)
        full_counts = np.sum(counts, axis=0, dtype=np.int64)
        if second_order_enable:
            view.full_pooled_mean = np.sum(full_sums, axis=0, dtype=np.float64) / float(
                np.sum(full_counts)
            )
            view.block_pooled_means = np.sum(view.block_sums, axis=1, dtype=np.float64) / np.sum(
                counts, axis=1, dtype=np.int64
            )[:, None]
        view.tvla1_t = _welch_from_sums(
            full_sums[0], full_square_sums[0], int(full_counts[0]),
            full_sums[1], full_square_sums[1], int(full_counts[1]),
        )
        view.stability_tvla1 = []
        if block_count:
            for block_index in range(block_count):
                view.stability_tvla1.append(
                    _welch_from_sums(
                        view.block_sums[block_index, 0],
                        view.block_square_sums[block_index, 0],
                        int(counts[block_index, 0]),
                        view.block_sums[block_index, 1],
                        view.block_square_sums[block_index, 1],
                        int(counts[block_index, 1]),
                    )
                )

    if second_order_enable:
        workspace = np.empty((trace_batch_size, max_width), dtype=np.float64)
        # Reuse accumulator storage.  Index 0 is the full-capture transform;
        # subsequent indices hold transforms around each block-local mean.
        second_units = block_count + 1 if block_count else 1
        for view in prepared.values():
            view.block_sums = np.zeros((second_units, 2, view.width), dtype=np.float64)
            view.block_square_sums = np.zeros((second_units, 2, view.width), dtype=np.float64)

        for row_start in range(0, int(traces.shape[0]), trace_batch_size):
            row_stop = min(int(traces.shape[0]), row_start + trace_batch_size)
            raw_rows = np.array(traces[row_start:row_stop, :], copy=True, order="C")
            batch_size = row_stop - row_start
            batch_labels = labels[row_start:row_stop]
            batch_blocks = trace_blocks[row_start:row_stop]
            for view in prepared.values():
                aligned = workspace[:batch_size, :view.width]
                batch_shifts = None if view.shifts is None else view.shifts[row_start:row_stop]
                _fill_aligned_workspace(aligned, raw_rows, batch_shifts, view.crop)

                full_transformed = aligned - view.full_pooled_mean[None, :]
                np.square(full_transformed, out=full_transformed)
                _accumulate_partitioned(
                    full_transformed,
                    batch_labels.astype(np.int64, copy=False),
                    view.block_sums[:1],
                    view.block_square_sums[:1],
                )
                if block_count:
                    for local_block in np.unique(batch_blocks):
                        positions = np.flatnonzero(batch_blocks == local_block)
                        transformed = (
                            aligned[positions]
                            - view.block_pooled_means[int(local_block)][None, :]
                        )
                        np.square(transformed, out=transformed)
                        local_labels = batch_labels[positions].astype(np.int64, copy=False)
                        _accumulate_partitioned(
                            transformed,
                            local_labels,
                            view.block_sums[int(local_block) + 1:int(local_block) + 2],
                            view.block_square_sums[int(local_block) + 1:int(local_block) + 2],
                        )

        full_counts = np.sum(counts, axis=0, dtype=np.int64)
        for view in prepared.values():
            view.tvla2_t = _welch_from_sums(
                view.block_sums[0, 0], view.block_square_sums[0, 0], int(full_counts[0]),
                view.block_sums[0, 1], view.block_square_sums[0, 1], int(full_counts[1]),
            )
            view.stability_tvla2 = []
            if block_count:
                for block_index in range(block_count):
                    unit = block_index + 1
                    view.stability_tvla2.append(
                        _welch_from_sums(
                            view.block_sums[unit, 0],
                            view.block_square_sums[unit, 0],
                            int(counts[block_index, 0]),
                            view.block_sums[unit, 1],
                            view.block_square_sums[unit, 1],
                            int(counts[block_index, 1]),
                        )
                    )

    results: Dict[str, Dict[str, object]] = {}
    fixed_count = int(np.sum(labels == 0))
    random_count = int(np.sum(labels == 1))
    for name, view in prepared.items():
        results[name] = {
            "accumulation_backend": "numba-int16-tiled" if adc_accumulator is not None else "numpy-tiled",
            "crop": int(view.crop),
            "time_view": view.time_view,
            "n_fixed": fixed_count,
            "n_random": random_count,
            "block_counts": counts.copy() if block_count else np.empty((0, 2), dtype=np.int64),
            "tvla1_t": view.tvla1_t,
            "stability_tvla1": view.stability_tvla1 or [],
            "tvla2_t": view.tvla2_t,
            "stability_tvla2": view.stability_tvla2 or [],
        }
    return results
