import pytest

from src.managers.file import (
    AuthorizationError,
    DirectoryNotEmptyError,
    FileAlreadyExistsError,
    FileManager,
    FileNotFoundError,
    FileSystemError,
    IsADirectoryError,
    NotADirectoryError,
)


class DenyWrites:
    def authorize(self, user, action, path):
        return action != "write"
class DenyAction:
    def __init__(self, denied_action):
        self.denied_action = denied_action

    def authorize(self, user, action, path):
        return action != self.denied_action

def test_basic_file_commands():
    fs = FileManager()
    fs.execute_command("CREATE notes.txt")
    fs.execute_command("WRITE notes.txt Hello_OS")
    assert fs.execute_command("READ notes.txt") == "Hello_OS"
    fs.execute_command("DELETE notes.txt")
    assert fs.list_directory("/") == []


def test_hierarchical_paths_and_navigation():
    fs = FileManager()
    fs.make_directory("/home")
    fs.make_directory("/home/student")
    fs.create_file("/home/student/notes.txt", "hello")
    assert fs.list_directory("/home") == ["student"]
    assert fs.change_directory("/home/student") == "/home/student"
    assert fs.execute_command("PWD") == "/home/student"
    assert fs.read_file("../student/notes.txt") == "hello"
    assert fs.change_directory("..") == "/home"


def test_metadata_tracks_content_size():
    fs = FileManager()
    fs.create_file("a.txt", "abc", owner="learner")
    metadata = fs.get_metadata("a.txt")
    assert metadata.size == 3
    assert metadata.owner == "learner"
    fs.write_file("a.txt", "longer")
    assert fs.get_metadata("a.txt").size == 6


def test_errors_for_existing_missing_and_wrong_type():
    fs = FileManager()
    fs.create_file("a.txt")
    with pytest.raises(FileAlreadyExistsError):
        fs.create_file("a.txt")
    with pytest.raises(FileNotFoundError):
        fs.read_file("missing.txt")
    fs.make_directory("folder")
    with pytest.raises(IsADirectoryError):
        fs.read_file("folder")
    with pytest.raises(NotADirectoryError):
        fs.change_directory("a.txt")


def test_nonempty_directory_cannot_be_removed():
    fs = FileManager()
    fs.make_directory("folder")
    fs.create_file("folder/a.txt")
    with pytest.raises(DirectoryNotEmptyError):
        fs.remove_directory("folder")


def test_write_request_requires_authorization():
    fs = FileManager(authorizer=DenyWrites())
    fs.create_file("a.txt")
    with pytest.raises(AuthorizationError):
        fs.write_file("a.txt", "blocked", user="guest")

def test_paths_cannot_navigate_through_a_file():
    fs = FileManager()
    fs.create_file("/a.txt", "hello")

    with pytest.raises(NotADirectoryError):
        fs.list_directory("/a.txt/..")


def test_write_command_handles_empty_quoted_content():
    fs = FileManager()
    fs.create_file("empty.txt")

    fs.execute_command('WRITE empty.txt ""')

    assert fs.read_file("empty.txt") == ""


def test_write_command_preserves_quoted_spaces():
    fs = FileManager()
    fs.create_file("notes.txt")

    fs.execute_command('WRITE notes.txt "hello world"')

    assert fs.read_file("notes.txt") == "hello world"


def test_write_command_preserves_unquoted_content():
    fs = FileManager()
    fs.create_file("notes.txt")

    fs.execute_command("WRITE notes.txt Hello_OS")

    assert fs.read_file("notes.txt") == "Hello_OS"


def test_append_preserves_existing_file_content():
    fs = FileManager()
    fs.create_file("notes.txt", "Hello")

    fs.write_file("notes.txt", " world", append=True)

    assert fs.read_file("notes.txt") == "Hello world"
    assert fs.get_metadata("notes.txt").size == len("Hello world")


def test_root_directory_cannot_be_removed():
    fs = FileManager()

    with pytest.raises((FileSystemError, IsADirectoryError)):
        fs.remove_directory("/")


