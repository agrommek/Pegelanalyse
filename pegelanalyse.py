#!/usr/bin/env python3
"""Pegelanalyse – acoustic sound level analysis and visualization."""

import argparse
import csv
import math
import os
import pickle
import re
import subprocess
import sys
import tempfile
import threading
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# Import / Parsing layer
# ---------------------------------------------------------------------------

@dataclass
class CsvData:
    """Raw parsed content of a Sound Level Meter CSV file."""
    sampling_rate: float                        # measurement interval in seconds
    measurements: list[tuple[datetime, float]]  # (timestamp, dB(A)), second precision


def parse_csv(path: str) -> CsvData:
    """Parse a Sound Level Meter CSV file.

    Expected format:
      Header: ...SamplingRate:0.2;...
      Data:   DD-MM-YYYY,HH:MM:SS, VALUE, dBA
    """
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)

        # --- header ---
        try:
            header_row = next(reader)
        except StopIteration:
            raise ValueError(f"Datei ist leer: {path!r}")
        header_str = ",".join(header_row)
        match = re.search(r"SamplingRate:([\d.]+)", header_str)
        if not match:
            raise ValueError(
                f"'SamplingRate' nicht im Header gefunden.\n  Header: {header_str!r}"
            )
        sampling_rate = float(match.group(1))
        if sampling_rate <= 0:
            raise ValueError(f"Ungültige Samplingrate im Header: {sampling_rate}")

        # --- data rows ---
        measurements: list[tuple[datetime, float]] = []
        for lineno, row in enumerate(reader, start=2):
            if len(row) < 3:
                continue  # skip unexpected short or empty lines
            try:
                dt = datetime.strptime(
                    f"{row[0].strip()},{row[1].strip()}", "%d-%m-%Y,%H:%M:%S"
                )
                value = float(row[2].strip())
            except ValueError as exc:
                raise ValueError(f"Fehler in Zeile {lineno}: {row!r}") from exc
            measurements.append((dt, value))

    if not measurements:
        raise ValueError(f"Keine Messdaten in Datei gefunden: {path!r}")

    return CsvData(sampling_rate=sampling_rate, measurements=measurements)


def interpolate_timestamps(data: CsvData) -> list[tuple[datetime, float]]:
    """Add sub-second precision to timestamps based on sampling rate.

    Consecutive rows that share the same second-level timestamp form a group.
    The expected number of measurements per second is round(1 / sampling_rate).

    First group: right-aligned — the recording started mid-second, so only the
    last k slots of that second were captured (e.g. one measurement in a 0.2 s
    dataset gets offset +0.8 s, not +0.0 s).

    All other groups: left-aligned starting at offset 0 (incomplete groups at
    the end or in the middle started from the beginning of the second).
    """
    result: list[tuple[datetime, float]] = []
    measurements = data.measurements
    dt = timedelta(seconds=data.sampling_rate)
    expected = round(1.0 / data.sampling_rate)
    n = len(measurements)
    i = 0
    first_group = True
    while i < n:
        base_ts = measurements[i][0]
        j = i + 1
        while j < n and measurements[j][0] == base_ts:
            j += 1
        group_size = j - i
        # right-align only the first group to account for a mid-second start
        start_slot = (expected - group_size) if first_group else 0
        for k in range(group_size):
            result.append((base_ts + (start_slot + k) * dt, measurements[i + k][1]))
        first_group = False
        i = j
    return result


def compute_moving_leq(
    samples: list[tuple[datetime, float]],
    integration_time: float,
) -> list[tuple[datetime, float]]:
    """Compute moving Leq (equivalent sound level) over a sliding time window.

    For each point at time t, all samples in [t - T/2, t + T/2] are averaged
    in the energy domain: Leq = 10 * log10(mean(10^(dB_i / 10))).
    At dataset edges the window is clipped to the available data (no padding).
    Uses a sliding deque for O(N) complexity.
    """
    n = len(samples)
    if n == 0:
        return []
    half = timedelta(seconds=integration_time / 2)
    result: list[tuple[datetime, float]] = []
    window: deque[tuple[datetime, float]] = deque()  # (timestamp, linear energy)
    window_sum = 0.0
    right = 0

    for i in range(n):
        t_center = samples[i][0]
        t_lo = t_center - half
        t_hi = t_center + half

        # expand right edge: add all samples up to t_hi
        while right < n and samples[right][0] <= t_hi:
            energy = 10 ** (samples[right][1] / 10)
            window.append((samples[right][0], energy))
            window_sum += energy
            right += 1

        # shrink left edge: remove samples that have fallen outside t_lo
        while window and window[0][0] < t_lo:
            window_sum -= window.popleft()[1]

        leq = 10 * math.log10(window_sum / len(window))
        result.append((t_center, leq))

    return result


