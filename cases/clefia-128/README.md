# 🔬 CLEFIA-128 / STM32F415 case

This folder contains one **AEGIS evaluation case**: bare-metal CLEFIA-128 firmware, a Python reference implementation, SWD/PicoScope capture tools, and first-order TVLA analysis scripts. The AEGIS method and release status are described in the [repository README](../../README.md).

> 📦 **Source only.** No waveforms, capture results, firmware images, flash backups, logs or credentials are included.

## 🧰 What's here

| Path | Purpose |
| --- | --- |
| `firmware/` | CLEFIA-128 implementation, startup code, linker script and constants from [RFC 6114](https://www.rfc-editor.org/rfc/rfc6114). |
| `scripts/reference.py` | Byte-oriented reference and RFC known-answer check. |
| `scripts/tvla_stats.py`, `scripts/tvla_analyze.py` | Welch first-order TVLA statistics and offline analysis. |
| `scripts/hardware.py`, `scripts/pico.py` | SWD mailbox and PicoScope adapters. |
| `scripts/capture.py`, `scripts/tvla_capture.py` | Optional hardware acquisition. |
| `build.ps1`, `run.ps1` | Windows build and local task launchers. |

## ⚡ Quick checks

Install the packages in `requirements.txt`, then run from **this folder**:

```powershell
python scripts/reference.py
python scripts/tvla_stats.py
```

These checks require no board or oscilloscope. The RFC test vector's expected ciphertext is `de2bf2fd9b74aacdf1298555459494fd`.

## 🔧 Build and capture

On Windows, install an Arm GNU Toolchain and put its `bin` directory on `PATH`:

```powershell
.\run.ps1 -Action build
```

Hardware acquisition additionally needs an STM32F415 board, CMSIS-DAP probe, PicoScope 3403D with PicoSDK, and the matching STM32F4xx CMSIS pack. Set `STM32_PACK` to the pack directory; set `PICO_SDK_LIB` if the SDK is outside its default Windows location. Check the target and probe settings in `scripts/hardware.py` before flashing or capturing. Generated files go under ignored `build/`, `backup/` and `captures/` directories.

The scripts use a fixed **public RFC test key** for experiments. It is unsuitable for protecting real data. Capture output stores plaintexts, ciphertexts and that key.

## 🧪 Reproducibility note

The Python reference reads constants from `firmware/tables.h`. Its algorithm is separately implemented and checked against the RFC vector, but its constants are not independent of the firmware header. This folder does not include the AEGIS core pipeline or the measurements behind the paper's reported results.
