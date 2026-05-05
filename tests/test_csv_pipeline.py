"""Tests for the CSV import and processing pipeline.

Covers parse_csv(), interpolate_timestamps(), and compute_moving_leq().
"""

import math
from datetime import datetime, timedelta

import pytest

from pegelanalyse import CsvData, compute_moving_leq, interpolate_timestamps, parse_csv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(h: int, m: int, s: int) -> datetime:
    return datetime(2026, 2, 15, h, m, s)


def _make_data(sampling_rate: float, measurements: list) -> CsvData:
    return CsvData(sampling_rate=sampling_rate, measurements=measurements)


# ---------------------------------------------------------------------------
# parse_csv
# ---------------------------------------------------------------------------

class TestParseCsv:
    def test_valid_minimal(self, minimal_csv):
        data = parse_csv(str(minimal_csv))
        assert data.sampling_rate == pytest.approx(0.2)
        assert len(data.measurements) == 2
        assert data.measurements[0][1] == pytest.approx(45.3)
        assert data.measurements[1][1] == pytest.approx(46.1)

    def test_valid_real_file(self, real_csv):
        data = parse_csv(str(real_csv))
        assert data.sampling_rate == pytest.approx(0.2)
        assert len(data.measurements) == 26871
        values = [v for _, v in data.measurements]
        assert all(0.0 <= v <= 140.0 for v in values)

    def test_real_file_start_timestamp(self, real_csv):
        data = parse_csv(str(real_csv))
        assert data.measurements[0][0] == datetime(2026, 2, 15, 9, 50, 50)

    def test_missing_file(self):
        with pytest.raises(OSError):
            parse_csv("/nonexistent/path/file.txt")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        with pytest.raises(ValueError, match="leer"):
            parse_csv(str(f))

    def test_missing_sampling_rate(self, tmp_path):
        f = tmp_path / "no_rate.txt"
        f.write_text("HEADER WITHOUT RATE\n15-02-2026,10:00:00, 45.0, dBA\n")
        with pytest.raises(ValueError, match="SamplingRate"):
            parse_csv(str(f))

    def test_invalid_sampling_rate_zero(self, tmp_path):
        f = tmp_path / "zero_rate.txt"
        f.write_text("SamplingRate:0.0;\n15-02-2026,10:00:00, 45.0, dBA\n")
        with pytest.raises(ValueError):
            parse_csv(str(f))

    def test_invalid_data_row(self, tmp_path):
        f = tmp_path / "bad_row.txt"
        f.write_text("SamplingRate:1.0;\n15-02-2026,10:00:00, not_a_number, dBA\n")
        with pytest.raises(ValueError, match="Zeile"):
            parse_csv(str(f))

    def test_short_rows_are_skipped(self, tmp_path):
        """Rows with fewer than 3 fields are silently skipped."""
        f = tmp_path / "short.txt"
        f.write_text(
            "SamplingRate:1.0;\n"
            "\n"
            "15-02-2026,10:00:00, 50.0, dBA\n"
        )
        data = parse_csv(str(f))
        assert len(data.measurements) == 1


# ---------------------------------------------------------------------------
# interpolate_timestamps
# ---------------------------------------------------------------------------

