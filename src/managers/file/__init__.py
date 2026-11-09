"""Hierarchical in-memory file system for the operating-system simulator.

The file system is intentionally in-memory: its contents are reset when a new
FileManager instance is created. Authorization is delegated to an injected
object implementing ``authorize(user, action, path) -> bool``.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class FileSystemError(Exception):
    """Base class for file-system operation errors."""


class FileNotFoundError(FileSystemError):
    """Raised when a requested path does not exist."""


class FileAlreadyExistsError(FileSystemError):
    """Raised when creating an entry at an occupied path."""


class NotADirectoryError(FileSystemError):
    """Raised when a path component is not a directory."""


class IsADirectoryError(FileSystemError):
    """Raised when a file operation targets a directory."""


class DirectoryNotEmptyError(FileSystemError):
    """Raised when attempting to remove a non-empty directory."""


class AuthorizationError(FileSystemError):
    """Raised when the authorization provider rejects an operation."""


class Authorizer(Protocol):
    """Authorization interface expected by FileManager."""

    def authorize(self, user: object, action: str, path: str) -> bool:
        """Return True when user may perform action on path."""


class AllowAllAuthorizer:
    """Default policy for standalone simulator use; permits every request."""

    def authorize(self, user: object, action: str, path: str) -> bool:
        return True


@dataclass
class FileMetadata:
    """Metadata maintained for every file and directory."""

    created_at: datetime
    modified_at: datetime
    accessed_at: datetime
    owner: str = "system"
    size: int = 0
    permissions: str = "rwx"


@dataclass
class _Node:
    name: str
    is_directory: bool
    parent: _Node | None
    metadata: FileMetadata
    content: str = ""
    children: dict[str, _Node] = field(default_factory=dict)


class FileManager:
    """Manage a hierarchical in-memory file system."""

    def __init__(self, authorizer: Authorizer | None = None) -> None:
        self.authorizer = authorizer if authorizer is not None else AllowAllAuthorizer()
        now = datetime.now(timezone.utc)
        self._root = _Node(
            name="", is_directory=True, parent=None,
            metadata=FileMetadata(now, now, now),
        )
        # Each process has its own current working directory.
        # None is the default context for backward compatibility.
        self._cwd_by_process = {}
        self._open_handles = {}
        self._next_handle = 1

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
    def _get_cwd(self, process_id: str | None = None) -> _Node:
        """Return the working directory for a process, defaulting to root."""
        if process_id not in self._cwd_by_process:
            self._cwd_by_process[process_id] = self._root
        return self._cwd_by_process[process_id]
    @staticmethod
    def _validate_path(path: str) -> None:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        if "\x00" in path:
            raise ValueError("path cannot contain a null character")

    def _authorize(self, user: object, action: str, path: str) -> None:
        try:
            allowed = self.authorizer.authorize(user, action, path)
        except AuthorizationError:
            self._last_command_failed = True
            raise
        except Exception as exc:
            raise AuthorizationError(
                f"Authorization check failed for {action} {path!r}"
            ) from exc
        if not allowed:
            raise AuthorizationError(
                f"User is not authorized to {action} {path!r}"
            )

    def _resolve(
        self, path: str, process_id: str | None = None
    ) -> _Node:
        self._validate_path(path)
        node = self._root if path.startswith("/") else self._get_cwd(process_id)

        parts = [part for part in path.split("/") if part not in ("", ".")]

        for part in parts:
            if not node.is_directory:
                raise NotADirectoryError(
                    f"'{self._display_path(node)}' is not a directory"
                )

            if part == "..":
                if node.parent is not None:
                    node = node.parent
                continue

            if part not in node.children:
                raise FileNotFoundError(f"Path not found: {path}")

            node = node.children[part]

        return node

    def _resolve_parent(
        self, path: str, process_id: str | None = None
    ) -> tuple[_Node, str]:
        self._validate_path(path)
        trimmed = path.rstrip("/")

        if not trimmed:
            raise ValueError("The root directory cannot be created or removed")

        parent_path, _, name = trimmed.rpartition("/")

        if not name or name in (".", ".."):
            raise ValueError("path must identify a named entry")

        parent = self._resolve(
            parent_path or ("/" if path.startswith("/") else "."),
            process_id,
        )

        if not parent.is_directory:
            raise NotADirectoryError(f"Parent of {path!r} is not a directory")

        return parent, name

    def _display_path(self, node: _Node) -> str:
        if node is self._root:
            return "/"
        names: list[str] = []
        current = node
        while current is not self._root:
            names.append(current.name)
            assert current.parent is not None
            current = current.parent
        return "/" + "/".join(reversed(names))

    @staticmethod
    def _check_permissions(node: _Node, action: str) -> None:
        required = {"read": "r", "list": "r", "metadata": "r", "cd": "x",
                    "write": "w", "delete": "w", "rmdir": "w"}.get(action)
        if required and required not in node.metadata.permissions:
            raise AuthorizationError(f"Permission denied: {required} permission required")

    def set_permissions(self, path: str, permissions: str, *, user: object = None,
                        process_id: str | None = None) -> None:
        """Set a three-character permission mask such as rw-, r--, or rwx."""
        if (not isinstance(permissions, str) or len(permissions) != 3
                or any(char not in "rwx-" for char in permissions)):
            raise ValueError("permissions must be a three-character rwx mask")
        node = self._resolve(path, process_id)
        self._authorize(user, "chmod", self._display_path(node))
        if user is not None and str(user) != node.metadata.owner:
            raise AuthorizationError("Only the file owner may change permissions")
        node.metadata.permissions = permissions
        node.metadata.modified_at = self._now()

    def storage_used(self) -> int:
        """Return aggregate UTF-8 bytes occupied by file contents."""
        def measure(node: _Node) -> int:
            if not node.is_directory:
                return len(node.content.encode("utf-8"))
            return sum(measure(child) for child in node.children.values())
        return measure(self._root)

    def open_file(self, path: str, mode: str = "r", *, user: object = None,
                  process_id: str | None = None) -> int:
        """Open a file and return a handle. Modes: r, w, a, r+, w+, a+."""
        if mode not in {"r", "w", "a", "r+", "w+", "a+"}:
            raise ValueError("mode must be r, w, a, r+, w+, or a+")
        node = self._resolve(path, process_id)
        if node.is_directory:
            raise IsADirectoryError(f"Cannot open directory: {path}")
        self._authorize(user, "open", self._display_path(node))
        required = set()
        if mode == "r" or "+" in mode:
            required.add("r")
        if mode != "r":
            required.add("w")
        if not required.issubset(set(node.metadata.permissions)):
            raise AuthorizationError(f"Insufficient permissions for mode {mode!r}")
        if mode.startswith("w"):
            node.content = ""
            node.metadata.size = 0
            node.metadata.modified_at = self._now()
        handle = self._next_handle
        self._next_handle += 1
        self._open_handles[handle] = {
            "node": node, "mode": mode,
            "position": len(node.content) if mode == "a" else 0,
        }
        node.metadata.accessed_at = self._now()
        return handle

    def close_file(self, handle: int) -> None:
        """Close an open file handle; closing twice is an error."""
        if isinstance(handle, bool) or handle not in self._open_handles:
            raise FileSystemError(f"Invalid or already closed file handle: {handle}")
        del self._open_handles[handle]

    def read_open_file(self, handle: int) -> str:
        state = self._open_handles.get(handle)
        if state is None:
            raise FileSystemError(f"Invalid or closed file handle: {handle}")
        if state["mode"] == "w" or state["mode"] == "a":
            raise AuthorizationError("File handle is not open for reading")
        node = state["node"]
        result = node.content[state["position"]:]
        state["position"] = len(node.content)
        node.metadata.accessed_at = self._now()
        return result

    def write_open_file(self, handle: int, content: str) -> None:
        state = self._open_handles.get(handle)
        if state is None:
            raise FileSystemError(f"Invalid or closed file handle: {handle}")
        if state["mode"] == "r":
            raise AuthorizationError("File handle is not open for writing")
        if not isinstance(content, str):
            raise TypeError("file content must be a string")
        node = state["node"]
        if state["mode"].startswith("a"):
            node.content += content
            state["position"] = len(node.content)
        else:
            pos = state["position"]
            node.content = node.content[:pos] + content + node.content[pos + len(content):]
            state["position"] = pos + len(content)
        now = self._now()
        node.metadata.modified_at = now
        node.metadata.accessed_at = now
        node.metadata.size = len(node.content.encode("utf-8"))

    def create_file(self, path: str, content: str = "", *,
                    user: object = None, owner: str = "system", process_id: str | None = None) -> None:
        """Create an empty file, or a file initialized with content."""
        parent, name = self._resolve_parent(path, process_id)
        canonical = self._display_path(parent).rstrip("/") + "/" + name
        self._authorize(user, "create", canonical)
        self._check_permissions(parent, "create")
        if name in parent.children:
            raise FileAlreadyExistsError(f"Entry already exists: {path}")
        if not isinstance(content, str):
            raise TypeError("file content must be a string")
        now = self._now()
        parent.children[name] = _Node(
            name, False, parent,
            FileMetadata(now, now, now, owner=owner, size=len(content.encode("utf-8"))),
            content=content,
        )
        parent.metadata.modified_at = now

    def write_file(self, path: str, content: str, *,
                   user: object = None, append: bool = False, process_id: str | None = None) -> None:
        """Replace file contents, or append when append=True."""
        node = self._resolve(path, process_id)
        self._authorize(user, "write", self._display_path(node))
        self._check_permissions(node, "write")
        if node.is_directory:
            raise IsADirectoryError(f"Cannot write to directory: {path}")
        if not isinstance(content, str):
            raise TypeError("file content must be a string")
        node.content = node.content + content if append else content
        now = self._now()
        node.metadata.modified_at = now
        node.metadata.accessed_at = now
        node.metadata.size = len(node.content.encode("utf-8"))

    def read_file(self, path: str, *, user: object = None, process_id: str | None = None) -> str:
        """Return the contents of a file."""
        node = self._resolve(path, process_id)
        self._authorize(user, "read", self._display_path(node))
        self._check_permissions(node, "read")
        if node.is_directory:
            raise IsADirectoryError(f"Cannot read directory: {path}")
        node.metadata.accessed_at = self._now()
        return node.content

    def delete_file(self, path: str, *, user: object = None, process_id: str | None = None) -> None:
        """Delete a file; directories must be removed with remove_directory."""
        node = self._resolve(path, process_id)
        self._authorize(user, "delete", self._display_path(node))
        self._check_permissions(node, "delete")
        if node.is_directory:
            raise IsADirectoryError(f"Cannot delete directory as a file: {path}")
        assert node.parent is not None
        del node.parent.children[node.name]
        node.parent.metadata.modified_at = self._now()

    def make_directory(self, path: str, *, user: object = None, process_id: str | None = None,
                       owner: str = "system") -> None:
        """Create one directory (parent directories must already exist)."""
        parent, name = self._resolve_parent(path, process_id)
        canonical = self._display_path(parent).rstrip("/") + "/" + name
        self._authorize(user, "mkdir", canonical)
        self._check_permissions(parent, "mkdir")
        if name in parent.children:
            raise FileAlreadyExistsError(f"Entry already exists: {path}")
        now = self._now()
        parent.children[name] = _Node(
            name, True, parent,
            FileMetadata(now, now, now, owner=owner),
        )
        parent.metadata.modified_at = now

    def remove_directory(self, path: str, *, user: object = None, process_id: str | None = None) -> None:
        """Remove an empty directory; root and current directory are protected."""
        node = self._resolve(path, process_id)
        self._authorize(user, "rmdir", self._display_path(node))
        self._check_permissions(node, "rmdir")
        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")
        
        if node is self._root:
            raise FileSystemError("Cannot remove the root directory")
        
        for cwd in self._cwd_by_process.values():
            current = cwd
            while current is not None:
                if current is node:
                    raise FileSystemError(
                        "Cannot remove a directory used by a process "
                        "or an ancestor of that directory"
                    )
                current = current.parent
        if node.children:
            raise DirectoryNotEmptyError(f"Directory is not empty: {path}")
        assert node.parent is not None
        del node.parent.children[node.name]
        node.parent.metadata.modified_at = self._now()

    def list_directory(self, path: str = ".", *, user: object = None, process_id: str | None = None) -> list[str]:
        """Return sorted names in a directory."""
        node = self._resolve(path, process_id)
        self._authorize(user, "list", self._display_path(node))
        self._check_permissions(node, "list")
        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")
        node.metadata.accessed_at = self._now()
        return sorted(node.children)

    def change_directory(
        self, path: str, *, user: object = None,
        process_id: str | None = None
    ) -> str:
        """Change one process's working directory."""
        node = self._resolve(path, process_id)
        self._authorize(user, "cd", self._display_path(node))
        self._check_permissions(node, "cd")

        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")

        self._cwd_by_process[process_id] = node
        node.metadata.accessed_at = self._now()

        return self.present_working_directory(process_id=process_id)


    def present_working_directory(
        self, *, process_id: str | None = None
    ) -> str:
        """Return the absolute working directory for a process."""
        return self._display_path(self._get_cwd(process_id))

    def get_metadata(self, path: str, *, user: object = None, process_id: str | None = None) -> FileMetadata:
        """Return a copy of a file or directory's metadata."""
        node = self._resolve(path, process_id)
        self._authorize(user, "metadata", self._display_path(node))
        self._check_permissions(node, "metadata")
        node.metadata.accessed_at = self._now()
        return FileMetadata(
            node.metadata.created_at, node.metadata.modified_at,
            node.metadata.accessed_at, node.metadata.owner,
            node.metadata.size, node.metadata.permissions,
        )

    
    def execute_command(self, command: str, *, user: object = None, process_id: str | None = None):
        """Execute file-system commands and return user-friendly error messages."""
        self._last_command_failed = False
        try:
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Command must be a non-empty string.")

            parts = shlex.split(command)

            if not parts:
                raise ValueError("Command must be a non-empty string.")

            operation = parts[0].upper()
            args = parts[1:]

            if operation in {"WRITE", "APPEND"}:
                if len(args) < 2:
                    raise ValueError(f"{operation} requires a path and content.")
                self.write_file(args[0], " ".join(args[1:]), user=user,
                                append=(operation == "APPEND"), process_id=process_id)
                return ""

            if operation == "STORAGE" and not args:
                return self.storage_used()

            if operation == "CHMOD" and len(args) == 2:
                self.set_permissions(args[0], args[1], user=user, process_id=process_id)
                return None

            if operation == "OPEN" and 1 <= len(args) <= 2:
                return self.open_file(args[0], args[1] if len(args) == 2 else "r",
                                      user=user, process_id=process_id)

            if operation == "CLOSE" and len(args) == 1:
                return self.close_file(int(args[0]))

            if operation == "HANDLE_READ" and len(args) == 1:
                return self.read_open_file(int(args[0]))

            if operation == "HANDLE_WRITE" and len(args) >= 2:
                self.write_open_file(int(args[0]), " ".join(args[1:]))
                return ""

            if operation == "CREATE" and len(args) == 1:
                return self.create_file(args[0], user=user, owner=str(user) if user is not None else "system", process_id=process_id)

            if operation == "READ" and len(args) == 1:
                return self.read_file(args[0], user=user, process_id=process_id)

            if operation == "DELETE" and len(args) == 1:
                return self.delete_file(args[0], user=user, process_id=process_id)

            if operation == "MKDIR" and len(args) == 1:
                return self.make_directory(args[0], user=user, owner=str(user) if user is not None else "system", process_id=process_id)

            if operation == "RMDIR" and len(args) == 1:
                return self.remove_directory(args[0], user=user, process_id=process_id)

            if operation == "LS" and len(args) <= 1:
                return self.list_directory(
                    args[0] if args else ".", user=user, process_id=process_id
                )

            if operation == "CD" and len(args) == 1:
                return self.change_directory(args[0], user=user, process_id=process_id)

            if operation == "PWD" and not args:
                return self.present_working_directory(process_id=process_id)

            if operation == "STAT" and len(args) == 1:
                return self.get_metadata(args[0], user=user, process_id=process_id)

            raise ValueError(
                f"Invalid command or arguments: {command}"
            )

        except FileNotFoundError as error:
            self._last_command_failed = True
            return f"Error: File or directory not found: {error}"

        except FileAlreadyExistsError as error:
            self._last_command_failed = True
            return f"Error: File or directory already exists: {error}"

        except NotADirectoryError as error:
            self._last_command_failed = True
            return f"Error: Not a directory: {error}"

        except IsADirectoryError as error:
            self._last_command_failed = True
            return f"Error: Is a directory: {error}"

        except DirectoryNotEmptyError as error:
            self._last_command_failed = True
            return f"Error: Directory is not empty: {error}"

        except AuthorizationError:
            self._last_command_failed = True
            return "Error: You are not authorized to perform this operation."

        except FileSystemError as error:
            self._last_command_failed = True
            return f"Error: File system operation failed: {error}"

        except ValueError as error:
            self._last_command_failed = True
            return f"Error: Invalid command: {error}"



__all__ = [
    "AllowAllAuthorizer", "Authorizer", "AuthorizationError",
    "DirectoryNotEmptyError", "FileAlreadyExistsError", "FileManager",
    "FileMetadata", "FileNotFoundError", "FileSystemError",
    "IsADirectoryError", "NotADirectoryError",
]
