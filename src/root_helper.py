import os
import socket
import subprocess
import sys
import uuid
import time
import pwd
import tempfile
import time
import struct
from gi.repository import Gio
from typing import Optional

# ----------------------------------------
# Server code
# ----------------------------------------

def run_server():
    """Run the root helper server."""
    session_token = os.environ.get("ROOT_HELPER_TOKEN")
    uid = _get_caller_uid()
    runtime_dir = _get_runtime_dir(uid)
    socket_path = _get_socket_path(uid)
    socket_dir = os.path.dirname(socket_path)

    _validate_started_as_root()
    _validate_session_token(session_token=session_token)
    _validate_runtime_dir_and_uid(runtime_dir=runtime_dir, uid=uid)

    _prepare_server_socket_directory(socket_dir=socket_dir, uid=uid)
    server = _setup_server_socket(socket_path=socket_path, uid=uid)
    _listen_socket(server=server, socket_path=socket_path, session_token=session_token, allowed_uid=uid)

def _prepare_server_socket_directory(socket_dir: str, uid: int):
    """Ensure the socket directory exists and has proper permissions."""
    os.makedirs(socket_dir, exist_ok=True)
    os.chown(socket_dir, uid, uid)
    os.chmod(socket_dir, 0o700)

def _setup_server_socket(socket_path: str, uid: int):
    """Set up the server socket for communication."""
    if os.path.exists(socket_path):
        os.remove(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chown(socket_path, uid, uid)
    os.chmod(socket_path, 0o600)
    return server

def _listen_socket(server: socket.socket, socket_path: str, session_token: str, allowed_uid: int):
    """Listen for incoming client commands and execute them."""
    print(f"[root_helper] Listening socket {socket_path}, allowed UID={allowed_uid}")
    server.listen(1)
    try:
        while True:
            conn, _ = server.accept()

            # Get peer credentials
            try:
                ucred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", ucred)
            except Exception as e:
                print(f"Failed to get client credentials: {e}")
                conn.send(b"ERROR: Unable to get client credentials\n")
                conn.close()
                continue
            if uid != allowed_uid:
                conn.send(b"ERROR: Unauthorized client UID\n")
                conn.close()
                continue

            data = conn.recv(4096).decode().strip()

            if not data.startswith(session_token + " "):
                conn.send(b"ERROR: Invalid token\n")
                conn.close()
                continue

            command = data[len(session_token) + 1:]
            match command:
                case "[EXIT]":
                    conn.send(b"Exiting.\n")
                    conn.close()
                    break
                case "[PING]":
                    conn.send(b"[OK]\n")
                    conn.close()
                    continue
            try:
                output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT)
                conn.send(output)
            except subprocess.CalledProcessError as e:
                conn.send(e.output)

            conn.close()
    finally:
        server.close()
        if os.path.exists(socket_path):
            os.remove(socket_path)

# ----------------------------------------
# Client code
# ----------------------------------------