# ---------------------------------------------------------------------------
# WAV streaming
# ---------------------------------------------------------------------------

@dataclass
class WavInfo:
    """WAV file metadata from header."""
    sample_rate: int   # Hz
    channels:    int
    bit_depth:   int   # bits per sample
    n_frames:    int   # total audio frames
    duration:    float # total duration in seconds


def read_wav_info(path: str) -> WavInfo:
    """Read and validate WAV file header."""
    with wave.open(path, 'rb') as wf:
        info = WavInfo(
            sample_rate=wf.getframerate(),
            channels=wf.getnchannels(),
            bit_depth=wf.getsampwidth() * 8,
            n_frames=wf.getnframes(),
            duration=wf.getnframes() / wf.getframerate(),
        )
    if info.bit_depth not in (16, 24, 32):
        raise ValueError(
            f"Nicht unterstützte Bit-Tiefe: {info.bit_depth} Bit  ({path!r})"
        )
    return info


def _block_mean_sq(raw: bytes, samp_width: int) -> float:
    """Return mean squared normalized amplitude for one block of PCM data."""
    if len(raw) == 0:
        return 0.0
    if samp_width == 2:
        norm = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif samp_width == 4:
        norm = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / float(1 << 31)
    else:
        # 24-bit: reconstruct signed int32 from 3-byte little-endian groups
        arr  = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        vals = (arr[:, 0].astype(np.int32)
                | (arr[:, 1].astype(np.int32) << 8)
                | (arr[:, 2].astype(np.int32) << 16))
        vals = np.where(vals >= 0x800000, vals - 0x1000000, vals)
        norm = vals.astype(np.float64) / float(1 << 23)
    return float(np.mean(norm ** 2))


