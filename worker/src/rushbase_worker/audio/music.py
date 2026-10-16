"""Pure-audio assets: audio type from the event distribution and BPM from onset autocorrelation.

Musical key detection is deliberately not implemented: chroma-template key estimates are wrong
often enough on mixed material (and for modal or atonal music) that a confident-looking value
would do more harm than leaving the field empty (``AudioOutput.musical_key`` stays ``None``).
"""

import math

import numpy as np
from numpy.typing import NDArray

from rushbase_core.results_ai import AudioEvent, AudioType
from rushbase_worker.audio.events import total_seconds
from rushbase_worker.audio.extract import SAMPLE_RATE

FRAME = 1024
HOP = 256
MIN_BPM = 60.0
MAX_BPM = 180.0
PRIOR_BPM = 120.0
MAX_SECONDS = 150.0
MIN_SECONDS = 8.0
MIN_PEAK = 0.2
"""Normalised autocorrelation the best lag must reach (white noise stays below 0.1)."""


def classify_audio_type(
    events: list[AudioEvent], duration_s: float
) -> tuple[AudioType, float] | None:
    """Music, speech, silence, SFX, ambience or mixed, with the dominant share as confidence."""
    if duration_s <= 0:
        return None
    share = {tag: min(1.0, total_seconds(events, tag) / duration_s) for tag in (
        "speech", "music", "silence", "gunshot", "explosion", "applause", "wind",
    )}  # fmt: skip
    if share["silence"] >= 0.9:
        return AudioType.SILENCE, share["silence"]
    if share["speech"] >= 0.2 and share["music"] >= 0.2:
        return AudioType.MIXED, min(share["speech"], share["music"]) + 0.3
    if share["music"] >= 0.5:
        return AudioType.MUSIC, share["music"]
    if share["speech"] >= 0.5:
        return AudioType.SPEECH, share["speech"]
    impulsive = share["gunshot"] + share["explosion"] + share["applause"]
    if impulsive > 0 or duration_s < 10.0:
        return AudioType.SFX, 0.5 + min(0.4, impulsive)
    return AudioType.AMBIENCE, 0.5 + min(0.4, share["wind"])


def onset_envelope(samples: NDArray[np.float32]) -> NDArray[np.float32]:
    """Positive spectral flux (log-compressed magnitude STFT) per hop, mean-removed."""
    count = 1 + (len(samples) - FRAME) // HOP
    if count < 4:
        return np.zeros(0, dtype=np.float32)
    window = np.hanning(FRAME).astype(np.float32)
    index = np.arange(FRAME)[None, :] + HOP * np.arange(count)[:, None]
    spectrum = np.abs(np.fft.rfft(samples[index] * window, axis=1))
    compressed = np.log1p(100.0 * spectrum)
    flux = np.maximum(np.diff(compressed, axis=0), 0.0).sum(axis=1)
    kernel = np.ones(int(0.5 * SAMPLE_RATE / HOP)) / int(0.5 * SAMPLE_RATE / HOP)
    envelope = flux - np.convolve(flux, kernel, mode="same")
    positive: NDArray[np.float32] = np.maximum(envelope, 0.0).astype(np.float32)
    return positive


def estimate_bpm(samples: NDArray[np.float32]) -> tuple[float, float] | None:
    """Tempo in BPM (60-180, log-Gaussian prior around 120) and a 0-1 confidence.

    Returns ``None`` for clips shorter than eight seconds or without a clear periodicity.
    """
    if len(samples) < MIN_SECONDS * SAMPLE_RATE:
        return None
    envelope = onset_envelope(samples[: int(MAX_SECONDS * SAMPLE_RATE)])
    if envelope.size < 64 or float(envelope.max()) <= 0.0:
        return None
    fps = SAMPLE_RATE / HOP
    size = 1 << (2 * envelope.size - 1).bit_length()
    spectrum = np.fft.rfft(envelope - envelope.mean(), size)
    autocorr = np.fft.irfft(np.abs(spectrum) ** 2)[: envelope.size]
    if autocorr[0] <= 0:
        return None
    autocorr = autocorr / autocorr[0]
    low, high = int(fps * 60.0 / MAX_BPM), int(fps * 60.0 / MIN_BPM)
    lags = np.arange(low, min(high, autocorr.size - 1) + 1)
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / PRIOR_BPM) / 1.0) ** 2)
    weighted = autocorr[lags] * prior
    best = int(np.argmax(weighted))
    lag = float(lags[best])
    if 0 < best < len(lags) - 1:  # parabolic interpolation around the peak
        a, b, c = autocorr[lags[best - 1]], autocorr[lags[best]], autocorr[lags[best + 1]]
        denominator = a - 2 * b + c
        if denominator != 0:
            lag += 0.5 * (a - c) / denominator
    peak = float(autocorr[lags[best]])
    if peak < MIN_PEAK or not math.isfinite(lag) or lag <= 0:
        return None
    return round(float(60.0 * fps / lag), 1), min(1.0, peak / 0.5)
