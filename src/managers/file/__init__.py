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
        self._cwd = self._root

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

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
            raise
        except Exception as exc:
            raise AuthorizationError(
                f"Authorization check failed for {action} {path!r}"
            ) from exc
        if not allowed:
            raise AuthorizationError(
                f"User is not authorized to {action} {path!r}"
            )

    def _resolve(self, path: str) -> _Node:
        self._validate_path(path)
        node = self._root if path.startswith("/") else self._cwd
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

    def _resolve_parent(self, path: str) -> tuple[_Node, str]:
        self._validate_path(path)
        trimmed = path.rstrip("/")
        if not trimmed:
            raise ValueError("The root directory cannot be created or removed")
        parent_path, _, name = trimmed.rpartition("/")
        if not name or name in (".", ".."):
            raise ValueError("path must identify a named entry")
        parent = self._resolve(parent_path or ("/" if path.startswith("/") else "."))
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

    def create_file(self, path: str, content: str = "", *,
                    user: object = None, owner: str = "system") -> None:
        """Create an empty file, or a file initialized with content."""
        parent, name = self._resolve_parent(path)
        canonical = self._display_path(parent).rstrip("/") + "/" + name
        self._authorize(user, "create", canonical)
        if name in parent.children:
            raise FileAlreadyExistsError(f"Entry already exists: {path}")
        if not isinstance(content, str):
            raise TypeError("file content must be a string")
        now = self._now()
        parent.children[name] = _Node(
            name, False, parent,
            FileMetadata(now, now, now, owner=owner, size=len(content)),
            content=content,
        )
        parent.metadata.modified_at = now

    def write_file(self, path: str, content: str, *,
                   user: object = None, append: bool = False) -> None:
        """Replace file contents, or append when append=True."""
        node = self._resolve(path)
        self._authorize(user, "write", self._display_path(node))
        if node.is_directory:
            raise IsADirectoryError(f"Cannot write to directory: {path}")
        if not isinstance(content, str):
            raise TypeError("file content must be a string")
        node.content = node.content + content if append else content
        now = self._now()
        node.metadata.modified_at = now
        node.metadata.accessed_at = now
        node.metadata.size = len(node.content)

    def read_file(self, path: str, *, user: object = None) -> str:
        """Return the contents of a file."""
        node = self._resolve(path)
        self._authorize(user, "read", self._display_path(node))
        if node.is_directory:
            raise IsADirectoryError(f"Cannot read directory: {path}")
        node.metadata.accessed_at = self._now()
        return node.content

    def delete_file(self, path: str, *, user: object = None) -> None:
        """Delete a file; directories must be removed with remove_directory."""
        node = self._resolve(path)
        self._authorize(user, "delete", self._display_path(node))
        if node.is_directory:
            raise IsADirectoryError(f"Cannot delete directory as a file: {path}")
        assert node.parent is not None
        del node.parent.children[node.name]
        node.parent.metadata.modified_at = self._now()

    def make_directory(self, path: str, *, user: object = None,
                       owner: str = "system") -> None:
        """Create one directory (parent directories must already exist)."""
        parent, name = self._resolve_parent(path)
        canonical = self._display_path(parent).rstrip("/") + "/" + name
        self._authorize(user, "mkdir", canonical)
        if name in parent.children:
            raise FileAlreadyExistsError(f"Entry already exists: {path}")
        now = self._now()
        parent.children[name] = _Node(
            name, True, parent,
            FileMetadata(now, now, now, owner=owner),
        )
        parent.metadata.modified_at = now

    def remove_directory(self, path: str, *, user: object = None) -> None:
        """Remove an empty directory; root and current directory are protected."""
        node = self._resolve(path)
        self._authorize(user, "rmdir", self._display_path(node))
        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")
        current = self._cwd
        while current is not None:
            if current is node:
                raise FileSystemError(
                    "Cannot remove the root, current directory, or its ancestor"
                )
            current = current.parent
        if node.children:
            raise DirectoryNotEmptyError(f"Directory is not empty: {path}")
        assert node.parent is not None
        del node.parent.children[node.name]
        node.parent.metadata.modified_at = self._now()

    def list_directory(self, path: str = ".", *, user: object = None) -> list[str]:
        """Return sorted names in a directory."""
        node = self._resolve(path)
        self._authorize(user, "list", self._display_path(node))
        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")
        node.metadata.accessed_at = self._now()
        return sorted(node.children)

    def change_directory(self, path: str, *, user: object = None) -> str:
        """Change the working directory and return its absolute path."""
        node = self._resolve(path)
        self._authorize(user, "cd", self._display_path(node))
        if not node.is_directory:
            raise NotADirectoryError(f"Not a directory: {path}")
        self._cwd = node
        node.metadata.accessed_at = self._now()
        return self.present_working_directory()

    def present_working_directory(self) -> str:
        """Return the absolute path of the current working directory."""
        return self._display_path(self._cwd)

    def get_metadata(self, path: str, *, user: object = None) -> FileMetadata:
        """Return a copy of a file or directory's metadata."""
        node = self._resolve(path)
        self._authorize(user, "metadata", self._display_path(node))
        node.metadata.accessed_at = self._now()
        return FileMetadata(
            node.metadata.created_at, node.metadata.modified_at,
            node.metadata.accessed_at, node.metadata.owner,
            node.metadata.size, node.metadata.permissions,
        )

    
    def execute_command(self, command: str, *, user: object = None):
        """Execute file-system commands and return user-friendly error messages."""
        try:
            if not isinstance(command, str) or not command.strip():
                raise ValueError("Command must be a non-empty string.")

            parts = shlex.split(command)

            if not parts:
                raise ValueError("Command must be a non-empty string.")

            operation = parts[0].upper()
            args = parts[1:]

            if operation == "WRITE":
                if len(args) < 2:
                    raise ValueError("WRITE requires a path and content.")

                path = args[0]
                content = " ".join(args[1:])

                self.write_file(path, content, user=user)
                return ""

            if operation == "CREATE" and len(args) == 1:
                return self.create_file(args[0], user=user)

            if operation == "READ" and len(args) == 1:
                return self.read_file(args[0], user=user)

            if operation == "DELETE" and len(args) == 1:
                return self.delete_file(args[0], user=user)

            if operation == "MKDIR" and len(args) == 1:
                return self.make_directory(args[0], user=user)

            if operation == "RMDIR" and len(args) == 1:
                return self.remove_directory(args[0], user=user)

            if operation == "LS" and len(args) <= 1:
                return self.list_directory(
                    args[0] if args else ".", user=user
                )

            if operation == "CD" and len(args) == 1:
                return self.change_directory(args[0], user=user)

            if operation == "PWD" and not args:
                return self.present_working_directory()

            if operation == "STAT" and len(args) == 1:
                return self.get_metadata(args[0], user=user)

            raise ValueError(
                f"Invalid command or arguments: {command}"
            )

        except FileNotFoundError as error:
            return f"Error: File or directory not found: {error}"

        except FileAlreadyExistsError as error:
            return f"Error: File or directory already exists: {error}"

        except NotADirectoryError as error:
            return f"Error: Not a directory: {error}"

        except IsADirectoryError as error:
            return f"Error: Is a directory: {error}"

        except DirectoryNotEmptyError as error:
            return f"Error: Directory is not empty: {error}"

        except AuthorizationError:
            return "Error: You are not authorized to perform this operation."

        except FileSystemError as error:
            return f"Error: File system operation failed: {error}"

        except ValueError as error:
            return f"Error: Invalid command: {error}"



__all__ = [
    "AllowAllAuthorizer", "Authorizer", "AuthorizationError",
    "DirectoryNotEmptyError", "FileAlreadyExistsError", "FileManager",
    "FileMetadata", "FileNotFoundError", "FileSystemError",
    "IsADirectoryError", "NotADirectoryError",
]