def stream_wav_leq(
    paths: list[str],
    output_interval: float,
    integration_time: float,
) -> list[tuple[float, float]]:
    """Stream one or more WAV files and compute moving dBFS Leq.

    Multiple files are treated as one continuous stream; the sliding window
    runs seamlessly across file boundaries.  All files must share the same
    sample rate, channel count, and bit depth.

    Returns list of (time_offset_seconds, dBFS) relative to the start of
    the first file.  Only one block of raw audio is in memory at a time.

    Phase 1 – streaming : read one block per output interval per file,
               append only its mean-squared energy (a single float).
    Phase 2 – sliding window Leq via prefix sums (O(N)).
    """
    if not paths:
        raise ValueError("Keine WAV-Dateien angegeben.")

    infos = [read_wav_info(p) for p in paths]
    ref   = infos[0]
    for info, path in zip(infos[1:], paths[1:]):
        if (info.sample_rate != ref.sample_rate
                or info.channels  != ref.channels
                or info.bit_depth != ref.bit_depth):
            raise ValueError(
                f"WAV-Dateien haben unterschiedliche Formate:\n"
                f"  {paths[0]}: {ref.sample_rate} Hz  {ref.channels}ch  {ref.bit_depth}-bit\n"
                f"  {path}: {info.sample_rate} Hz  {info.channels}ch  {info.bit_depth}-bit"
            )

    samp_width      = ref.bit_depth // 8
    frames_per_block = max(1, round(ref.sample_rate * output_interval))

    # Phase 1: stream every file, accumulate one energy value per block
    block_energies: list[float] = []
    for idx, (path, info) in enumerate(zip(paths, infos), 1):
        label = f"[{idx}/{len(paths)}] " if len(paths) > 1 else ""
        print(f"  {label}{path}  [", end="", flush=True)
        with wave.open(path, 'rb') as wf:
            n_total = wf.getnframes()
            n_done  = 0
            step    = max(1, n_total // 20)
            last    = -step
            while True:
                raw = wf.readframes(frames_per_block)
                if not raw:
                    break
                n_done += len(raw) // (samp_width * info.channels)
                block_energies.append(_block_mean_sq(raw, samp_width))
                if n_done - last >= step:
                    print(".", end="", flush=True)
                    last = n_done
        print("]")

    # Phase 2: sliding window Leq via prefix sums (O(N))
    n    = len(block_energies)
    half = round((integration_time / 2) / output_interval)
    prefix = [0.0] * (n + 1)
    for i, e in enumerate(block_energies):
        prefix[i + 1] = prefix[i] + e

    result: list[tuple[float, float]] = []
    for i in range(n):
        lo   = max(0, i - half)
        hi   = min(n, i + half + 1)
        avg  = (prefix[hi] - prefix[lo]) / (hi - lo)
        dbfs = 10 * math.log10(avg) if avg > 0.0 else -math.inf
        result.append((i * output_interval, dbfs))

    return result


def align_wav_timestamps(
    leq_wav_raw: list[tuple[float, float]],
    audio_start: datetime,
) -> list[tuple[datetime, float]]:
    """Convert WAV time offsets (seconds from WAV start) to absolute datetimes.

    audio_start must be a full datetime (date + time), not just a time object.
    The date is typically taken from the CSV measurements.
    """
    return [
        (audio_start + timedelta(seconds=offset), dbfs)
        for offset, dbfs in leq_wav_raw
    ]


# ---------------------------------------------------------------------------
# Plot / Visualization layer
# ---------------------------------------------------------------------------

def _best_legend_loc(ax_left, ax_right=None) -> str:
    """Return the corner with the fewest plotted data points across both axes.

    Evaluates the four corners of each axis independently (each axis has its
    own y-scale), counts how many finite data points fall in each quadrant,
    and picks the emptiest corner.  Uses numpy for speed on large datasets.
    """
    scores: dict[str, int] = {
        "upper left": 0, "upper right": 0,
        "lower left": 0, "lower right": 0,
    }
    axes = [ax_left] if ax_right is None else [ax_left, ax_right]
    for ax in axes:
        xl0, xl1 = ax.get_xlim()
        yl0, yl1 = ax.get_ylim()
        xmid = (xl0 + xl1) / 2
        ymid = (yl0 + yl1) / 2
        for line in ax.get_lines():
            xd = np.asarray(line.get_xdata(orig=False), dtype=float)
            yd = np.asarray(line.get_ydata(orig=False), dtype=float)
            valid = np.isfinite(xd) & np.isfinite(yd)
            xd, yd = xd[valid], yd[valid]
            if xd.size == 0:
                continue
            right = xd > xmid
            upper = yd > ymid
            scores["upper left"]  += int(np.sum(~right &  upper))
            scores["upper right"] += int(np.sum( right &  upper))
            scores["lower left"]  += int(np.sum(~right & ~upper))
            scores["lower right"] += int(np.sum( right & ~upper))
    return min(scores, key=scores.__getitem__)


def _plot_worker(
    leq_csv:         list[tuple[datetime, float]] | None,
    leq_wav:         list[tuple[datetime, float]] | None,
    integration_time: float,
    save_png:        bool,
    save_svg:        bool,
    export_basename: str | None,
) -> None:
    """Build figure, save files, and display interactively.

    Runs in a subprocess so the parent process can exit while this window
    remains open.
    """
    if leq_csv is None and leq_wav is None:
        return

    first_ts = leq_csv[0][0] if leq_csv else leq_wav[0][0]
    date_str  = first_ts.strftime("%Y-%m-%d")
    basename  = export_basename if export_basename else date_str
    title     = (f"Pegelanalyse  {export_basename}  ({date_str})"
                 if export_basename else f"Pegelanalyse  {date_str}")

    fig, ax_left = plt.subplots(figsize=(14, 5))

    # --- left axis: dB(A) ---
    if leq_csv is not None:
        times  = [t for t, _ in leq_csv]
        values = [v for _, v in leq_csv]
        ax_left.plot(times, values, color="steelblue", linewidth=0.8,
                     label=f"Leq dB(A)  (T={integration_time:.0f} s)")
        ax_left.set_ylim(0, 90)
        ax_left.set_ylabel("Schallpegel [dB(A)]")
    else:
        ax_left.set_yticks([])

    ax_left.set_xlabel("Uhrzeit")
    ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_left.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax_left.grid(True, alpha=0.3)

    # --- right axis: dBFS ---
    ax_right = None
    if leq_wav is not None:
        ax_right = ax_left.twinx()
        times_w  = [t for t, _ in leq_wav]
        # replace -inf (silence) with NaN so matplotlib leaves a gap
        values_w = [v if math.isfinite(v) else float("nan") for _, v in leq_wav]
        ax_right.plot(times_w, values_w, color="red", linewidth=0.8,
                      label=f"Leq dBFS  (T={integration_time:.0f} s)")
        ax_right.set_ylim(-80, 0)
        ax_right.set_ylabel("Aufnahme-Pegel [dBFS]", color="red")
        ax_right.tick_params(axis="y", colors="red")

    # --- x-axis limits: set before legend so _best_legend_loc sees correct xlim ---
    all_datasets = [d for d in (leq_csv, leq_wav) if d]
    ax_left.set_xlim(
        min(d[0][0]  for d in all_datasets),
        max(d[-1][0] for d in all_datasets),
    )

    # --- combined legend: placed in the emptiest corner ---
    lines, labels = ax_left.get_legend_handles_labels()
    if ax_right is not None:
        lines_r, labels_r = ax_right.get_legend_handles_labels()
        lines  += lines_r
        labels += labels_r
    ax_left.legend(lines, labels, loc=_best_legend_loc(ax_left, ax_right))

    fig.suptitle(title)
    fig.autofmt_xdate()
    fig.tight_layout()

    for ext, flag in (("svg", save_svg), ("png", save_png)):
        if flag:
            fig.savefig(f"{basename}.{ext}", dpi=150 if ext == "png" else None)
            print(f"Gespeichert: {basename}.{ext}")

    plt.show()


def _show_plot_from_file(path: str) -> None:
    """Load serialised plot data from a temp file and display it (child side)."""
    try:
        with open(path, 'rb') as f:
            kwargs = pickle.load(f)
    except OSError:
        return
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    _plot_worker(**kwargs)


def create_plot(
    leq_csv:         list[tuple[datetime, float]] | None,
    integration_time: float,
    leq_wav:         list[tuple[datetime, float]] | None = None,
    save_png:        bool = False,
    save_svg:        bool = False,
    export_basename: str  | None = None,
) -> None:
    """Display the plot in a detached child process; return immediately.

    Strategy depends on platform and whether the app is frozen by PyInstaller:

    Linux/macOS frozen (--onefile): use os.fork().
      Re-spawning the same frozen binary would cause the bootloader to
      re-extract the bundle into a fresh temp dir; that extraction races with
      the parent's cleanup and fails reliably.  fork() is simpler: the child
      inherits all already-loaded modules and library mappings, so no
      re-extraction is needed.  os.setsid() detaches the child from the
      parent's session so it outlives the parent.

    Windows frozen and non-frozen (all platforms): serialise plot data to a
      temp file and spawn a fresh subprocess.  On Windows the PyInstaller temp
      dir is not deleted while the parent is still running, so the child can
      import freely.  In development mode there is no PyInstaller at all.
    """
    data = dict(
        leq_csv=leq_csv,
        leq_wav=leq_wav,
        integration_time=integration_time,
        save_png=save_png,
        save_svg=save_svg,
        export_basename=export_basename,
    )

    # --- fork path: Linux / macOS frozen ---
    if getattr(sys, 'frozen', False) and hasattr(os, 'fork'):
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            os.setsid()
            _plot_worker(**data)
            os._exit(0)
        return

    # --- subprocess path: non-frozen (dev) and frozen Windows ---
    with tempfile.NamedTemporaryFile(suffix='.pkl', delete=False, mode='wb') as f:
        pickle.dump(data, f)
        tmp_path = f.name

    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--_plot-data', tmp_path]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), '--_plot-data', tmp_path]

    if sys.platform == 'win32':
        popen_kwargs: dict = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        popen_kwargs = {'start_new_session': True}
    subprocess.Popen(cmd, **popen_kwargs)


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

try:
    import tkinter as tk
    _TKINTER_AVAILABLE = True
except ImportError:
    _TKINTER_AVAILABLE = False


def _parse_dnd_paths(raw: str) -> list[str]:
    """Parse the Tcl-list-formatted path string returned by tkinterdnd2.

    tkinterdnd2 encodes dropped file paths as a Tcl list:
      - single path, no spaces  →  /path/to/file.wav
      - single path with spaces →  {/path/to/my file.wav}
      - multiple paths          →  {/p/a.wav} {/p/b.wav}   or  /p/a.wav /p/b.wav
    Elements without spaces are separated by whitespace; elements that
    contain spaces are wrapped in braces.
    """
    paths: list[str] = []
    raw = raw.strip()
    i = 0
    while i < len(raw):
        if raw[i] == '{':
            j = raw.find('}', i + 1)
            if j == -1:
                break
            paths.append(raw[i + 1:j])
            i = j + 1
        else:
            j = raw.find(' ', i)
            if j == -1:
                paths.append(raw[i:])
                break
            paths.append(raw[i:j])
            i = j
        while i < len(raw) and raw[i] == ' ':
            i += 1
    return [p for p in paths if p]


class PegelanalyseGUI:
    _CSV_EXTS = ('.txt', '.csv')
    _WAV_EXT  = '.wav'

    # colours
    _C_IDLE_CSV = '#dbeafe'   # light blue
    _C_IDLE_WAV = '#fde8e8'   # light red
    _C_OK       = '#bbf7d0'   # light green
    _C_ERR      = '#fee2e2'   # light red (error)

    def __init__(self, root: 'tk.Tk') -> None:
        self.root = root
        self.root.title("Pegelanalyse")
        self.root.minsize(580, 400)
        self.root.resizable(True, True)

        self.csv_path:  str | None = None
        self.wav_paths: list[str]  = []
        self._basename_auto: str | None = None  # tracks last auto-filled export name

        self._build_drop_zones()
        self._build_controls()
        self._build_statusbar()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_drop_zones(self) -> None:
        from tkinterdnd2 import DND_FILES

        outer = tk.Frame(self.root, padx=10, pady=10)
        outer.pack(fill='both', expand=True)

        # ---- CSV zone ----
        csv_frame = tk.LabelFrame(outer, text=" CSV (Pegelmessung) ", padx=4, pady=4)
        csv_frame.pack(side='left', fill='both', expand=True, padx=(0, 5))

        self.csv_lbl = tk.Label(
            csv_frame,
            text="CSV hier ablegen\n(.txt / .csv)",
            bg=self._C_IDLE_CSV, relief='groove', bd=2,
            wraplength=260, justify='center', height=5,
        )
        self.csv_lbl.pack(fill='x')
        self.csv_lbl.drop_target_register(DND_FILES)
        self.csv_lbl.dnd_bind('<<Drop>>', self._on_csv_drop)

        self.csv_info_lbl = tk.Label(
            csv_frame, text="", anchor='w', justify='left',
            fg='#374151', padx=2, pady=2,
        )
        self.csv_info_lbl.pack(fill='x')

        # ---- WAV zone ----
        wav_frame = tk.LabelFrame(outer, text=" WAV-Aufnahme(n) ", padx=4, pady=4)
        wav_frame.pack(side='left', fill='both', expand=True, padx=(5, 0))

        self.wav_lbl = tk.Label(
            wav_frame,
            text="WAV-Datei(en) hier ablegen\n(mehrere gleichzeitig möglich)",
            bg=self._C_IDLE_WAV, relief='groove', bd=2,
            wraplength=260, justify='center', height=5,
        )
        self.wav_lbl.pack(fill='x')
        self.wav_lbl.drop_target_register(DND_FILES)
        self.wav_lbl.dnd_bind('<<Drop>>', self._on_wav_drop)

        self.wav_info_lbl = tk.Label(
            wav_frame, text="", anchor='w', justify='left',
            fg='#374151', padx=2, pady=2,
        )
        self.wav_info_lbl.pack(fill='x')

    def _build_controls(self) -> None:
        # thin separator line
        tk.Frame(self.root, height=1, bg='#d1d5db').pack(fill='x', padx=10)

        ctrl = tk.Frame(self.root, padx=10, pady=8)
        ctrl.pack(fill='x')

        # ---- Row 0: Audio-Start | Integrationszeit ----
        tk.Label(ctrl, text="Audio-Start:", anchor='w').grid(
            row=0, column=0, sticky='w', padx=(0, 4), pady=3)
        self.audio_start_var = tk.StringVar(value="09:55:00")
        tk.Entry(ctrl, textvariable=self.audio_start_var, width=10).grid(
            row=0, column=1, sticky='w', padx=(0, 20), pady=3)

        tk.Label(ctrl, text="Integrationszeit:", anchor='w').grid(
            row=0, column=2, sticky='w', padx=(0, 4), pady=3)
        self.integration_time_var = tk.StringVar(value="20")
        tk.Entry(ctrl, textvariable=self.integration_time_var, width=6).grid(
            row=0, column=3, sticky='w', pady=3)
        tk.Label(ctrl, text="s", anchor='w').grid(
            row=0, column=4, sticky='w', padx=(2, 0), pady=3)

        # ---- Row 1: Export-Dateiname | Checkboxen ----
        tk.Label(ctrl, text="Export-Dateiname:", anchor='w').grid(
            row=1, column=0, sticky='w', padx=(0, 4), pady=3)
        self.basename_var = tk.StringVar(value="")
        tk.Entry(ctrl, textvariable=self.basename_var, width=24).grid(
            row=1, column=1, columnspan=2, sticky='ew', padx=(0, 20), pady=3)

        self.save_png_var = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl, text="PNG", variable=self.save_png_var).grid(
            row=1, column=3, sticky='w', pady=3)
        self.save_svg_var = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl, text="SVG", variable=self.save_svg_var).grid(
            row=1, column=4, sticky='w', pady=3)

        # ---- Row 2: Buttons (Reset left, Generiere Plot right) ----
        ctrl.columnconfigure(5, weight=1)
        self.reset_btn = tk.Button(
            ctrl, text="Reset", command=self._on_reset, width=10)
        self.reset_btn.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))

        self.plot_btn = tk.Button(
            ctrl, text="Generiere Plot", command=self._on_generate_plot, width=16)
        self.plot_btn.grid(row=2, column=2, columnspan=3, sticky='e', pady=(8, 0))

    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bd=1, relief='sunken')
        bar.pack(side='bottom', fill='x')
        self.status_lbl = tk.Label(
            bar, text="", anchor='w', padx=6, pady=2,
            fg='#b91c1c',   # dark red — only used for errors
        )
        self.status_lbl.pack(fill='x')

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_error(self, msg: str) -> None:
        self.status_lbl.config(text=msg, fg='#b91c1c')

    def _clear_status(self) -> None:
        self.status_lbl.config(text='', fg='#b91c1c')

    # ------------------------------------------------------------------
    # Drop handlers
    # ------------------------------------------------------------------

    def _on_csv_drop(self, event) -> None:
        paths = _parse_dnd_paths(event.data)
        valid = [p for p in paths if p.lower().endswith(self._CSV_EXTS)]
        if not valid:
            self._set_error("CSV: Ungültige Datei — bitte .txt oder .csv ablegen.")
            return
        self._clear_status()
        self.csv_path = valid[0]
        self.csv_lbl.config(text=os.path.basename(self.csv_path), bg=self._C_OK)
        try:
            data = parse_csv(self.csv_path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self._set_error(f"CSV Fehler: {exc}")
            self.csv_info_lbl.config(text="")
            return
        t0 = data.measurements[0][0]
        t1 = data.measurements[-1][0]
        dur = (t1 - t0).total_seconds() / 60
        self.csv_info_lbl.config(text=(
            f"{t0:%H:%M:%S} bis {t1:%H:%M:%S}  ({dur:.1f} min)\n"
            f"{len(data.measurements)} Werte  ·  {data.sampling_rate} s Samplingrate"
        ))
        # auto-fill export basename from CSV date (only if field is empty or still auto)
        auto = str(t0.date())
        current = self.basename_var.get()
        if not current or current == self._basename_auto:
            self.basename_var.set(auto)
            self._basename_auto = auto

    def _on_wav_drop(self, event) -> None:
        paths = _parse_dnd_paths(event.data)
        valid = [p for p in paths if p.lower().endswith(self._WAV_EXT)]
        if not valid:
            self._set_error("WAV: Ungültige Datei(en) — bitte .wav ablegen.")
            return
        self._clear_status()
        # Accumulate and deduplicate; sort keeps chronological order for
        # date-prefixed filenames (e.g. 20260215095052_…wav).
        self.wav_paths = sorted(set(self.wav_paths) | set(valid))
        names = '\n'.join(os.path.basename(p) for p in self.wav_paths)
        self.wav_lbl.config(
            text=f"{len(self.wav_paths)} Datei(en):\n{names}",
            bg=self._C_OK,
        )
        try:
            infos = [read_wav_info(p) for p in self.wav_paths]
        except (OSError, ValueError, wave.Error) as exc:
            self._set_error(f"WAV Fehler: {exc}")
            self.wav_info_lbl.config(text="")
            return
        ref = infos[0]
        total_dur = sum(i.duration for i in infos) / 60
        self.wav_info_lbl.config(text=(
            f"Gesamt: {total_dur:.1f} min\n"
            f"{ref.sample_rate} Hz  ·  {ref.channels}ch  ·  {ref.bit_depth}-bit"
        ))

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_reset(self) -> None:
        self.csv_path = None
        self.wav_paths = []
        self._basename_auto = None

        self.csv_lbl.config(text="CSV hier ablegen\n(.txt / .csv)", bg=self._C_IDLE_CSV)
        self.csv_info_lbl.config(text="")
        self.wav_lbl.config(
            text="WAV-Datei(en) hier ablegen\n(mehrere gleichzeitig möglich)",
            bg=self._C_IDLE_WAV,
        )
        self.wav_info_lbl.config(text="")

        self.audio_start_var.set("09:55:00")
        self.integration_time_var.set("20")
        self.basename_var.set("")
        self.save_png_var.set(False)
        self.save_svg_var.set(False)
        self._clear_status()

    def _set_busy(self, busy: bool) -> None:
        state = 'disabled' if busy else 'normal'
        self.plot_btn.config(state=state)
        self.reset_btn.config(state=state)

    def _on_pipeline_done(self, saved_files: list[str]) -> None:
        self._set_busy(False)
        if saved_files:
            self.status_lbl.config(
                text="Gespeichert: " + ", ".join(saved_files), fg='#374151')
        else:
            self.status_lbl.config(text="Plot erstellt.", fg='#374151')

    def _on_pipeline_error(self, msg: str) -> None:
        self._set_busy(False)
        self._set_error(f"Fehler: {msg}")

    def _on_generate_plot(self) -> None:
        # ---- at least one source required ----
        if self.csv_path is None and not self.wav_paths:
            self._set_error("Bitte mindestens ein CSV- oder WAV-File ablegen.")
            return

        # ---- audio-start format ----
        audio_start_str = self.audio_start_var.get().strip()
        try:
            datetime.strptime(audio_start_str, "%H:%M:%S")
        except ValueError:
            self._set_error("Audio-Start: Ungültiges Format — bitte HH:MM:SS eingeben.")
            return

        # ---- integration time ----
        try:
            integration_time = float(self.integration_time_var.get().strip())
            if integration_time <= 0:
                raise ValueError
        except ValueError:
            self._set_error("Integrationszeit: Bitte eine positive Zahl eingeben.")
            return

        # Snapshot GUI state before handing off to the thread
        csv_path   = self.csv_path
        wav_paths  = list(self.wav_paths)
        save_png   = self.save_png_var.get()
        save_svg   = self.save_svg_var.get()
        basename   = self.basename_var.get().strip() or None

        self._set_busy(True)
        self.status_lbl.config(text="Verarbeite …", fg='#374151')

        def _worker() -> None:
            try:
                saved = _run_pipeline(
                    csv_path, wav_paths, audio_start_str,
                    integration_time, save_png, save_svg, basename,
                )
                self.root.after(0, lambda: self._on_pipeline_done(saved))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._on_pipeline_error(msg))

        threading.Thread(target=_worker, daemon=True).start()


