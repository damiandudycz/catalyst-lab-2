import os
import socket
import subprocess
import sys
import uuid
import time
import pwd
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
    _listen_socket(server=server, socket_path=socket_path, session_token=session_token)

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

def _listen_socket(server: socket, socket_path: str, session_token: str):
    """Listen for incoming client commands and execute them."""
    print(f"[root_helper] Listening socket {socket_path}")
    server.listen(1)

    try:
        while True:
            conn, _ = server.accept()
            data = conn.recv(4096).decode().strip()

            if not data.startswith(session_token + " "):
                conn.send(b"ERROR: Invalid token\n")
                conn.close()
                continue

            command = data[len(session_token) + 1:]
            if command == "exit":
                conn.send(b"Exiting.\n")
                conn.close()
                break

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

        env = os.environ.copy()
        env['ROOT_HELPER_TOKEN'] = self.token
        env['RUNTIME_DIR'] = _get_runtime_dir(os.getuid())
        helper_host_path = os.path.realpath(__file__)

        # Start the process to launch the server
        self._process = subprocess.Popen([
            "flatpak-spawn", "--host", "pkexec", "python3", helper_host_path
        ], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = self._process.communicate()
        if self._process.returncode != 0:
            raise RuntimeError(f"Error starting root helper: {stderr.decode() if stderr else 'No error message'}")
        print("Root helper process started.")

    def stop_root_helper(self):
        """Stop the root helper process."""
        try:
            self.send_command("exit")
        except Exception as e:
            print(f"Failed to stop root helper: {e}")
        finally:
            self._process = None
            print("Root helper process stopped.")

    def send_command(self, command):
        """Send a command to the root helper server."""
        if not self.is_server_running:
            print("Root helper is not running, attempting to start it.")
            self.start_root_helper()
        if not self.is_server_running:
            raise RuntimeError("Failed to start the root helper server.")
        # Logic for communicating with the root helper process
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(self.socket_path)
            s.sendall(f"{self.token} {command}".encode())
            response = s.recv(4096)

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
    return uid, runtime_dir

def _validate_started_as_root():
    """Ensure the script is run as root."""
    if os.geteuid() != 0:
        raise RuntimeError("This script must be run as root.")

def _get_caller_uid():
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

# ----------------------------------------
# Entry point
# ----------------------------------------

if __name__ == "__main__":
    run_server()

