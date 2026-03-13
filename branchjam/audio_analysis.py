import math
import os
import struct
import wave


def _bytes_to_int_samples(raw, sample_width):
    if sample_width == 1:
        # 8-bit PCM is unsigned.
        return [b - 128 for b in raw]
    if sample_width == 2:
        count = len(raw) // 2
        return list(struct.unpack("<" + ("h" * count), raw[: count * 2]))
    if sample_width == 3:
        out = []
        for i in range(0, len(raw) - 2, 3):
            chunk = raw[i : i + 3]
            value = int.from_bytes(chunk, byteorder="little", signed=False)
            if value & 0x800000:
                value -= 0x1000000
            out.append(value)
        return out
    if sample_width == 4:
        count = len(raw) // 4
        return list(struct.unpack("<" + ("i" * count), raw[: count * 4]))
    raise ValueError("Unsupported sample width.")


def _deinterleave_to_mono(samples, channels):
    if channels <= 1:
        return samples
    mono = []
    for i in range(0, len(samples) - channels + 1, channels):
        mono.append(sum(samples[i : i + channels]) / channels)
    return mono


def _read_pcm(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".wav":
        with wave.open(path, "rb") as f:
            channels = f.getnchannels()
            sample_rate = f.getframerate()
            sample_width = f.getsampwidth()
            frame_count = f.getnframes()
            raw = f.readframes(frame_count)
    else:
        raise ValueError("Tempo check currently supports WAV only.")

    int_samples = _bytes_to_int_samples(raw, sample_width)
    mono_samples = _deinterleave_to_mono(int_samples, channels)
    return mono_samples, sample_rate


def _downsample_envelope(samples, sample_rate, target_hz=200, max_seconds=60):
    max_len = min(len(samples), int(sample_rate * max_seconds))
    samples = samples[:max_len]
    if not samples:
        return [], target_hz
    step = max(1, int(sample_rate / target_hz))
    env = []
    for i in range(0, len(samples), step):
        chunk = samples[i : i + step]
        if not chunk:
            continue
        env.append(sum(abs(v) for v in chunk) / len(chunk))
    mean_val = sum(env) / len(env) if env else 0
    centered = [v - mean_val for v in env]
    return centered, sample_rate / step


def estimate_bpm(path, min_bpm=60, max_bpm=200):
    samples, sample_rate = _read_pcm(path)
    envelope, env_rate = _downsample_envelope(samples, sample_rate)
    n = len(envelope)
    if n < 200:
        raise ValueError("Audio too short for tempo analysis.")

    min_lag = int(env_rate * 60 / max_bpm)
    max_lag = int(env_rate * 60 / min_bpm)
    min_lag = max(1, min_lag)
    max_lag = min(n - 1, max_lag)

    best_lag = None
    best_score = -float("inf")
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        for i in range(lag, n):
            score += envelope[i] * envelope[i - lag]
        if score > best_score:
            best_score = score
            best_lag = lag

    if not best_lag:
        raise ValueError("Could not detect tempo.")
    return 60.0 * env_rate / best_lag


def waveform_svg(path, width=420, height=72, bins=120):
    samples, _ = _read_pcm(path)
    if not samples:
        return "<svg width='420' height='72'></svg>"

    chunk_size = max(1, math.ceil(len(samples) / bins))
    peaks = []
    for i in range(0, len(samples), chunk_size):
        chunk = samples[i : i + chunk_size]
        peaks.append(max(abs(v) for v in chunk) if chunk else 0)
        if len(peaks) >= bins:
            break

    peak_max = max(peaks) or 1.0
    bar_width = max(1, width // max(1, len(peaks)))
    x = 0
    parts = [f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"]
    parts.append(f"<rect x='0' y='0' width='{width}' height='{height}' fill='#f6f6f6'/>")
    for p in peaks:
        norm = p / peak_max
        bar_h = max(1, int((height - 8) * norm))
        y = (height - bar_h) // 2
        parts.append(f"<rect x='{x}' y='{y}' width='{bar_width - 1}' height='{bar_h}' fill='#2c7be5'/>")
        x += bar_width
        if x >= width:
            break
    parts.append("</svg>")
    return "".join(parts)


def trim_wav_inplace(path, start_s, end_s):
    if start_s < 0:
        start_s = 0.0
    if end_s is not None and end_s < 0:
        end_s = 0.0

    temp_path = path + ".trimtmp"
    with wave.open(path, "rb") as src:
        channels = src.getnchannels()
        sample_rate = src.getframerate()
        sample_width = src.getsampwidth()
        total_frames = src.getnframes()

        start_frame = int(start_s * sample_rate)
        end_frame = int(end_s * sample_rate) if end_s and end_s > 0 else total_frames
        start_frame = max(0, min(start_frame, total_frames))
        end_frame = max(start_frame, min(end_frame, total_frames))
        frames_to_copy = end_frame - start_frame

        src.setpos(start_frame)
        raw = src.readframes(frames_to_copy)

    with wave.open(temp_path, "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(sample_width)
        dst.setframerate(sample_rate)
        dst.writeframes(raw)

    os.replace(temp_path, path)


def _frame_rms(frame):
    if not frame:
        return 0.0
    return math.sqrt(sum(v * v for v in frame) / len(frame))


def _detect_pitch_autocorr(frame, sample_rate, min_hz=80, max_hz=1000):
    if not frame:
        return None
    mean = sum(frame) / len(frame)
    centered = [v - mean for v in frame]
    energy = sum(v * v for v in centered) / max(1, len(centered))
    if energy < 1e-5:
        return None

    min_lag = max(1, int(sample_rate / max_hz))
    max_lag = min(len(centered) - 1, int(sample_rate / min_hz))
    if min_lag >= max_lag:
        return None

    best_lag = None
    best_score = -float("inf")
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        for i in range(lag, len(centered)):
            score += centered[i] * centered[i - lag]
        if score > best_score:
            best_score = score
            best_lag = lag
    if not best_lag:
        return None
    return float(sample_rate) / float(best_lag)


def _synth_sample(instrument, phase):
    if instrument == "square":
        return 1.0 if math.sin(phase) >= 0 else -1.0
    if instrument == "saw":
        # map phase [0, 2pi) to [-1, 1]
        return ((phase % (2 * math.pi)) / math.pi) - 1.0
    return math.sin(phase)


def hum_to_instrument_wav(input_path, output_path, instrument="sine"):
    samples, sample_rate = _read_pcm(input_path)
    if not samples:
        raise ValueError("Hum track is empty.")

    peak = max(abs(v) for v in samples) or 1.0
    mono = [float(v) / float(peak) for v in samples]

    frame_size = max(256, int(sample_rate * 0.04))
    hop = max(128, int(sample_rate * 0.02))

    freqs = []
    amps = []
    for start in range(0, len(mono), hop):
        frame = mono[start : start + frame_size]
        if len(frame) < frame_size // 2:
            break
        amp = _frame_rms(frame)
        freq = _detect_pitch_autocorr(frame, sample_rate)
        if amp < 0.02:
            freq = None
        freqs.append(freq)
        amps.append(min(1.0, amp * 2.2))

    if not freqs:
        raise ValueError("Could not extract melody from hum track.")

    # Median smoothing to reduce pitch jitter.
    smoothed = []
    for i in range(len(freqs)):
        window = [f for f in freqs[max(0, i - 2) : min(len(freqs), i + 3)] if f]
        smoothed.append(sorted(window)[len(window) // 2] if window else None)

    out = []
    phase = 0.0
    for i in range(len(mono)):
        idx = min(len(smoothed) - 1, i // hop)
        freq = smoothed[idx]
        amp = amps[idx] if idx < len(amps) else 0.0
        if freq is None:
            out.append(0)
            continue
        phase += (2.0 * math.pi * freq) / sample_rate
        sample = _synth_sample(instrument, phase)
        val = int(max(-1.0, min(1.0, sample * amp * 0.8)) * 32767)
        out.append(val)

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        data = struct.pack("<" + ("h" * len(out)), *out)
        wf.writeframes(data)
