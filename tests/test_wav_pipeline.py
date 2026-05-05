"""Tests for the WAV import and processing pipeline.

Covers read_wav_info(), _block_mean_sq(), stream_wav_leq(), and
align_wav_timestamps().  All WAV files are synthesised in memory so no
real audio data is needed.
"""

import math
import struct
import wave
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from pegelanalyse import (
    WavInfo,
    _block_mean_sq,
    align_wav_timestamps,
    read_wav_info,
    stream_wav_leq,
)


# ---------------------------------------------------------------------------
# WAV file factory
# ---------------------------------------------------------------------------

def _make_wav(
    path: Path,
    *,
    bit_depth: int = 16,
    sample_rate: int = 48_000,
    channels: int = 1,
    duration: float = 1.0,
    amplitude: float = 0.5,
) -> Path:
    """Write a WAV file with a constant DC amplitude.

    amplitude is in the range [0.0, 1.0] relative to full scale.
    A DC signal is used so the mean-squared value is exactly amplitude².
    """
    n_frames = int(sample_rate * duration)

    if bit_depth == 16:
        sample_val = round(amplitude * 32768)
        raw = struct.pack(f"<{n_frames * channels}h", *([sample_val] * n_frames * channels))
        sampwidth = 2
    elif bit_depth == 24:
        sample_val = round(amplitude * (1 << 23))
        b = bytes([sample_val & 0xFF, (sample_val >> 8) & 0xFF, (sample_val >> 16) & 0xFF])
        raw = b * (n_frames * channels)
        sampwidth = 3
    elif bit_depth == 32:
        sample_val = round(amplitude * (1 << 31))
        raw = struct.pack(f"<{n_frames * channels}i", *([sample_val] * n_frames * channels))
        sampwidth = 4
    elif bit_depth == 8:
        # Intentionally unsupported — used to test the rejection path.
        raw = bytes([128] * n_frames * channels)
        sampwidth = 1
    else:
        raise ValueError(f"Unsupported bit_depth {bit_depth} in test helper")

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return path


# ---------------------------------------------------------------------------
# Expected dBFS for a constant-amplitude DC signal
# ---------------------------------------------------------------------------

def _expected_dbfs(amplitude: float, bit_depth: int) -> float:
    """Compute the expected Leq dBFS for a DC signal at a given amplitude."""
    divisors = {16: 32768.0, 24: float(1 << 23), 32: float(1 << 31)}
    sample_val = round(amplitude * divisors[bit_depth])
    norm = sample_val / divisors[bit_depth]
    return 10 * math.log10(norm ** 2)


# ---------------------------------------------------------------------------
# read_wav_info
# ---------------------------------------------------------------------------

class TestReadWavInfo:
    def test_16bit(self, tmp_path):
        f = _make_wav(tmp_path / "a.wav", bit_depth=16, sample_rate=48_000,
                      channels=1, duration=2.0)
        info = read_wav_info(str(f))
        assert info.sample_rate == 48_000
        assert info.channels == 1
        assert info.bit_depth == 16
        assert info.n_frames == 96_000
        assert info.duration == pytest.approx(2.0)

    def test_24bit(self, tmp_path):
        f = _make_wav(tmp_path / "a.wav", bit_depth=24, sample_rate=96_000,
                      channels=2, duration=0.5)
        info = read_wav_info(str(f))
        assert info.sample_rate == 96_000
        assert info.channels == 2
        assert info.bit_depth == 24
        assert info.n_frames == 48_000
        assert info.duration == pytest.approx(0.5)

    def test_32bit(self, tmp_path):
        f = _make_wav(tmp_path / "a.wav", bit_depth=32, sample_rate=44_100, channels=1)
        info = read_wav_info(str(f))
        assert info.bit_depth == 32

    def test_8bit_raises(self, tmp_path):
        f = _make_wav(tmp_path / "a.wav", bit_depth=8)
        with pytest.raises(ValueError, match="Bit-Tiefe"):
            read_wav_info(str(f))

    def test_missing_file_raises(self):
        with pytest.raises(Exception):
            read_wav_info("/nonexistent/audio.wav")


# ---------------------------------------------------------------------------
# _block_mean_sq
# ---------------------------------------------------------------------------

class TestBlockMeanSq:
    def test_empty_returns_zero(self):
        assert _block_mean_sq(b"", samp_width=2) == pytest.approx(0.0)

    def test_16bit_silence(self):
        raw = struct.pack("<4h", 0, 0, 0, 0)
        assert _block_mean_sq(raw, samp_width=2) == pytest.approx(0.0)

    def test_16bit_known_amplitude(self):
        # sample_val = 16384, norm = 16384/32768 = 0.5, mean_sq = 0.25
        raw = struct.pack("<4h", 16384, 16384, 16384, 16384)
        assert _block_mean_sq(raw, samp_width=2) == pytest.approx(0.25, rel=1e-6)

    def test_16bit_negative_amplitude(self):
        # Negative samples: norm = -16384/32768 = -0.5, mean_sq = 0.25
        raw = struct.pack("<4h", -16384, -16384, -16384, -16384)
        assert _block_mean_sq(raw, samp_width=2) == pytest.approx(0.25, rel=1e-6)

    def test_24bit_silence(self):
        raw = bytes(3 * 4)  # 4 samples × 3 bytes, all zero
        assert _block_mean_sq(raw, samp_width=3) == pytest.approx(0.0)

    def test_24bit_known_amplitude(self):
        # sample_val = 2^22 = 4194304, norm = 2^22 / 2^23 = 0.5, mean_sq = 0.25
        val = 1 << 22
        sample = bytes([val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF])
        raw = sample * 4
        assert _block_mean_sq(raw, samp_width=3) == pytest.approx(0.25, rel=1e-6)

    def test_32bit_silence(self):
        raw = struct.pack("<4i", 0, 0, 0, 0)
        assert _block_mean_sq(raw, samp_width=4) == pytest.approx(0.0)

    def test_32bit_known_amplitude(self):
        # sample_val = 2^30, norm = 2^30 / 2^31 = 0.5, mean_sq = 0.25
        val = 1 << 30
        raw = struct.pack("<4i", val, val, val, val)
        assert _block_mean_sq(raw, samp_width=4) == pytest.approx(0.25, rel=1e-6)