def test_current_working_directory_cannot_be_removed():
    fs = FileManager()
    fs.make_directory("folder")
    fs.change_directory("folder")

    with pytest.raises(FileSystemError):
        fs.remove_directory(".")

def test_read_and_delete_are_allowed_when_only_writes_are_denied():
    fs = FileManager()
    fs.create_file("a.txt", "hello")

    fs.authorizer = DenyWrites()

    # DenyWrites only denies write, so reading and deleting should still work.
    assert fs.read_file("a.txt") == "hello"
    fs.delete_file("a.txt")

    assert fs.list_directory("/") == []


def test_authorization_denial_blocks_read():
    fs = FileManager()
    fs.create_file("a.txt", "hello")
    fs.authorizer = DenyAction("read")

    with pytest.raises(AuthorizationError):
        fs.read_file("a.txt")


def test_authorization_denial_blocks_delete():
    fs = FileManager()
    fs.create_file("a.txt", "hello")
    fs.authorizer = DenyAction("delete")

    with pytest.raises(AuthorizationError):
        fs.delete_file("a.txt")


def test_command_displays_missing_file_error():
    fs = FileManager()

    result = fs.execute_command("READ missing.txt")

    assert result.startswith("Error:")
    assert "not found" in result.lower()


def test_command_displays_duplicate_file_error():
    fs = FileManager()
    fs.execute_command("CREATE notes.txt")

    result = fs.execute_command("CREATE notes.txt")

    assert result.startswith("Error:")
    assert "already exists" in result.lower()


def test_command_displays_authorization_error():
    fs = FileManager(authorizer=DenyWrites())
    fs.execute_command("CREATE notes.txt")

    result = fs.execute_command("WRITE notes.txt blocked")

    assert result.startswith("Error:")
    assert "not authorized" in result.lower()


def test_command_displays_invalid_command_error():
    fs = FileManager()

    result = fs.execute_command("INVALID")

    assert result.startswith("Error:")
    assert "invalid command" in result.lower()


def test_command_displays_empty_command_error():
    fs = FileManager()

    result = fs.execute_command("")

    assert result.startswith("Error:")
    assert "non-empty" in result.lower()


def test_command_displays_malformed_quotes_error():
    fs = FileManager()

    result = fs.execute_command('WRITE notes.txt "unfinished')

    assert result.startswith("Error:")
    assert "invalid command" in result.lower()

def test_processes_have_independent_working_directories():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")

    fs.change_directory("/home/p1", process_id="P1")
    fs.change_directory("/home/p2", process_id="P2")

    assert fs.present_working_directory(process_id="P1") == "/home/p1"
    assert fs.present_working_directory(process_id="P2") == "/home/p2"


def test_process_relative_paths_use_own_working_directory():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")

    fs.create_file("/home/p1/notes.txt", "P1 file")
    fs.create_file("/home/p2/notes.txt", "P2 file")

    fs.change_directory("/home/p1", process_id="P1")
    fs.change_directory("/home/p2", process_id="P2")

    assert fs.read_file("notes.txt", process_id="P1") == "P1 file"
    assert fs.read_file("notes.txt", process_id="P2") == "P2 file"


def test_cd_for_one_process_does_not_change_another():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")
    fs.make_directory("/home/shared")

    fs.change_directory("/home/p1", process_id="P1")
    fs.change_directory("/home/p2", process_id="P2")

    fs.change_directory("/home/shared", process_id="P1")

    assert fs.present_working_directory(process_id="P1") == "/home/shared"
    assert fs.present_working_directory(process_id="P2") == "/home/p2"


def test_process_cannot_remove_another_process_working_directory():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")

    fs.change_directory("/home/p1", process_id="P1")
    fs.change_directory("/home/p2", process_id="P2")

    with pytest.raises(FileSystemError):
        fs.remove_directory("/home/p2", process_id="P1")


def test_execute_command_uses_process_specific_directory():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")

    fs.execute_command("CD /home/p1", process_id="P1")
    fs.execute_command("CD /home/p2", process_id="P2")

    assert fs.execute_command("PWD", process_id="P1") == "/home/p1"
    assert fs.execute_command("PWD", process_id="P2") == "/home/p2"