class RootHelperClient:
    _instance = None  # Class-level variable to store the single instance

    def __init__(self):
        self.token = str(uuid.uuid4())
        self.socket_path = _get_socket_path(os.getuid())
        self._process = None

    @classmethod
    def shared(cls):
        """Return the shared instance of the RootHelperClient (singleton pattern)."""
        if cls._instance is None:
            cls._instance = cls()  # Create a new instance if one doesn't exist
        return cls._instance

    @property
    def is_server_running(self):
        """Check if the server process is running."""
        # Check if the process is running
        if self._process is None:
            return False
        return self._process.poll() is None

    def start_root_helper(self):
        """Start the root helper process."""
        if self.is_server_running:
            print("Root helper is already running.")
            return

        helper_host_path = _extract_root_helper_to_tmp()
        runtime_dir = _get_runtime_dir(os.getuid())
        socket_path = _get_socket_path(os.getuid())

        if os.path.exists(socket_path):
            os.remove(socket_path)

        self._process = subprocess.Popen([
            "flatpak-spawn", "--host", "pkexec", "env",
            f"ROOT_HELPER_TOKEN={self.token}",
            f"RUNTIME_DIR={runtime_dir}",
            "python3", helper_host_path
        ])

        # Wait for the server to send the "OK" message
        if not self._wait_for_server_ready():
            stderr = self._process.stderr.read() if self._process.stderr else None
            raise RuntimeError(f"Error starting root helper: {stderr.decode() if stderr else 'No error message'}")
        print("Root helper process started and is ready.")

    def _wait_for_server_ready(self, timeout: int = 60) -> bool:
        """Wait for the server to send an 'OK' message indicating it's ready."""
        import errno

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.send_command("[PING]", allow_auto_start=False)
                if response.strip() == "[OK]":
                    return True
            except (FileNotFoundError, ConnectionRefusedError, socket.error) as e:
                # If the socket isn't ready yet, retry after short sleep
                if isinstance(e, socket.error) and getattr(e, "errno", None) not in (errno.ECONNREFUSED, errno.ENOENT):
                    raise
            except Exception as e:
                print(f"Unexpected error while waiting for server: {e}")
                break
            time.sleep(1)  # Retry delay

        print("Server did not respond with '[OK]' in time.")
        self._process.kill()
        return False

    def stop_root_helper(self):
        """Stop the root helper process."""
        if not self.is_server_running:
            print(f"Server is already stopped")
            return # Already stopped
        try:
            self.send_command("[EXIT]")
        except Exception as e:
            print(f"Failed to stop root helper: {e}")
        finally:
            self._process = None
            print("Root helper process stopped.")

    def send_command(self, command, allow_auto_start=True) -> str:
        """Send a command to the root helper server."""
        if not self.is_server_running:
            if allow_auto_start:
                print("Root helper is not running, attempting to start it.")
                self.start_root_helper()
            else:
                raise RuntimeError("Root helper is not running.")

        if not self.is_server_running:
            raise RuntimeError("Failed to start the root helper server.")

        if not os.path.exists(self.socket_path):
            print("Waiting for server socket")
            return "[WAITING]"

        print(f"SEND {command} with token: {self.token}")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(self.socket_path)
                s.sendall(f"{self.token} {command}".encode())
                response = s.recv(4096)
            return response.decode()
        except FileNotFoundError as e:
            raise ConnectionRefusedError("Socket not available yet") from e

# ----------------------------------------
# Shared functions
# ----------------------------------------

def _validate_session_token(session_token: str):
    """Validate that the session token is a valid UUIDv4."""
    if not session_token:
        raise RuntimeError("Missing ROOT_HELPER_TOKEN env value.")
    try:
        token_obj = uuid.UUID(session_token, version=4)
        if str(token_obj) != session_token:
            raise RuntimeError("Token string doesn't match parsed UUID")
    except (ValueError, AttributeError):
        raise RuntimeError("Invalid ROOT_HELPER_TOKEN: must be a valid UUIDv4.")

def _validate_runtime_dir_and_uid(runtime_dir: str, uid: int):
    """Ensure the runtime directory exists and the UID is valid."""
    if uid is None:
        raise RuntimeError("Cannot determine calling user UID.")
    if runtime_dir is None:
        raise RuntimeError(f"Runtime directory not specified.")
    if not os.path.isdir(runtime_dir):
        raise RuntimeError(f"Runtime directory does not exist: {runtime_dir}")

def _validate_started_as_root():
    """Ensure the script is run as root."""
    if os.geteuid() != 0:
        raise RuntimeError("This script must be run as root.")

def _get_caller_uid() -> int:
    """Get the UID of the user calling the script, handling pkexec."""
    pkexec_uid = os.environ.get("PKEXEC_UID")
    if pkexec_uid:
        return int(pkexec_uid)
    raise RuntimeError("Could not determine UID. This script must be run using pkexec.")

def _get_runtime_dir(uid: int) -> str:
    return os.environ.get("RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"

def _get_socket_path(uid: int) -> str:
    """Get the path to the socket file for communication."""
    runtime_dir = _get_runtime_dir(uid)
    return os.path.join(runtime_dir, "catalystlab-root-helper", "socket")

def _extract_root_helper_to_tmp() -> str:
    resource_path = "/com/damiandudycz/CatalystLab/root_helper.py"
    output_path = os.path.join(tempfile.gettempdir(), "catalystlab-root-helper.py")
    if os.path.exists(output_path):
        os.remove(output_path)
    resource = Gio.Resource.load("/app/share/catalystlab/catalystlab.gresource")
    data = resource.lookup_data(resource_path, Gio.ResourceLookupFlags.NONE)
    with open(output_path, "wb") as f:
        f.write(data.get_data())
    os.chmod(output_path, 0o700)
    return output_path

# ----------------------------------------
# Entry point
# ----------------------------------------

if __name__ == "__main__":
    run_server()

