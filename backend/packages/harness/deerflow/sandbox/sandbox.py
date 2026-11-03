from abc import ABC, abstractmethod
from collections.abc import Mapping

from deerflow.sandbox.search import GrepMatch


class Sandbox(ABC):
    """Abstract base class for sandbox environments"""

    _id: str

    def __init__(self, id: str):
        self._id = id
        # Session-level env injected into every ``execute_command`` call.
        # Populated via :meth:`set_session_env` (e.g. by the Media Gateway
        # credential middleware); empty by default so behaviour is unchanged.
        self._session_env: dict[str, str] = {}

    @property
    def id(self) -> str:
        return self._id

    def set_session_env(self, env: Mapping[str, str]) -> None:
        """Set session-level environment variables for this sandbox.

        Every subsequent :meth:`execute_command` runs with these variables
        layered over the inherited host/container environment (session values
        win). Used to inject per-user Media Gateway credentials so skills and
        scripts can read them from ``os.environ`` without handling secrets
        themselves.

        Base-class storage works for every provider; providers that do not
        execute commands in-process (e.g. the AIO/docker provider) honour it
        through their own ``execute_command`` env plumbing — see the
        ``LocalSandbox`` implementation for the reference wiring and the
        TODO in the AIO provider for the follow-up integration point.
        """
        self._session_env = dict(env)

    @property
    def session_env(self) -> dict[str, str]:
        """The session-level environment override (may be empty)."""
        return dict(self._session_env)

    @abstractmethod
    def execute_command(self, command: str) -> str:
        """Execute bash command in sandbox.

        Args:
            command: The command to execute.

        Returns:
            The standard or error output of the command.
        """
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read the content of a file.

        Args:
            path: The absolute path of the file to read.

        Returns:
            The content of the file.
        """
        pass

    @abstractmethod
    def download_file(self, path: str) -> bytes:
        """Download the binary content of a file.

        Args:
            path: The absolute path of the file to download.

        Returns:
            Raw file bytes.

        Raises:
            PermissionError: If path traversal is detected or the path is outside
                the allowed virtual prefix.
            OSError: If the file cannot be read or does not exist.  Both local
                and remote implementations must raise ``OSError`` so callers
                have a single exception type to handle.
        """
        pass

    @abstractmethod
    def list_dir(self, path: str, max_depth=2) -> list[str]:
        """List the contents of a directory.

        Args:
            path: The absolute path of the directory to list.
            max_depth: The maximum depth to traverse. Default is 2.

        Returns:
            The contents of the directory.
        """
        pass

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write content to a file.

        Args:
            path: The absolute path of the file to write to.
            content: The text content to write to the file.
            append: Whether to append the content to the file. If False, the file will be created or overwritten.
        """
        pass

    @abstractmethod
    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        """Find paths that match a glob pattern under a root directory."""
        pass

    @abstractmethod
    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        """Search for matches inside text files under a directory."""
        pass

    @abstractmethod
    def update_file(self, path: str, content: bytes) -> None:
        """Update a file with binary content.

        Args:
            path: The absolute path of the file to update.
            content: The binary content to write to the file.
        """
        pass
