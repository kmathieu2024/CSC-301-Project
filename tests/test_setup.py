
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_process_file():
    path = ROOT / "test_data/processes.json"

    assert path.exists()

    with open(path, encoding="utf-8") as file:
        data = json.load(file)

    assert len(data["processes"]) > 0


def test_memory_trace():
    path = ROOT / "test_data/trace.txt"

    assert path.exists()
    assert path.read_text(encoding="utf-8").strip()


def test_file_script():
    path = ROOT / "test_data/fs_script.txt"

    assert path.exists()
    assert path.read_text(encoding="utf-8").strip()


def test_expected_results():
    expected = ROOT / "test_data/expected_results"

    assert (expected / "process_expected.md").exists()
    assert (expected / "memory_expected.md").exists()
    assert (expected / "file_expected.md").exists()