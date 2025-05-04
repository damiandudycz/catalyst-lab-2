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
import signal
from gi.repository import Gio
from typing import Optional

# ----------------------------------------
# Server code
# ----------------------------------------

class RootHelperServer:
    _instance = None  # Class-level variable to store the single instance
    _is_running = False  # Flag to indicate whether the server is running
    _server_socket = None  # Save the server socket reference
    _pid_lock = None # Locks future calls only from initial process id

    def __init__(self):
        self.uid = self._get_caller_uid()
        print("Please provide session token:")
        self.session_token = sys.stdin.readline().strip()
        print("Please provide runtime dir:")
        os.environ["CATALYSTLAB_SERVER_RUNTIME_DIR"] = sys.stdin.readline().strip()
        self.runtime_dir = _get_runtime_dir(self.uid, runtime_env_name="CATALYSTLAB_SERVER_RUNTIME_DIR")
        self.socket_path = _get_socket_path(self.uid, runtime_env_name="CATALYSTLAB_SERVER_RUNTIME_DIR")
        self.socket_dir = os.path.dirname(self.socket_path)
        self._validate_started_as_root()
        self._validate_session_token(self.session_token)
        self._validate_runtime_dir_and_uid(self.runtime_dir, self.uid)

    @classmethod
    def shared(cls):
        """Return the shared instance of the RootHelperServer (singleton pattern)."""
        if cls._instance is None:
            cls._instance = cls()  # Create a new instance if one doesn't exist
        return cls._instance

    def start(self):
        """Run the root helper server."""
        self._prepare_server_socket_directory(self.socket_dir, self.uid)
        self._server_socket = self._setup_server_socket(self.socket_path, self.uid)  # Save the server socket reference
        self._is_running = True  # Set the flag to indicate the server is running
        self._listen_socket(self._server_socket, self.socket_path, self.session_token, self.uid)

    def stop(self):
        """Stop the server, closing the socket and removing any resources."""
        self._is_running = False  # Set the flag to indicate the server should stop
        print("[root_helper] Stopping server...")

        if self._server_socket:
            self._server_socket.close()  # Close the server socket
            self._server_socket = None  # Clear the reference to the socket

        # Clean up the socket file
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        print("[root_helper] Server stopped.")

    def _prepare_server_socket_directory(self, socket_dir: str, uid: int):
        """Ensure the socket directory exists and has proper permissions."""
        os.makedirs(socket_dir, exist_ok=True)
        os.chown(socket_dir, uid, uid)
        os.chmod(socket_dir, 0o700)

    def _setup_server_socket(self, socket_path: str, uid: int):
        """Set up the server socket for communication."""
        if os.path.exists(socket_path):
            os.remove(socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        os.chown(socket_path, uid, uid)
        os.chmod(socket_path, 0o600)
        return server

    def _listen_socket(self, server: socket.socket, socket_path: str, session_token: str, allowed_uid: int):
        """Listen for incoming client commands and execute them."""
        print(f"[root_helper] Listening socket {socket_path}")
        server.listen(1)

        try:
            while self._is_running:
                conn, _ = server.accept()

                # Get peer credentials
                try:
                    ucred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    pid, uid, gid = struct.unpack("3i", ucred)
                except Exception as e:
                    print(f"Failed to get client credentials: {e}")
                    conn.sendall(b"ERROR: Unable to get client credentials\n")
                    conn.close()
                    continue
                if uid != allowed_uid:
                    conn.sendall(b"ERROR: Unauthorized client UID\n")
                    conn.close()
                    continue
                if self._pid_lock is not None and self._pid_lock != pid:
                    conn.sendall(b"ERROR: Unauthorized client PID\n")
                    conn.close()
                    continue

                data = conn.recv(4096).decode().strip()

                if not data.startswith(session_token + " "):
                    conn.sendall(b"ERROR: Invalid token\n")
                    conn.close()
                    continue

                command = data[len(session_token) + 1:]
                match command:
                    case "[EXIT]":
                        conn.sendall(b"Exiting.\n")
                        conn.close()
                        break
                    case "[INITIALIZE]":
                        # Used to ping the server to see when it is ready.
                        # Also locks future calls to this server for pid of this connection.
                        self._pid_lock = pid
                        conn.sendall(b"[OK]\n")
                        conn.close()
                        continue

                if self._pid_lock is None:
                    conn.sendall(b"ERROR: Connection initialization not finished.\n")
                    conn.close()
                    continue
                try:
                    output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT)
                    conn.sendall(output)
                except subprocess.CalledProcessError as e:
                    conn.sendall(e.output)

                conn.close()
        finally:
            # Ensure server is stopped and resources are cleaned up
            self.stop()  # Gracefully stop the server and remove the socket file

    def _validate_session_token(self, session_token: str):
        """Validate that the session token is a valid UUIDv4."""
        if not session_token:
            raise RuntimeError("Missing ROOT_HELPER_TOKEN env value.")
        try:
            token_obj = uuid.UUID(session_token, version=4)
            if str(token_obj) != session_token:
                raise RuntimeError("Token string doesn't match parsed UUID")
        except (ValueError, AttributeError):
            raise RuntimeError("Invalid ROOT_HELPER_TOKEN: must be a valid UUIDv4.")

    def _validate_runtime_dir_and_uid(self, runtime_dir: str, uid: int):
        """Ensure the runtime directory exists and the UID is valid."""
        if uid is None:
            raise RuntimeError("Cannot determine calling user UID.")
        if runtime_dir is None:
            raise RuntimeError(f"Runtime directory not specified.")
        if not os.path.isdir(runtime_dir):
            raise RuntimeError(f"Runtime directory does not exist: {runtime_dir}")

    def _validate_started_as_root(self):
        """Ensure the script is run as root."""
        if os.geteuid() != 0:
            raise RuntimeError("This script must be run as root.")

    def _get_caller_uid(self) -> int:
        """Get the UID of the user calling the script, handling pkexec."""
        pkexec_uid = os.environ.get("PKEXEC_UID")
        if pkexec_uid:
            return int(pkexec_uid)
        raise RuntimeError("Could not determine UID. This script must be run using pkexec.")

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
    def is_server_process_running(self):
        """Check if the server process is running."""
        # Check if the process is running
        if self._process is None:
            return False
        return self._process.poll() is None

    def start_root_helper(self):
        """Start the root helper process."""
        if self.is_server_process_running:
            print("Root helper is already running.")
            return

        helper_host_path = self._extract_root_helper_to_run_user(os.getuid())
        socket_path = _get_socket_path(os.getuid())
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

        if os.path.exists(socket_path):
            os.remove(socket_path)

        # Start pkexec and pass token via stdin
        self._process = subprocess.Popen(
            ["flatpak-spawn", "--host", "pkexec", "python3", helper_host_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Send the token and XGD_RUNTIME_DIR to server through stdin
        self._process.stdin.write(self.token.encode() + b'\n')
        self._process.stdin.flush()
        self._process.stdin.write(xdg_runtime_dir.encode() + b'\n')
        self._process.stdin.flush()

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
                response = self.send_command("[INITIALIZE]", allow_auto_start=False)
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
        if not self.is_server_process_running:
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
        if not self.is_server_process_running:
            if allow_auto_start:
                print("Root helper is not running, attempting to start it.")
                self.start_root_helper()
            else:
                raise RuntimeError("Root helper is not running.")

        if not self.is_server_process_running:
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

    def _extract_root_helper_to_run_user(self, uid: int) -> str:
        """Extract and store the root helper script in /run/user/{uid}/catalystlab-root-helper."""
        runtime_dir = _get_runtime_dir(uid)
        output_path = os.path.join(runtime_dir, "root-helper.py")
        os.makedirs(runtime_dir, exist_ok=True)
        if os.path.exists(output_path):
            os.remove(output_path)
        # Load the resource from the bundled Gio resource
        resource_path = "/com/damiandudycz/CatalystLab/root_helper.py"
        resource = Gio.Resource.load("/app/share/catalystlab/catalystlab.gresource")
        data = resource.lookup_data(resource_path, Gio.ResourceLookupFlags.NONE)
        # Write the script to the desired location
        with open(output_path, "wb") as f:
            f.write(data.get_data())
        # Set the appropriate permissions (only the user can execute)
        os.chmod(output_path, 0o700)

        return output_path

# ----------------------------------------
# Shared functions
# ----------------------------------------

def _get_runtime_dir(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
    return os.path.join(os.environ.get(runtime_env_name) or f"/run/user/{uid}", "catalystlab-root-helper")

def _get_socket_path(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
    """Get the path to the socket file for communication."""
    runtime_dir = _get_runtime_dir(uid, runtime_env_name=runtime_env_name)
    return os.path.join(runtime_dir, "root-service-socket")

# ----------------------------------------
# Server runtime
# ----------------------------------------

def _signal_handler(sig, frame):
    RootHelperServer.shared().stop()
    sys.exit(0)

if __name__ == "__main__":
    # Set up signal handling using a shared instance for all signals
    signal.signal(signal.SIGINT, _signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # Termination
    signal.signal(signal.SIGQUIT, _signal_handler)  # Quit cleanly
    RootHelperServer.shared().start()

