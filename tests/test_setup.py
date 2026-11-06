
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

def test_milestone3_inputs_have_required_lengths_and_commands():
    folder = ROOT / "test_data" / "milestone3"
    locality = (folder / "trace_locality.txt").read_text(encoding="utf-8").splitlines()
    stress = (folder / "trace_stress.txt").read_text(encoding="utf-8").splitlines()
    commands = (folder / "fs_script_assignment.txt").read_text(encoding="utf-8").splitlines()

    # Keep the original examples and check these longer examples separately.
    assert 10 <= len(locality) <= 200
    assert len(stress) == 200
    assert all(int(address, 0) >= 0 for address in locality)
    assert all(int(address) >= 0 for address in stress)
    assert len(commands) >= 12
    assert {line.split()[0] for line in commands} >= {
        "mkdir", "ls", "mkfile", "write", "append", "cat", "rm", "rmdir",
    }
