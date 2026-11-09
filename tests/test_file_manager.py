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

# Check the response as well as whether the file changed.
def test_basic_file_commands():
    fs = FileManager()
    assert fs.execute_command("CREATE notes.txt") is None
    assert fs.list_directory("/") == ["notes.txt"]
    assert fs.execute_command("WRITE notes.txt Hello_OS") == ""
    assert fs.execute_command("READ notes.txt") == "Hello_OS"
    assert fs.execute_command("DELETE notes.txt") is None
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
    # Different path spellings should still reach the same file.
    assert fs.read_file("/home//student/./notes.txt") == "hello"
    assert fs.change_directory("/../../..") == "/"


def test_metadata_tracks_content_size():
    fs = FileManager()
    fs.create_file("a.txt", "abc", owner="learner")
    metadata = fs.get_metadata("a.txt")
    assert metadata.size == 3
    assert metadata.owner == "learner"
    created = metadata.created_at
    assert metadata.permissions == "rwx"
    fs.write_file("a.txt", "longer")
    assert fs.get_metadata("a.txt").size == 6

    # A write changes the file, not its owner or original creation time.
    updated = fs.get_metadata("a.txt")
    assert updated.owner == "learner"
    assert updated.created_at == created
    assert updated.modified_at >= created


def test_errors_for_existing_missing_and_wrong_type():
    fs = FileManager()
    fs.create_file("a.txt")
    before = snapshot_tree(fs._root)
    with pytest.raises(FileAlreadyExistsError):
        fs.create_file("a.txt")
    with pytest.raises(FileNotFoundError):
        fs.read_file("missing.txt")
    assert snapshot_tree(fs._root) == before
    fs.make_directory("folder")
    before = snapshot_tree(fs._root)
    with pytest.raises(IsADirectoryError):
        fs.read_file("folder")
    with pytest.raises(NotADirectoryError):
        fs.change_directory("a.txt")

    assert snapshot_tree(fs._root) == before


def test_nonempty_directory_cannot_be_removed():
    fs = FileManager()
    fs.make_directory("folder")
    fs.create_file("folder/a.txt")
    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(DirectoryNotEmptyError):
        fs.remove_directory("folder")

    assert snapshot_tree(fs._root) == before


def test_write_request_requires_authorization():
    fs = FileManager(authorizer=DenyWrites())
    fs.create_file("a.txt")
    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(AuthorizationError):
        fs.write_file("a.txt", "blocked", user="guest")

    assert snapshot_tree(fs._root) == before

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

    # Appending nothing should keep the original content.
    fs.write_file("notes.txt", "", append=True)
    assert fs.read_file("notes.txt") == "Hello"

    fs.write_file("notes.txt", " world", append=True)

    assert fs.read_file("notes.txt") == "Hello world"
    assert fs.get_metadata("notes.txt").size == len("Hello world")


def test_root_directory_cannot_be_removed():
    fs = FileManager()

    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises((FileSystemError, IsADirectoryError)):
        fs.remove_directory("/")

    assert snapshot_tree(fs._root) == before


def test_current_working_directory_cannot_be_removed():
    fs = FileManager()
    fs.make_directory("folder")
    fs.change_directory("folder")

    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(FileSystemError):
        fs.remove_directory(".")

    assert snapshot_tree(fs._root) == before

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

    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(AuthorizationError):
        fs.read_file("a.txt")

    assert snapshot_tree(fs._root) == before


def test_authorization_denial_blocks_delete():
    fs = FileManager()
    fs.create_file("a.txt", "hello")
    fs.authorizer = DenyAction("delete")

    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(AuthorizationError):
        fs.delete_file("a.txt")

    assert snapshot_tree(fs._root) == before


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
    before = snapshot_tree(fs._root)

    # Try missing arguments and bad input as well as an unknown command.
    for command in ["INVALID", "", "   ", None, "CREATE", "WRITE /a", "LS / extra", 'WRITE /a "unfinished']:
        result = fs.execute_command(command)
        assert result.startswith("Error:")
        assert "invalid command" in result.lower()
        assert snapshot_tree(fs._root) == before


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

    # A rejection should leave the file system as it was.
    before = snapshot_tree(fs._root)
    with pytest.raises(FileSystemError):
        fs.remove_directory("/home/p2", process_id="P1")

    assert snapshot_tree(fs._root) == before


def test_execute_command_uses_process_specific_directory():
    fs = FileManager()

    fs.make_directory("/home")
    fs.make_directory("/home/p1")
    fs.make_directory("/home/p2")

    fs.execute_command("CD /home/p1", process_id="P1")
    fs.execute_command("CD /home/p2", process_id="P2")

    assert fs.execute_command("PWD", process_id="P1") == "/home/p1"
    assert fs.execute_command("PWD", process_id="P2") == "/home/p2"

