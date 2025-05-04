#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect
from enum import Enum
from typing import Optional
from functools import wraps
from gi.repository import Gio
from dataclasses import dataclass
from dataclasses import asdict

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    INITIALIZE = "[INITIALIZE]"

class ServerResponseStatusCode(Enum):
    OK = 0
    COMMAND_EXECUTION_FAILED = 1
    COMMAND_DECODE_FAILED = 2
    COMMAND_UNSUPPORTED_FUNC = 3
    AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS = 10
    AUTHORIZATION_WRONG_UID = 11
    AUTHORIZATION_WRONG_PID = 12
    AUTHORIZATION_WRONG_TOKEN = 13
    INITIALIZATION_ALREADY_DONE = 20
    INITIALIZATION_NOT_DONE = 21

@dataclass
class ServerResponse:
    code: ServerResponseStatusCode
    response: str | None = None

class RootHelperServer:
    _instance = None
    _is_running = False
    _server_socket = None
    _pid_lock = None

    def __init__(self):
        self.uid = self._get_caller_uid()
        print("Please provide session token:")
        self.session_token = sys.stdin.readline().strip() # TODO: This causes freeze if pkexec took longer than 1m
        print("Please provide runtime dir:")
        os.environ["CATALYSTLAB_SERVER_RUNTIME_DIR"] = sys.stdin.readline().strip()
        self._threads = []
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

        for thread in self._threads:
            if thread.is_alive():
                thread.join()
        self._threads.clear()

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
        """Listen for incoming client connections and spawn threads to handle them."""
        print(f"[root_helper] Listening on socket {socket_path}")
        server.listen()

        try:
            while self._is_running:
                try:
                    conn, _ = server.accept()
                    thread = threading.Thread(
                        target=self._handle_connection,
                        args=(conn, session_token, allowed_uid)
                    )
                    thread.start()
                    self._threads.append(thread)
                except Exception as e:
                    print(f"[root_helper] Error accepting connection: {e}")
        finally:
            self.stop()

    def _handle_connection(self, conn: socket.socket, session_token: str, allowed_uid: int):
        """Handle a single client connection in a separate thread."""
        def respond(code: ServerResponseStatusCode, response: str | None = None):
            payload = {
                "code": code.value,
                "response": response
            }
            try:
                conn.sendall(json.dumps(payload).encode())
            except Exception as e:
                print(f"[root_helper] Failed to send response: {e}")
            finally:
                conn.close()

        try:
            # Get peer credentials
            try:
                ucred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", ucred)
            except Exception:
                respond(ServerResponseStatusCode.AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS)
                return

            if uid != allowed_uid:
                respond(ServerResponseStatusCode.AUTHORIZATION_WRONG_UID)
                return
            if self._pid_lock is not None and self._pid_lock != pid:
                respond(ServerResponseStatusCode.AUTHORIZATION_WRONG_PID)
                return

            data = conn.recv(4096).decode().strip()
            if not data.startswith(session_token + " "):
                respond(ServerResponseStatusCode.AUTHORIZATION_WRONG_TOKEN)
                return

            command = data[len(session_token) + 1:]
            try:
                cmd_enum = ServerCommand(command)
            except ValueError:
                cmd_enum = None

            match cmd_enum:
                case ServerCommand.EXIT:
                    respond(ServerResponseStatusCode.OK, "Exiting")
                    self.stop()
                    return
                case ServerCommand.INITIALIZE:
                    if self._pid_lock is None:
                        self._pid_lock = pid
                        respond(ServerResponseStatusCode.OK, "Initialization succeeded")
                        return
                    else:
                        respond(ServerResponseStatusCode.INITIALIZATION_ALREADY_DONE)
                        return

            if self._pid_lock is None:
                respond(ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
                return

            try:
                call = json.loads(command)
                function_name = call.get("function")
                args = call.get("args", [])
                kwargs = call.get("kwargs", {})
            except json.JSONDecodeError:
                respond(ServerResponseStatusCode.COMMAND_DECODE_FAILED)
                return

            if function_name not in ROOT_FUNCTION_REGISTRY:
                respond(ServerResponseStatusCode.COMMAND_UNSUPPORTED_FUNC, response=f"{ROOT_FUNCTION_REGISTRY}")
                return

            try:
                result = ROOT_FUNCTION_REGISTRY[function_name](*args, **kwargs)
                respond(ServerResponseStatusCode.OK, response=f"{result}")
            except Exception as e:
                respond(ServerResponseStatusCode.COMMAND_EXECUTION_FAILED, response=str(e))

        except Exception as e:
            print(f"[root_helper] Unexpected error in connection handler: {e}")
            conn.close()

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
    """Registers a function to be allowed to call from client."""
    ROOT_FUNCTION_REGISTRY[func.__name__] = func
