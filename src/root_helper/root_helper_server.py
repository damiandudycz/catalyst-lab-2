#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, time, struct, signal, threading, json, multiprocessing
from enum import Enum
from functools import wraps
from dataclasses import dataclass, asdict
from typing import Any
from contextlib import redirect_stdout, redirect_stderr

class RootHelperServer:

    ROOT_FUNCTION_REGISTRY = {}               # Registry used to collect registered root functions.
    _instance: RootHelperServer | None = None # Singleton shared instance.

    # --------------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self):
        self.is_running = False
        self.server_socket: socket.socket | None = None
        self.pid_lock: int | None = None
        self.threads: List[threading.Thread] = []
        self.uid: int = self._get_caller_uid()
        log("Welcome")
        log("Please provide session token:")
        self.session_token = sys.stdin.readline().strip()
        log("Please provide runtime dir:")
        os.environ["CATALYSTLAB_SERVER_RUNTIME_DIR"] = sys.stdin.readline().strip()
        self.runtime_dir: str = RootHelperServer.get_runtime_dir(self.uid, runtime_env_name="CATALYSTLAB_SERVER_RUNTIME_DIR")
        self.socket_path: str = RootHelperServer.get_socket_path(self.uid, runtime_env_name="CATALYSTLAB_SERVER_RUNTIME_DIR")
        self.socket_dir:  str = os.path.dirname(self.socket_path)
        # Validate state:
        self._validate_started_as_root()
        self._validate_session_token(self.session_token)
        self._validate_runtime_dir_and_uid(self.runtime_dir, self.uid)

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self):
        """Run the root helper server."""
        if self.is_running:
            log_error("Server is already running")
            return
        log("Starting server...")
        self.pid_lock = None
        self.prepare_server_socket_directory(self.socket_dir, self.uid)
        self.server_socket = self.setup_server_socket(self.socket_path, self.uid)
        self.is_running = True
        self.listen_socket(self.server_socket, self.socket_path, self.session_token, self.uid)

    def stop(self):
        """Stop the server, closing the socket and removing any resources."""
        if not self.is_running:
            log_error("Server is not running")
            return
        log("Stopping server...")
        self.is_running = False

        if self.server_socket:
            log("Closing socket...")
            self.server_socket.close()
            self.server_socket = None

        # Clean up the socket file
        if os.path.exists(self.socket_path):
            log("Removing socket file...")
            os.remove(self.socket_path)

        current_thread = threading.current_thread()
        for thread in self.threads:
            if thread.is_alive():
                if thread is not current_thread:
                    thread.join()
        self.threads.clear()
        log("Server stopped.")

    # --------------------------------------------------------------------------------
    # Socket management:

    def prepare_server_socket_directory(self, socket_dir: str, uid: int):
        """Ensure the socket directory exists and has proper permissions."""
        log("Preparing socket directory...")
        os.makedirs(socket_dir, exist_ok=True)
        os.chown(socket_dir, uid, uid)
        os.chmod(socket_dir, 0o700)

    def setup_server_socket(self, socket_path: str, uid: int):
        """Set up the server socket for communication."""
        log("Setting up socket...")
        if os.path.exists(socket_path):
            os.remove(socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        os.chown(socket_path, uid, uid)
        os.chmod(socket_path, 0o600)
        return server

    # --------------------------------------------------------------------------------
    # Handling connections:

    def listen_socket(self, server: socket.socket, socket_path: str, session_token: str, allowed_uid: int):
        """Listen for incoming client connections and spawn threads to handle them."""
        log(f"Listening on socket {socket_path}...")
        server.listen()
        try:
            while self.is_running:
                try:
                    conn, _ = server.accept()
                    log("Accepting connection...")
                    thread = threading.Thread(
                        target=self.handle_connection,
                        args=(conn, session_token, allowed_uid)
                    )
                    self.threads.append(thread)
                    thread.start()
                except Exception as e:
                    log_error(f"Error accepting connection: {e}")
        finally:
            self.stop()

    def handle_connection(self, conn: socket.socket, session_token: str, allowed_uid: int):
        """Handle a single client connection in a separate thread."""
        conn_thread = threading.current_thread()
        try:
            # Validate peer credentials:
            try:
                ucred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", ucred)
            except Exception:
                self.respond(conn, conn_thread, ServerResponseStatusCode.AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS)
                return

            if uid != allowed_uid:
                self.respond(conn, conn_thread, ServerResponseStatusCode.AUTHORIZATION_WRONG_UID)
                return
            if self.pid_lock is not None and self.pid_lock != pid:
                self.respond(conn, conn_thread, ServerResponseStatusCode.AUTHORIZATION_WRONG_PID)
                return

            # Receive request data:
            data = conn.recv(4096).decode().strip()
            if not data.startswith(session_token + " "):
                self.respond(conn, conn_thread, ServerResponseStatusCode.AUTHORIZATION_WRONG_TOKEN)
                return
            full_payload = data[len(session_token) + 1:]
            request_type, payload = full_payload.split(" ", 1)

            # Process request:
            match request_type:
                case "command":
                    self.handle_command_request(conn, conn_thread, pid, payload)
                case "function":
                    self.handle_function_request(conn, conn_thread, pid, payload)
                case _:
                    self.respond(conn, conn_thread, ServerResponseStatusCode.COMMAND_DECODE_FAILED)

        except Exception as e:
            log_error(f"Unexpected error in connection handler: {e}")
            conn.close()

    def handle_command_request(self, conn: socket.conn, conn_thread: threading.Thread, pid: int, payload: str):
        log("Processing command request")
        try:
            cmd_enum = ServerCommand(payload)
            log(f"Command: {cmd_enum}")
            match cmd_enum:
                case ServerCommand.EXIT:
                    self.respond(conn, conn_thread, ServerResponseStatusCode.OK, "Exiting...")
                    self.stop()
                case ServerCommand.PING:
                    self.respond(conn, conn_thread, ServerResponseStatusCode.OK)
                case ServerCommand.HANDSHAKE:
                    if self.pid_lock is None:
                        self.pid_lock = pid
                        self.respond(conn, conn_thread, ServerResponseStatusCode.OK, "Initialization succeeded")
                    else:
                        self.respond(conn, conn_thread, ServerResponseStatusCode.INITIALIZATION_ALREADY_DONE)
        except ValueError:
            self.respond(conn, conn_thread, ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def handle_function_request(self, conn: socket.conn, conn_thread: threading.Thread, pid: int, payload: str):
        log("Processing function request")
        if self.pid_lock is None:
            self.respond(conn, conn_thread, ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
        else:
            try:
                func_struct = ServerFunction.from_json(payload)
                log(f"Function: {func_struct.function_name} ({func_struct.args}) ({func_struct.kwargs})")
                if func_struct.function_name not in RootHelperServer.ROOT_FUNCTION_REGISTRY:
                    self.respond(conn, conn_thread, ServerResponseStatusCode.COMMAND_UNSUPPORTED_FUNC, response=f"{func_struct.func_name}")
                else:
                    try:
                        result = _run_function_with_streaming_output(
                            RootHelperServer.ROOT_FUNCTION_REGISTRY[func_struct.function_name],
                            func_struct.args,
                            func_struct.kwargs,
                            self.respond_stdout,
                            self.respond_stderr
                        )
                        self.respond(conn, conn_thread, ServerResponseStatusCode.OK, response=result)
                    except Exception as e:
                        self.respond(conn, conn_thread, ServerResponseStatusCode.COMMAND_EXECUTION_FAILED, response=str(e))
            except ValueError:
                self.respond(conn, conn_thread, ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def respond(self, conn: socket.conn, conn_thread: threading.Thread, code: ServerResponseStatusCode, response: str | None = None):
        # Final response, returning the result of function called.
        # Closes connection. Does not contain stdout and stderr produced by the function, just the returned value is any.
        log(f"Responding with code: {code}, response: {response}")
        server_response = ServerResponse(code=code, response=response)
        server_response_json = server_response.to_json()
        conn.sendall(f"{ServerMessageType.RETURN.value}:{len(server_response_json)}:".encode() + server_response_json.encode())
        conn.shutdown(socket.SHUT_WR)
        conn.close()
        self.threads.remove(conn_thread)

    def respond_stdout(self, conn: socket.conn, message: str):
        # Send part of stdout to the server.
        log(f"Sending stdout: {message}")
        conn.sendall(f"{ServerMessageType.STDOUT.value}:{len(message)}:".encode() + message.encode())

    def respond_stderr(self, conn: socket.conn, message: str):
        # Send part of stderr to the server.
        log(f"Sending stderr: {message}")
        conn.sendall(f"{ServerMessageType.STDERR.value}:{len(message)}:".encode() + message.encode())

    # --------------------------------------------------------------------------------
    # Helper function.

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

    # --------------------------------------------------------------------------------
    # Shared helper functions.

    def get_runtime_dir(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
        return os.path.join(os.environ.get(runtime_env_name) or f"/run/user/{uid}", "catalystlab-root-helper")

    def get_socket_path(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
        """Get the path to the socket file for communication."""
        runtime_dir = RootHelperServer.get_runtime_dir(uid, runtime_env_name=runtime_env_name)
        return os.path.join(runtime_dir, "root-service-socket")

# --------------------------------------------------------------------------------
# Server runtime lifecycle.
# --------------------------------------------------------------------------------

def _signal_handler(sig, frame):
    RootHelperServer.shared().stop()
    sys.exit(0)

def __init_server__():
    # Set up signal handling using a shared instance for all signals
    signal.signal(signal.SIGINT, _signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # Termination
    signal.signal(signal.SIGQUIT, _signal_handler)  # Quit cleanly
    RootHelperServer.shared().start()

# --------------------------------------------------------------------------------
# Helper functions and types.
# --------------------------------------------------------------------------------

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    HANDSHAKE = "[HANDSHAKE]"
    PING = "[PING]"

    @property
    def function_name(self):
        return self.value

class ServerFunction:
    def __init__(self, function_name: str, *args, **kwargs):
        self.function_name = function_name
        self.args = args
        self.kwargs = kwargs

    def to_json(self):
        """Convert the ServerFunction instance to a JSON string."""
        return json.dumps({
            "function": self.function_name,
            "args": self.args,
            "kwargs": self.kwargs
        })

    @classmethod
    def from_json(cls, json_str: str):
        """Create a ServerFunction instance from a JSON string."""
        try:
            data = json.loads(json_str)
            return cls(data["function"], *data.get("args", []), **data.get("kwargs", {}))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(f"Invalid ServerFunction JSON: {e}")

@dataclass
class ServerResponse:
    code: ServerResponseStatusCode
    response: Any | None = None

    def to_json(self) -> str:
        """Convert the ServerResponse instance to a JSON string, including dynamic type info."""
        response_data = {
            "code": self.code.value,
            "response": self.response
        }
        return json.dumps(response_data)

    @classmethod
    def from_json(cls, json_str: str) -> 'ServerResponse':
        """Create a ServerResponse instance from a JSON string, restoring the type of the response."""
        data = json.loads(json_str)
        code = ServerResponseStatusCode(data["code"])
        response = data.get("response")
        return cls(code=code, response=response)

class ServerResponseStatusCode(Enum):
    OK = 0
    COMMAND_EXECUTION_FAILED = 10
    COMMAND_DECODE_FAILED = 11
    COMMAND_UNSUPPORTED_FUNC = 12
    AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS = 20
    AUTHORIZATION_WRONG_UID = 21
    AUTHORIZATION_WRONG_PID = 22
    AUTHORIZATION_WRONG_TOKEN = 23
    INITIALIZATION_ALREADY_DONE = 30
    INITIALIZATION_NOT_DONE = 31

class ServerMessageType(Enum):
    RETURN = 0
    STDOUT = 1
    STDERR = 2

# --------------------------------------------------------------------------------
# Server logging.
# --------------------------------------------------------------------------------

# Use instead of print to see results in client.
def log(string: str):
    sys.stdout.write(f"{string}\n")
    sys.stdout.flush()  # Ensure it's written immediately
def log_error(string: str):
    sys.stderr.write(f"{string}\n")
    sys.stderr.flush()  # Ensure it's written immediately

# --------------------------------------------------------------------------------
# @root_function decorator.
# --------------------------------------------------------------------------------

def root_function(func):
    """Registers a function to be allowed to call from client."""
    """Server version of this decorator just collects these functions into ROOT_FUNCTION_REGISTRY."""
    RootHelperServer.ROOT_FUNCTION_REGISTRY[func.__name__] = func

# --------------------------------------------------------------------------------
# Function processing with output handlers support.
# --------------------------------------------------------------------------------

class StreamType(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"

class StreamWrapper:
    def __init__(self, stream, stream_type: StreamType):
        self.stream = stream
        self.stream_type = stream_type
    def write(self, message):
        if not message.strip():
            return  # Skip empty messages
        payload = json.dumps({
            "stream": self.stream_type.value,
            "message": message.rstrip("\n")
        })
        self.stream.write(payload + "\n")
        self.stream.flush()
    def flush(self):
        self.stream.flush()

def _run_function_with_streaming_output(func, args, kwargs, stdout_callback, stderr_callback) -> Any | None:
    """Takes a function and runs it as separate process while sending its output to stdout_callback and stderr_callback."""
    """Spawned process runs in sync, so this function only after given function is ready."""
    """Returns what given function returns or throws if given function throws."""
    """This is used to process given function output data for handlers."""
    read_fd, write_fd = os.pipe()
    result_queue = multiprocessing.Queue()

    proc = multiprocessing.Process(
        target=_run_and_capture_target,
        args=(func, args, kwargs, write_fd, result_queue)
    )
    proc.start()
    os.close(write_fd)

    def stream_reader():
        with os.fdopen(read_fd, 'r') as pipe:
            for line in pipe:
                try:
                    data = json.loads(line)
                    stream = data["stream"]
                    if stream == StreamType.STDOUT.value:
                        stdout_callback(data["message"])
                    elif stream == StreamType.STDERR.value:
                        stderr_callback(data["message"])
                except json.JSONDecodeError as e:
                    stderr_callback(f"[parser error] {e}: {line}")

    reader_thread = threading.Thread(target=stream_reader)
    reader_thread.start()

    proc.join()
    reader_thread.join()

    # Safe to use .empty() here because proc and reader_thread have been joined,
    # ensuring no further writes to the result_queue will occur.
    if not result_queue.empty():
        result = result_queue.get()
        if isinstance(result, Exception):
            raise result
        return result
    else:
        return None

def _run_and_capture_target(func, args, kwargs, write_fd, result_queue):
    """Runs function and redirects current stdout and stderr from it to StreamWrapper objects."""
    with os.fdopen(write_fd, 'w', buffering=1) as f:
        stdout_wrapper = StreamWrapper(f, StreamType.STDOUT)
        stderr_wrapper = StreamWrapper(f, StreamType.STDERR)
        with redirect_stdout(stdout_wrapper), redirect_stderr(stderr_wrapper):
            try:
                result = func(*args, **kwargs)
                result_queue.put(result)
            except Exception as e:
                result_queue.put(e)