def test_file_command_script_matches_expected_results():
    # None represents a directory; a string represents file contents.
    emptyTree = {"/": None}
    docsTree = {"/": None, "/docs": None}
    emptyFileTree = {**docsTree, "/docs/notes.txt": ""}
    writtenTree = {**docsTree, "/docs/notes.txt": "Hello OS"}
    archiveTree = {**writtenTree, "/docs/archive": None}
    temporaryTree = {**archiveTree, "/docs/archive/tmp.txt": ""}

    # Each row contains a command, expected response, and expected tree.
    # Error: entries specify the expected error-message text.
    commands = [
        ("MKDIR /docs", None, docsTree),
        ("CREATE /docs/notes.txt", None, emptyFileTree),
        ('WRITE /docs/notes.txt "Hello OS"', "", writtenTree),
        ("READ /docs/notes.txt", "Hello OS", writtenTree),
        ("LS /docs", ["notes.txt"], writtenTree),
        ("STAT /docs/notes.txt", 8, writtenTree),
        ("MKDIR /docs/archive", None, archiveTree),
        ("CREATE /docs/archive/tmp.txt", None, temporaryTree),
        ("DELETE /docs/archive/tmp.txt", None, archiveTree),
        ("RMDIR /docs/archive", None, writtenTree),
        ("MKDIR /docs", "Error:already exists", writtenTree),
        ("READ /missing.txt", "Error:not found", writtenTree),
        ('WRITE /docs "bad"', "Error:directory", writtenTree),
        ("RMDIR /docs", "Error:not empty", writtenTree),
        ("DELETE /docs", "Error:directory", writtenTree),
        ("CREATE /missing/new.txt", "Error:not found", writtenTree),
        ("READ /docs/./notes.txt", "Hello OS", writtenTree),
        ("READ /docs//notes.txt", "Hello OS", writtenTree),
        ("RMDIR /", "Error:root", writtenTree),
        ("DELETE /docs/notes.txt", None, docsTree),
        ("RMDIR /docs", None, emptyTree),
        ("LS /", [], emptyTree),
    ]
    fs = FileManager()
    successful = 0
    errors = 0

    # Run all 22 commands, continuing after deliberate error cases.
    for command, expectedResult, expectedTree in commands:
        result = fs.execute_command(command, process_id="P1", user="demo")

        if isinstance(expectedResult, str) and expectedResult.startswith("Error:"):
            assert isinstance(result, str)
            assert result.startswith("Error:"), "Expected a rejected operation"
            assert expectedResult[6:] in result.lower(), "Wrong error message"
            errors += 1
        elif command.startswith("STAT"):
            assert result.size == expectedResult, "Metadata size is incorrect"
            assert result.owner == "demo"
            successful += 1
        else:
            assert result == expectedResult, "Command response is incorrect"
            successful += 1

        # Check content and hierarchy after every command, not just at the end.
        assert collect_tree(fs._root) == expectedTree, "File tree is incorrect"

    assert len(commands) == 22
    assert successful == 15
    assert errors == 7
    assert fs.list_directory("/") == []


@pytest.mark.xfail(strict=True, reason="M3-F01: mkfile command is missing")
def test_required_mkfile_command():
    # This is a requirement check, not a passing implementation claim.
    fs = FileManager()
    result = fs.execute_command("mkfile /new.txt")
    assert result is None
    assert fs.read_file("/new.txt") == ""


@pytest.mark.xfail(strict=True, reason="M3-F01: cat command is missing")
def test_required_cat_command():
    fs = FileManager()
    fs.create_file("/notes.txt", "Hello")
    result = fs.execute_command("cat /notes.txt")
    assert result == "Hello"


@pytest.mark.xfail(strict=True, reason="M3-F01: rm command is missing")
def test_required_rm_command():
    fs = FileManager()
    fs.create_file("/notes.txt", "Hello")
    result = fs.execute_command("rm /notes.txt")
    assert result is None
    assert fs.list_directory("/") == []


def test_required_append_command():
    fs = FileManager()
    fs.create_file("/notes.txt", "Hello")
    result = fs.execute_command('append /notes.txt " OS"')
    assert not (isinstance(result, str) and result.startswith("Error:"))
    assert fs.read_file("/notes.txt") == "Hello OS"


def collect_tree(node, path="/"):
    # Build a plain Python mapping for comparison with the expected tree.
    result = {path: None if node.is_directory else node.content}
    for name, child in node.children.items():
        result.update(collect_tree(child, path.rstrip("/") + "/" + name))
    return result


def snapshot_tree(node):
    # Reading metadata through get_metadata changes its access timestamp.
    # Inspect existing values directly so the test does not change them.
    metadata = node.metadata
    return (
        node.name, node.is_directory, node.content,
        metadata.created_at, metadata.modified_at, metadata.accessed_at,
        metadata.owner, metadata.permissions, metadata.size,
        {name: snapshot_tree(child) for name, child in node.children.items()},
    )


def test_broken_authorizer_does_not_allow_a_write():
    class BrokenPolicy:
        def authorize(self, user, action, path):
            raise RuntimeError("Policy unavailable")

    fs = FileManager()
    fs.create_file("/notes.txt", "Original")
    before = snapshot_tree(fs._root)
    fs.authorizer = BrokenPolicy()

    # If the policy fails, the write should not slip through.
    with pytest.raises(AuthorizationError):
        fs.write_file("/notes.txt", "Denied", user="guest")
    assert snapshot_tree(fs._root) == before