def _run_pipeline(
    csv_path:         str | None,
    wav_paths:        list[str],
    audio_start_str:  str,
    integration_time: float,
    save_png:         bool,
    save_svg:         bool,
    export_basename:  str | None,
) -> list[str]:
    """Run the full processing pipeline (CSV + WAV → plot).

    Called from a background thread.  Returns the list of files saved so the
    GUI can display a confirmation message.  Raises on any error.
    """
    leq_csv: list[tuple[datetime, float]] | None = None
    output_interval = 0.2
    csv_date = None

    if csv_path:
        data = parse_csv(csv_path)
        output_interval = data.sampling_rate
        csv_date = data.measurements[0][0].date()
        samples  = interpolate_timestamps(data)
        leq_csv  = compute_moving_leq(samples, integration_time)
        if export_basename is None:
            export_basename = str(csv_date)

    leq_wav: list[tuple[datetime, float]] | None = None

    if wav_paths:
        ref_date = csv_date if csv_date is not None else datetime.today().date()
        audio_start = datetime.combine(
            ref_date,
            datetime.strptime(audio_start_str, "%H:%M:%S").time(),
        )
        leq_wav_raw = stream_wav_leq(wav_paths, output_interval, integration_time)
        if not leq_wav_raw:
            raise ValueError("WAV-Dateien enthalten keine Audiodaten.")
        leq_wav = align_wav_timestamps(leq_wav_raw, audio_start)

    create_plot(
        leq_csv, integration_time, leq_wav,
        save_png=save_png,
        save_svg=save_svg,
        export_basename=export_basename,
    )

    saved: list[str] = []
    if export_basename:
        if save_png:
            saved.append(f"{export_basename}.png")
        if save_svg:
            saved.append(f"{export_basename}.svg")
    return saved


