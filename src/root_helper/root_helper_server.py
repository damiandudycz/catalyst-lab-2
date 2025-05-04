#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect
from enum import Enum
from typing import Optional
from functools import wraps
from gi.repository import Gio
from dataclasses import dataclass

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    INITIALIZE = "[INITIALIZE]"

class RootHelperServer:
    _instance = None

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
            cls._instance = cls()
        return cls._instance

    def start(self):
        """Run the root helper server."""
        self._prepare_server_socket_directory(self.socket_dir, self.uid)
        self._server_socket = self._setup_server_socket(self.socket_path, self.uid)
        self._is_running = True
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

def __init_server__():
    # Set up signal handling using a shared instance for all signals
    signal.signal(signal.SIGINT, _signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # Termination
    signal.signal(signal.SIGQUIT, _signal_handler)  # Quit cleanly
    RootHelperServer.shared().start()

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