def test_file_size_counts_encoded_bytes():
    fs = FileManager()
    fs.create_file("/notes.txt", "é")

    # This is one character but two UTF-8 bytes.
    assert fs.get_metadata("/notes.txt").size == len("é".encode("utf-8"))


def test_other_denied_file_actions_leave_state_alone():
    # Read, write and delete have existing tests. Check the other actions too.
    for action in ["create", "mkdir", "rmdir", "list", "metadata"]:
        fs = FileManager()
        fs.make_directory("/docs")
        fs.make_directory("/empty")
        fs.create_file("/docs/notes.txt", "Original")
        before = snapshot_tree(fs._root)
        fs.authorizer = DenyAction(action)

        with pytest.raises(AuthorizationError):
            if action == "create":
                fs.create_file("/docs/new.txt", user="guest")
            elif action == "mkdir":
                fs.make_directory("/docs/new", user="guest")
            elif action == "rmdir":
                fs.remove_directory("/empty", user="guest")
            elif action == "list":
                fs.list_directory("/docs", user="guest")
            else:
                fs.get_metadata("/docs/notes.txt", user="guest")
        assert snapshot_tree(fs._root) == before


def test_relative_path_uses_the_real_path_for_authorization():
    class RecordingPolicy:
        def __init__(self):
            self.calls = []

        def authorize(self, user, action, path):
            self.calls.append((user, action, path))
            return False

    fs = FileManager()
    fs.make_directory("/docs")
    fs.create_file("/docs/notes.txt", "Original")
    fs.change_directory("/docs", process_id="P1")
    policy = RecordingPolicy()
    fs.authorizer = policy
    before = snapshot_tree(fs._root)

    with pytest.raises(AuthorizationError):
        fs.write_file("./notes.txt", "Denied", user="guest", process_id="P1")
    assert policy.calls == [("guest", "write", "/docs/notes.txt")]
    assert snapshot_tree(fs._root) == before


def test_bad_write_content_keeps_the_original_file():
    fs = FileManager()
    fs.create_file("/notes.txt", "Original")
    before = snapshot_tree(fs._root)

    # A bad payload should not erase the content before raising its error.
    with pytest.raises(TypeError):
        fs.write_file("/notes.txt", 12)
    assert snapshot_tree(fs._root) == before


def test_storage_used_counts_utf8_bytes_across_files():
    fs = FileManager()
    fs.create_file("/one.txt", "é")
    fs.make_directory("/docs")
    fs.create_file("/docs/two.txt", "🙂")
    assert fs.storage_used() == len("é🙂".encode("utf-8"))
    assert fs.execute_command("STORAGE") == fs.storage_used()


def test_permissions_mask_is_enforced():
    fs = FileManager()
    fs.create_file("/private.txt", "secret")
    fs._resolve("/private.txt").metadata.permissions = "r--"
    assert fs.read_file("/private.txt") == "secret"
    with pytest.raises(AuthorizationError):
        fs.write_file("/private.txt", "changed")


def test_open_close_modes_and_invalid_handles():
    fs = FileManager()
    fs.create_file("/data.txt", "hello")
    read_handle = fs.open_file("/data.txt", "r")
    assert fs.read_open_file(read_handle) == "hello"
    with pytest.raises(AuthorizationError):
        fs.write_open_file(read_handle, "!")
    fs.close_file(read_handle)
    with pytest.raises(FileSystemError):
        fs.read_open_file(read_handle)

    append_handle = fs.open_file("/data.txt", "a")
    fs.write_open_file(append_handle, " world")
    fs.close_file(append_handle)
    assert fs.read_file("/data.txt") == "hello world"

    truncate_handle = fs.open_file("/data.txt", "w")
    fs.write_open_file(truncate_handle, "fresh")
    fs.close_file(truncate_handle)
    assert fs.read_file("/data.txt") == "fresh"


def test_open_rejects_invalid_modes_and_missing_permissions():
    fs = FileManager()
    fs.create_file("/locked.txt", "secret")
    fs._resolve("/locked.txt").metadata.permissions = "r--"
    with pytest.raises(ValueError):
        fs.open_file("/locked.txt", "x")
    with pytest.raises(AuthorizationError):
        fs.open_file("/locked.txt", "w")


def test_chmod_command_updates_and_enforces_permissions():
    fs = FileManager()
    fs.create_file("/private.txt", "secret")
    assert fs.execute_command("CHMOD /private.txt r--") is None
    assert fs.get_metadata("/private.txt").permissions == "r--"
    with pytest.raises(AuthorizationError):
        fs.write_file("/private.txt", "changed")


def test_open_close_command_interface():
    fs = FileManager()
    fs.create_file("/handle.txt", "hello")
    handle = fs.execute_command("OPEN /handle.txt r")
    assert isinstance(handle, int)
    assert fs.execute_command(f"HANDLE_READ {handle}") == "hello"
    assert fs.execute_command(f"CLOSE {handle}") is None
    assert fs.execute_command(f"CLOSE {handle}").startswith("Error:")
