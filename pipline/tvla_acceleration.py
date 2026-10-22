"""Optional exact-ADC accumulation; NumPy remains the portable fallback.

Install the pinned wheels into .runtime_packages, without replacing NumPy:
python -m pip install --no-deps --target .runtime_packages numba==0.60.0 llvmlite==0.43.0
"""
from pathlib import Path
import os
import sys
import math
import numpy as np

_kernel = None
_envelope_kernel = None
_tried = False


def get_adc_accumulator():
    global _kernel, _envelope_kernel, _tried
    if os.environ.get("TVLA_DISABLE_JIT") == "1":
        return None
    if _tried:
        return _kernel
    _tried = True
    local = Path(__file__).resolve().parent / ".runtime_packages"
    if local.is_dir() and str(local) not in sys.path:
        sys.path.insert(0, str(local))
    try:
        from numba import njit, prange, set_num_threads, config
    except (ImportError, OSError):
        return None
    threads = max(1, min(6, os.cpu_count() or 1, config.NUMBA_NUM_THREADS))
    set_num_threads(threads)

    @njit(parallel=True, fastmath=False, cache=True)
    def accumulate(raw, shifts, crop, keys, sums, squares):
        width = sums.shape[2]
        tile = 8192
        # Parallel workers own disjoint sample columns. Within each column,
        # rows retain their original order. No cross-thread sum reductions.
        for part in prange((width + tile - 1)//tile):
            a = part*tile
            b = min(width, a+tile)
            for row in range(raw.shape[0]):
                block = keys[row]//2
                group = keys[row]%2
                offset = crop + shifts[row]
                for col in range(a,b):
                    value = float(raw[row,offset+col])
                    sums[block,group,col] += value
                    squares[block,group,col] += value*value
    _kernel = accumulate
    @njit(parallel=True, fastmath=False, cache=True)
    def rms(raw, factor):
        result = np.empty(raw.size//factor, dtype=np.float64)
        for block in prange(result.size):
            total = 0.0
            for j in range(factor):
                value = float(raw[block*factor+j])
                total += value*value
            result[block] = math.sqrt(total/factor)
        return result
    _envelope_kernel = rms
    return _kernel


def get_adc_envelope():
    if get_adc_accumulator() is None:
        return None
    return _envelope_kernel