class TestInterpolateTimestamps:
    def test_1hz_timestamps_unchanged(self):
        """At 1 Hz each second has exactly one measurement — no shift needed."""
        measurements = [(_dt(10, 0, i), float(i)) for i in range(5)]
        data = _make_data(1.0, measurements)
        result = interpolate_timestamps(data)
        assert len(result) == 5
        for (orig_t, _), (new_t, _) in zip(measurements, result):
            assert orig_t == new_t

    def test_5hz_full_group_offsets(self):
        """0.2 s rate, full group of 5 (not the first group): offsets 0–0.8 s."""
        base = _dt(10, 0, 1)
        # first group: one sample at t=0 (right-aligned → offset 0.8 s, but we don't check it here)
        measurements = [(_dt(10, 0, 0), 0.0)] + [(base, float(i)) for i in range(5)]
        data = _make_data(0.2, measurements)
        result = interpolate_timestamps(data)
        second_group = [(t, v) for t, v in result if t >= base]
        assert len(second_group) == 5
        for k, (t, _) in enumerate(second_group):
            expected = base + timedelta(seconds=k * 0.2)
            assert t == expected

    def test_5hz_first_group_right_aligned(self):
        """First group of 3 at 0.2 s rate → right-aligned: offsets 0.4, 0.6, 0.8 s."""
        base = _dt(10, 0, 0)
        measurements = [(base, float(i)) for i in range(3)]
        data = _make_data(0.2, measurements)
        result = interpolate_timestamps(data)
        assert len(result) == 3
        expected_offsets = [0.4, 0.6, 0.8]
        for (t, _), offset in zip(result, expected_offsets):
            diff = (t - base).total_seconds()
            assert diff == pytest.approx(offset, abs=1e-9)

    def test_2hz_first_group_single_sample(self):
        """First group of 1 at 0.5 s rate → right-aligned: offset 0.5 s."""
        base = _dt(10, 0, 0)
        measurements = [(base, 50.0), (_dt(10, 0, 1), 51.0)]
        data = _make_data(0.5, measurements)
        result = interpolate_timestamps(data)
        assert (result[0][0] - base).total_seconds() == pytest.approx(0.5, abs=1e-9)

    def test_output_strictly_monotone(self):
        """Interpolated timestamps must be strictly increasing."""
        base = _dt(10, 0, 0)
        measurements = (
            [(base, float(i)) for i in range(3)]
            + [(_dt(10, 0, 1), float(i)) for i in range(5)]
            + [(_dt(10, 0, 2), float(i)) for i in range(5)]
        )
        data = _make_data(0.2, measurements)
        result = interpolate_timestamps(data)
        times = [t for t, _ in result]
        assert all(times[i] < times[i + 1] for i in range(len(times) - 1))

    def test_values_preserved(self):
        """Interpolation must not alter the dB(A) values."""
        base = _dt(10, 0, 0)
        values = [42.1, 43.5, 44.9, 50.0, 55.5]
        measurements = [(base, v) for v in values]
        data = _make_data(0.2, measurements)
        result = interpolate_timestamps(data)
        assert [v for _, v in result] == pytest.approx(values)


# ---------------------------------------------------------------------------
# compute_moving_leq
# ---------------------------------------------------------------------------

class TestComputeMovingLeq:
    def test_empty_input(self):
        assert compute_moving_leq([], integration_time=30.0) == []

    def test_single_point(self):
        samples = [(_dt(10, 0, 0), 63.0)]
        result = compute_moving_leq(samples, integration_time=30.0)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(63.0, abs=1e-6)

    def test_uniform_value(self):
        """All samples at same dB → Leq equals that dB, regardless of window size."""
        t0 = _dt(10, 0, 0)
        samples = [(t0 + timedelta(seconds=i), 50.0) for i in range(20)]
        result = compute_moving_leq(samples, integration_time=6.0)
        for _, leq in result:
            assert leq == pytest.approx(50.0, abs=1e-6)

    def test_output_length_matches_input(self):
        t0 = _dt(10, 0, 0)
        samples = [(t0 + timedelta(seconds=i * 0.2), 50.0) for i in range(100)]
        result = compute_moving_leq(samples, integration_time=10.0)
        assert len(result) == 100

    def test_timestamps_preserved(self):
        t0 = _dt(10, 0, 0)
        samples = [(t0 + timedelta(seconds=i * 0.2), 50.0) for i in range(10)]
        result = compute_moving_leq(samples, integration_time=2.0)
        for (in_t, _), (out_t, _) in zip(samples, result):
            assert in_t == out_t

    def test_known_two_values(self):
        """Leq of [50 dB, 60 dB] = 10*log10((10^5 + 10^6) / 2) ≈ 57.40 dB."""
        expected = 10 * math.log10((10**5 + 10**6) / 2)
        t0 = _dt(10, 0, 0)
        samples = [(t0, 50.0), (t0 + timedelta(seconds=1.0), 60.0)]
        # Large window so both points are always in range
        result = compute_moving_leq(samples, integration_time=60.0)
        for _, leq in result:
            assert leq == pytest.approx(expected, abs=1e-6)

    def test_edge_window_no_nan(self):
        """Points at dataset edges must produce finite values (no padding/NaN)."""
        t0 = _dt(10, 0, 0)
        samples = [(t0 + timedelta(seconds=i), 50.0) for i in range(100)]
        result = compute_moving_leq(samples, integration_time=30.0)
        assert all(math.isfinite(leq) for _, leq in result)

    def test_integration_with_real_csv(self, real_csv):
        """End-to-end: real CSV → interpolate → Leq — sanity checks on output."""
        data = parse_csv(str(real_csv))
        samples = interpolate_timestamps(data)
        result = compute_moving_leq(samples, integration_time=20.0)

        assert len(result) == len(samples)
        times = [t for t, _ in result]
        assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))
        leqs = [v for _, v in result]
        assert all(math.isfinite(v) for v in leqs)
        assert all(0.0 <= v <= 140.0 for v in leqs)
