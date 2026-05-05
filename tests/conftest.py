from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def real_csv():
    return FIXTURES_DIR / "2026-2-15_11-22.txt"


@pytest.fixture
def minimal_csv(tmp_path):
    """A minimal valid CSV with two measurements at 0.2 s sampling rate."""
    f = tmp_path / "minimal.txt"
    f.write_text(
        "STANDARD SLM DATA SamplingRate:0.2;\n"
        "15-02-2026,10:00:00, 45.3, dBA\n"
        "15-02-2026,10:00:00, 46.1, dBA\n"
    )
    return f
