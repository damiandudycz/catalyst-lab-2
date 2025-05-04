#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect
from enum import Enum
from typing import Optional
from functools import wraps
from gi.repository import Gio

# ----------------------------------------
# Server code
# ----------------------------------------

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    INITIALIZE = "[INITIALIZE]"

# Function registry used by injected root functions
ROOT_FUNCTION_REGISTRY = {}

def root_function(func):
    """Registers a function and replaces it with a proxy that calls the root server."""
    # Example:
    #@root_function
    #def add(a, b):
    #    return a + b
    #
    # Then in client you can call it directly:
    # add(1, 2) - it will be actually running on root server
    # or even with async variant and handler:
    # add._async(handler, 1, 2)
    # IMPORTANT: Functions decorated with root_function must be defined in global space and can't accept reference types, including self, cls etc.
    ROOT_FUNCTION_REGISTRY[func.__name__] = func
    @wraps(func)
    def proxy_function(*args, **kwargs):
        return RootHelperClient.shared().call_root_function(func.__name__, *args, **kwargs)
    # Attach async variant
    def async_variant(handler, *args, **kwargs):
        return RootHelperClient.shared().call_root_function_async(func.__name__, handler, *args, **kwargs)
    # Add .async_ to the proxy
    proxy_function._async = async_variant
    return proxy_function

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
                try:
                    cmd_enum = ServerCommand(command)
                except ValueError:
                    cmd_enum = None
                match cmd_enum:
                    case ServerCommand.EXIT:
                        conn.sendall(b"Exiting.\n")
                        conn.close()
                        break
                    case ServerCommand.INITIALIZE:
                        # Used to ping the server to see when it is ready.
                        # Also locks future calls to this server for pid of this connection.
                        if self._pid_lock is None:
                            self._pid_lock = pid
                            conn.sendall(json.dumps({"status": "ok"}).encode())  # Fixed parentheses and encoding
                            conn.close()
                            continue
                        else:
                            # Use a server response from the enum for consistency
                            conn.sendall("[ERROR: Already initialized]\n")  # Standardized response
                            conn.close()
                            break

                if self._pid_lock is None:
                    conn.sendall(b"ERROR: Connection initialization not finished.\n")
                    conn.close()
                    continue

                try:
                    call = json.loads(command)
                    function_name = call.get("function")
                    args = call.get("args", [])
                    kwargs = call.get("kwargs", {})
                except json.JSONDecodeError:
                    conn.sendall(b"ERROR: Invalid JSON\n")
                    conn.close()
                    continue
                if function_name not in ROOT_FUNCTION_REGISTRY:
                    conn.sendall(b"ERROR: Function not allowed\n")
                    conn.close()
                    continue
                try:
                    result = ROOT_FUNCTION_REGISTRY[function_name](*args, **kwargs)
                    response = json.dumps({"status": "ok", "result": result})
                except Exception as e:
                    response = json.dumps({"status": "error", "message": str(e)})
                conn.sendall(response.encode())
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

    def call_root_function(self, func_name: str, *args, **kwargs):
        if not self._ensure_server_ready():
            return "[WAITING]"

        payload = {
            "function": func_name,
            "args": args,
            "kwargs": kwargs,
        }

        print(payload)

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.socket_path)
            s.sendall(f"{self.token} {json.dumps(payload)}".encode())

            response_chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response_chunks.append(chunk)

            result = json.loads(b"".join(response_chunks).decode())
            if result["status"] == "ok":
                return result["result"]
            else:
                raise RuntimeError(f"Root function error: {result['message']}")
        except FileNotFoundError as e:
            raise ConnectionRefusedError("Socket not available yet") from e

    def call_root_function_async(self, func_name: str, handler: callable, *args, **kwargs) -> threading.Thread | str:
        """
        Asynchronously call a root function and pass the decoded response (JSON) to the handler.
        """
        if not self._ensure_server_ready():
            return "[WAITING]"

        payload = {
            "function": func_name,
            "args": args,
            "kwargs": kwargs,
        }

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.socket_path)
            s.sendall(f"{self.token} {json.dumps(payload)}".encode())
        except FileNotFoundError as e:
            raise ConnectionRefusedError("Socket not available yet") from e

        def reader_thread(sock, callback):
            try:
                buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                if buffer:
                    try:
                        result = json.loads(buffer.decode())
                    except json.JSONDecodeError:
                        result = {"status": "error", "message": "Invalid JSON response"}
                    callback(result)
            finally:
                sock.close()

        thread = threading.Thread(target=reader_thread, args=(s, handler), daemon=True)
        thread.start()
        return thread

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

        from .environment import RuntimeEnv
        cmd_prefix = ["flatpak-spawn", "--host"] if RuntimeEnv.current() == RuntimeEnv.FLATPAK else []
        cmd_authorize = ["pkexec"]
        exec_call = cmd_prefix + cmd_authorize + [helper_host_path]

        # Start pkexec and pass token via stdin
        self._process = subprocess.Popen(
            exec_call,
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
                response = self.send_command(ServerCommand.INITIALIZE, allow_auto_start=False)
                if response.strip() == '{"status": "ok"}': # TODO: Map to json object and get status that way
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
            self.send_command(ServerCommand.EXIT)
        except Exception as e:
            print(f"Failed to stop root helper: {e}")
        finally:
            self._process = None
            print("Root helper process stopped.")

    def send_command(self, command: ServerCommand, allow_auto_start=True) -> str:
        """Send a command to the root helper server."""
        if not self._ensure_server_ready(allow_auto_start):
            return "[WAITING]"

        print(f"> {command.value}")
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.socket_path)
            s.sendall(f"{self.token} {command.value}".encode())
            response_chunks = []
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                response_chunks.append(chunk)
                print(chunk.decode(), end='')
            return b"".join(response_chunks).decode()
        except FileNotFoundError as e:
            raise ConnectionRefusedError("Socket not available yet") from e

    def send_command_async(self, command: ServerCommand, handler: callable, allow_auto_start=True) -> threading.Thread | str:
        """Send a command to the root helper server in async mode."""
        if not self._ensure_server_ready(allow_auto_start):
            return "[WAITING]"

        print(f"> {command.value}")
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.socket_path)
            s.sendall(f"{self.token} {command.value}".encode())
        except FileNotFoundError as e:
            raise ConnectionRefusedError("Socket not available yet") from e

        def reader_thread(sock, callback):
            try:
                buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        if buffer:
                            callback(buffer.decode())
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        callback(line.decode())
            finally:
                sock.close()

        thread = threading.Thread(target=reader_thread, args=(s, handler), daemon=True)
        thread.start()
        return thread

    def _ensure_server_ready(self, allow_auto_start=True):
        """Ensure the root helper server is running and the socket is available."""
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
            return False
        return True

    def _extract_root_helper_to_run_user(self, uid: int) -> str:
        """Extracts root helper server script and appends root-callable functions."""
        import os

        # Runtime directory where the generated root-helper script will be placed
        runtime_dir = _get_runtime_dir(uid)
        output_path = os.path.join(runtime_dir, "root-helper.py")

        # Ensure the directory exists
        os.makedirs(runtime_dir, exist_ok=True)

        # If the file already exists, remove it
        if os.path.exists(output_path):
            os.remove(output_path)

        # Load the embedded server code from resources
        resource_path = "/com/damiandudycz/CatalystLab/root_helper.py"
        resource = Gio.Resource.load("/app/share/catalystlab/catalystlab.gresource")
        data = resource.lookup_data(resource_path, Gio.ResourceLookupFlags.NONE)
        server_code = data.get_data().decode()

        # Collect the root functions (dynamically registered)
        injected_functions = collect_root_function_sources()

        # Combine the server code with the dynamically injected functions
        full_code = server_code + "\n\n" + injected_functions

        # Add the `if __name__ == "__main__":` block to run the server
        # This needs to be added bellow dynamic functions.
        full_code += """
if __name__ == "__main__":
    # Set up signal handling using a shared instance for all signals
    signal.signal(signal.SIGINT, _signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # Termination
    signal.signal(signal.SIGQUIT, _signal_handler)  # Quit cleanly
    RootHelperServer.shared().start()
"""

        # Write the full code to the output file
        with open(output_path, "w") as f:
            f.write(full_code)

        # Make the script executable
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

def collect_root_function_sources() -> str:
    """Returns all registered root function sources as a single Python string."""
    if not ROOT_FUNCTION_REGISTRY:
        return ""

    sources = []
    for func in ROOT_FUNCTION_REGISTRY.values():
        try:
            sources.append(inspect.getsource(func))
        except OSError:
            print(f"Warning: could not get source for function {func.__name__}")
    return "\n\n# ---- Injected root functions ----\n\n" + "\n\n".join(sources)

# ----------------------------------------
# Server runtime
# ----------------------------------------

def _signal_handler(sig, frame):
    RootHelperServer.shared().stop()
    sys.exit(0)