# ---------------------------------------------------------------------------
# stream_wav_leq
# ---------------------------------------------------------------------------

class TestStreamWavLeq:
    def test_silence_gives_neg_inf(self, tmp_path, capsys):
        f = _make_wav(tmp_path / "s.wav", amplitude=0.0, duration=1.0)
        result = stream_wav_leq([str(f)], output_interval=0.1, integration_time=0.5)
        assert all(v == -math.inf for _, v in result)

    def test_16bit_known_amplitude(self, tmp_path, capsys):
        f = _make_wav(tmp_path / "a.wav", bit_depth=16, amplitude=0.5,
                      sample_rate=48_000, duration=2.0)
        result = stream_wav_leq([str(f)], output_interval=0.2, integration_time=1.0)
        expected = _expected_dbfs(0.5, 16)
        for _, dbfs in result:
            assert dbfs == pytest.approx(expected, abs=0.01)

    def test_24bit_known_amplitude(self, tmp_path, capsys):
        f = _make_wav(tmp_path / "a.wav", bit_depth=24, amplitude=0.5,
                      sample_rate=48_000, duration=2.0)
        result = stream_wav_leq([str(f)], output_interval=0.2, integration_time=1.0)
        expected = _expected_dbfs(0.5, 24)
        for _, dbfs in result:
            assert dbfs == pytest.approx(expected, abs=0.01)

    def test_32bit_known_amplitude(self, tmp_path, capsys):
        f = _make_wav(tmp_path / "a.wav", bit_depth=32, amplitude=0.5,
                      sample_rate=48_000, duration=2.0)
        result = stream_wav_leq([str(f)], output_interval=0.2, integration_time=1.0)
        expected = _expected_dbfs(0.5, 32)
        for _, dbfs in result:
            assert dbfs == pytest.approx(expected, abs=0.01)

    def test_time_offsets_are_multiples_of_interval(self, tmp_path, capsys):
        f = _make_wav(tmp_path / "a.wav", duration=1.0)
        interval = 0.2
        result = stream_wav_leq([str(f)], output_interval=interval, integration_time=0.5)
        for i, (t, _) in enumerate(result):
            assert t == pytest.approx(i * interval, abs=1e-9)

    def test_multi_file_length(self, tmp_path, capsys):
        """Two identical files → output twice as long as one file."""
        f1 = _make_wav(tmp_path / "a.wav", duration=1.0)
        f2 = _make_wav(tmp_path / "b.wav", duration=1.0)
        r_single = stream_wav_leq([str(f1)], output_interval=0.2, integration_time=0.5)
        r_double = stream_wav_leq([str(f1), str(f2)], output_interval=0.2, integration_time=0.5)
        assert len(r_double) == 2 * len(r_single)

    def test_multi_file_uniform_leq(self, tmp_path, capsys):
        """Concatenated files with same amplitude → all Leq values equal."""
        f1 = _make_wav(tmp_path / "a.wav", amplitude=0.5, duration=1.0)
        f2 = _make_wav(tmp_path / "b.wav", amplitude=0.5, duration=1.0)
        result = stream_wav_leq([str(f1), str(f2)], output_interval=0.2, integration_time=0.5)
        values = [v for _, v in result]
        assert max(values) - min(values) == pytest.approx(0.0, abs=0.01)

    def test_format_mismatch_raises(self, tmp_path, capsys):
        f1 = _make_wav(tmp_path / "a.wav", sample_rate=48_000)
        f2 = _make_wav(tmp_path / "b.wav", sample_rate=96_000)
        with pytest.raises(ValueError, match="unterschiedliche Formate"):
            stream_wav_leq([str(f1), str(f2)], output_interval=0.2, integration_time=1.0)

    def test_no_files_raises(self):
        with pytest.raises(ValueError):
            stream_wav_leq([], output_interval=0.2, integration_time=1.0)


# ---------------------------------------------------------------------------
# align_wav_timestamps
# ---------------------------------------------------------------------------

class TestAlignWavTimestamps:
    def test_zero_offset(self):
        start = datetime(2026, 2, 15, 10, 0, 0)
        raw = [(0.0, -10.0)]
        result = align_wav_timestamps(raw, start)
        assert result[0][0] == start
        assert result[0][1] == -10.0

    def test_multiple_offsets(self):
        start = datetime(2026, 2, 15, 10, 0, 0)
        raw = [(0.0, -10.0), (0.2, -15.0), (0.4, -20.0)]
        result = align_wav_timestamps(raw, start)
        assert len(result) == 3
        assert result[1][0] == start + timedelta(seconds=0.2)
        assert result[2][0] == start + timedelta(seconds=0.4)

    def test_values_preserved(self):
        start = datetime(2026, 2, 15, 10, 0, 0)
        raw = [(float(i) * 0.2, float(-10 - i)) for i in range(10)]
        result = align_wav_timestamps(raw, start)
        for (_, orig_v), (_, out_v) in zip(raw, result):
            assert orig_v == out_v

    def test_empty_input(self):
        start = datetime(2026, 2, 15, 10, 0, 0)
        assert align_wav_timestamps([], start) == []