def run_gui() -> None:
    if not _TKINTER_AVAILABLE:
        print("Fehler: tkinter nicht verfügbar — GUI kann nicht gestartet werden.",
              file=sys.stderr)
        return
    try:
        from tkinterdnd2 import TkinterDnD
    except ImportError:
        root = tk.Tk()
        root.withdraw()
        import tkinter.messagebox as mb
        mb.showerror(
            "Fehlende Abhängigkeit",
            "tkinterdnd2 ist nicht installiert.\n\n"
            "Bitte ausführen:\n    pip install tkinterdnd2",
        )
        root.destroy()
        return

    root = TkinterDnD.Tk()
    PegelanalyseGUI(root)
    root.mainloop()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse und Visualisierung von Schallpegelmessungen."
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="CSV-Datei mit Schallpegelmessungen vom Sound Level Meter",
    )
    parser.add_argument(
        "--wav",
        metavar="FILE",
        nargs="+",
        help="WAV-Audiodatei(en) mit der Aufnahme (mehrere Dateien möglich)",
    )
    parser.add_argument(
        "--audio-start",
        metavar="HH:MM:SS",
        default="09:55:00",
        help="Startzeitpunkt der Audioaufnahme für zeitliches Alignment (Standard: 09:55:00)",
    )
    parser.add_argument(
        "--integration-time",
        metavar="SECONDS",
        type=float,
        default=20.0,
        help="Integrationszeit für Moving-RMS in Sekunden (Standard: 20)",
    )
    parser.add_argument(
        "--save-png",
        action="store_true",
        help="Plot als PNG-Datei speichern",
    )
    parser.add_argument(
        "--save-svg",
        action="store_true",
        help="Plot als SVG-Datei speichern",
    )
    parser.add_argument(
        "--export-basename",
        metavar="NAME",
        help="Basisname für exportierte Dateien ohne Dateiendung (Standard: Datum aus CSV)",
    )
    args = parser.parse_args()

    if args.csv is None and args.wav is None:
        parser.error("Mindestens --csv oder --wav muss angegeben werden.")

    if args.audio_start is not None:
        if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", args.audio_start):
            parser.error("--audio-start muss im Format HH:MM:SS angegeben werden (z.B. 10:50:52).")
        try:
            datetime.strptime(args.audio_start, "%H:%M:%S")
        except ValueError:
            parser.error("--audio-start enthält ungültige Uhrzeit.")

    return args


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Internal: invoked by create_plot's subprocess to display the plot
    if len(sys.argv) == 3 and sys.argv[1] == '--_plot-data':
        _show_plot_from_file(sys.argv[2])
        return

    # No CLI arguments → launch GUI
    if len(sys.argv) == 1:
        run_gui()
        return

    args = parse_args()

    leq_csv: list[tuple[datetime, float]] | None = None
    output_interval = 0.2   # default when no CSV is provided

    if args.csv:
        try:
            data = parse_csv(args.csv)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            sys.exit(f"Fehler beim Lesen der CSV-Datei: {exc}")
        output_interval = data.sampling_rate
        t_start  = data.measurements[0][0]
        t_end    = data.measurements[-1][0]
        duration = (t_end - t_start).total_seconds()
        print(f"CSV:  {t_start:%H:%M:%S} bis {t_end:%H:%M:%S}"
              f"  ({duration/60:.1f} min, {len(data.measurements)} Werte,"
              f"  {data.sampling_rate} s Samplingrate)")
        samples = interpolate_timestamps(data)
        leq_csv = compute_moving_leq(samples, args.integration_time)

    leq_wav: list[tuple[datetime, float]] | None = None

    if args.wav:
        try:
            infos = [read_wav_info(p) for p in args.wav]
        except (OSError, ValueError, wave.Error) as exc:
            sys.exit(f"Fehler beim Lesen der WAV-Datei: {exc}")
        total_dur = sum(i.duration for i in infos)
        ref = infos[0]
        print(f"WAV:  {len(args.wav)} Datei(en)  {ref.channels}ch  {ref.sample_rate} Hz"
              f"  {ref.bit_depth}-bit  gesamt {total_dur / 60:.1f} min")
        print(f"  Berechne Leq (T={args.integration_time:.0f} s)")
        try:
            leq_wav_raw = stream_wav_leq(args.wav, output_interval, args.integration_time)
        except (OSError, ValueError, wave.Error) as exc:
            sys.exit(f"Fehler beim Verarbeiten der WAV-Datei: {exc}")

        # Temporal alignment: combine date from CSV (or WAV-only handled later)
        # with the time from --audio-start
        if args.csv:
            csv_date = data.measurements[0][0].date()
        else:
            csv_date = datetime.today().date()   # WAV-only: fallback, Feature 10
        audio_start = datetime.combine(
            csv_date,
            datetime.strptime(args.audio_start, "%H:%M:%S").time(),
        )
        if not leq_wav_raw:
            sys.exit("Fehler: WAV-Dateien enthalten keine Audiodaten.")
        leq_wav = align_wav_timestamps(leq_wav_raw, audio_start)

        wav_end = leq_wav[-1][0]
        vals = [v for _, v in leq_wav if math.isfinite(v)]
        print(f"  Audio-Start: {audio_start:%H:%M:%S}  Ende: {wav_end:%H:%M:%S}")
        if vals:
            print(f"  dBFS:  min={min(vals):.1f}  max={max(vals):.1f}"
                  f"  mittel={sum(vals)/len(vals):.1f}")

        if leq_csv is not None:
            csv_t0  = leq_csv[0][0]
            csv_t1  = leq_csv[-1][0]
            wav_t0  = leq_wav[0][0]
            wav_t1  = leq_wav[-1][0]
            ovl_t0  = max(csv_t0, wav_t0)
            ovl_t1  = min(csv_t1, wav_t1)
            if ovl_t0 < ovl_t1:
                ovl_s = (ovl_t1 - ovl_t0).total_seconds()
                print(f"  Ueberlappung: {ovl_t0:%H:%M:%S} bis {ovl_t1:%H:%M:%S}"
                      f"  ({ovl_s/60:.1f} min)")
            else:
                print("  Warnung: CSV und WAV haben keinen zeitlichen Ueberlapp.")

    if leq_csv is not None or leq_wav is not None:
        create_plot(
            leq_csv, args.integration_time, leq_wav,
            save_png=args.save_png,
            save_svg=args.save_svg,
            export_basename=args.export_basename,
        )


if __name__ == "__main__":
    main()
