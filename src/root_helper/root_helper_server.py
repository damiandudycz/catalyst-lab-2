#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, time, struct, signal, threading, json
from enum import Enum
from functools import wraps
from dataclasses import dataclass, asdict
from typing import Any
import multiprocessing
from contextlib import redirect_stdout, redirect_stderr

class RootHelperServer:
    _instance = None
    _is_running = False
    _server_socket = None
    _pid_lock = None

    def __init__(self):
        log("Welcome")
        self.uid = self._get_caller_uid()
        log("Please provide session token:")
        self.session_token = sys.stdin.readline().strip()
        log("Please provide runtime dir:")
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
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start(self):
        """Run the root helper server."""
        log("Starting server...")
        self._prepare_server_socket_directory(self.socket_dir, self.uid)
        self._server_socket = self._setup_server_socket(self.socket_path, self.uid)
        self._is_running = True
        self._listen_socket(self._server_socket, self.socket_path, self.session_token, self.uid)

    def stop(self):
        """Stop the server, closing the socket and removing any resources."""
        log("Stopping server...")
        self._is_running = False

        if self._server_socket:
            log("Closing socket...")
            self._server_socket.close()
            self._server_socket = None

        # Clean up the socket file
        if os.path.exists(self.socket_path):
            log("Removing socket file...")
            os.remove(self.socket_path)

        current_thread = threading.current_thread()
        for thread in self._threads:
            if thread.is_alive():
                log("Waiting for function thread to complete...")
                if thread is not current_thread:
                    thread.join()
        log("Clearing list of function threads...")
        self._threads.clear()
        log("Server stopped.")

    def _prepare_server_socket_directory(self, socket_dir: str, uid: int):
        """Ensure the socket directory exists and has proper permissions."""
        log("Preparing socket directory...")
        os.makedirs(socket_dir, exist_ok=True)
        os.chown(socket_dir, uid, uid)
        os.chmod(socket_dir, 0o700)

    def _setup_server_socket(self, socket_path: str, uid: int):
        """Set up the server socket for communication."""
        log("Setting up socket...")
        if os.path.exists(socket_path):
            os.remove(socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        os.chown(socket_path, uid, uid)
        os.chmod(socket_path, 0o600)
        return server

    def _listen_socket(self, server: socket.socket, socket_path: str, session_token: str, allowed_uid: int):
        """Listen for incoming client connections and spawn threads to handle them."""
        log(f"Listening on socket {socket_path}...")
        server.listen()

        try:
            while self._is_running:
                try:
                    conn, _ = server.accept()
                    log("Accepting connection...")
                    thread = threading.Thread(
                        target=self._handle_connection,
                        args=(conn, session_token, allowed_uid)
                    )
                    self._threads.append(thread)
                    thread.start()
                except Exception as e:
                    log_error(f"Error accepting connection: {e}")
        finally:
            self.stop()

    def _handle_connection(self, conn: socket.socket, session_token: str, allowed_uid: int):
        """Handle a single client connection in a separate thread."""

        def respond(code: ServerResponseStatusCode, response: str | None = None):
            # Final response, returning the result of function called.
            # Closes connection. Does not contain stdout and stderr produced by the function, just the returned value is any.
            log(f"Responding with code: {code}, response: {response}")
            server_response = ServerResponse(code=code, response=response)
            server_response_json = server_response.to_json()
            conn.sendall(f"{ServerMessageType.RETURN.value}:{len(server_response_json)}:".encode() + server_response_json.encode())
            conn.shutdown(socket.SHUT_WR)
            conn.close()
        def stdout(message: str):
            # Send part of stdout to the server.
            log(f"Sending stdout: {message}")
            conn.sendall(f"{ServerMessageType.STDOUT.value}:{len(message)}:".encode() + message.encode())
        def stderr(message: str):
            # Send part of stderr to the server.
            log(f"Sending stderr: {message}")
            conn.sendall(f"{ServerMessageType.STDERR.value}:{len(message)}:".encode() + message.encode())

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

            full_payload = data[len(session_token) + 1:]
            request_type, payload = full_payload.split(" ", 1)

            match request_type:
                case "command":
                    log("Processing command request")
                    try:
                        cmd_enum = ServerCommand(payload)
                        log(f"Command: {cmd_enum}")
                        match cmd_enum:
                            case ServerCommand.EXIT:
                                respond(ServerResponseStatusCode.OK, "Exiting")
                                self.stop()
                            case ServerCommand.INITIALIZE:
                                if self._pid_lock is None:
                                    self._pid_lock = pid
                                    respond(ServerResponseStatusCode.OK, "Initialization succeeded")
                                else:
                                    respond(ServerResponseStatusCode.INITIALIZATION_ALREADY_DONE)
                    except ValueError:
                        respond(ServerResponseStatusCode.COMMAND_DECODE_FAILED)
                case "function":
                    log("Processing function request")
                    if self._pid_lock is None:
                        respond(ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
                    else:
                        try:
                            func_struct = ServerFunction.from_json(payload)
                            log(f"Function: {func_struct.function_name} ({func_struct.args}) ({func_struct.kwargs})")
                            if func_struct.function_name not in ROOT_FUNCTION_REGISTRY:
                                respond(ServerResponseStatusCode.COMMAND_UNSUPPORTED_FUNC, response=f"{ROOT_FUNCTION_REGISTRY}")
                            else:
                                try:
                                    result = _run_function_with_streaming_output(
                                        ROOT_FUNCTION_REGISTRY[func_struct.function_name],
                                        func_struct.args,
                                        func_struct.kwargs,
                                        stdout,
                                        stderr
                                    )
                                    respond(ServerResponseStatusCode.OK, response=result)
                                except Exception as e:
                                    respond(ServerResponseStatusCode.COMMAND_EXECUTION_FAILED, response=str(e))
                        except ValueError:
                            respond(ServerResponseStatusCode.COMMAND_DECODE_FAILED)
                case _:
                    respond(ServerResponseStatusCode.COMMAND_DECODE_FAILED)

        except Exception as e:
            log_error(f"Unexpected error in connection handler: {e}")
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

# ----------------------------------------
# Helpers
# ----------------------------------------

# Function registry used by injected root functions
ROOT_FUNCTION_REGISTRY = {}

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    INITIALIZE = "[INITIALIZE]"

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
    COMMAND_EXECUTION_FAILED = 1
    COMMAND_DECODE_FAILED = 2
    COMMAND_UNSUPPORTED_FUNC = 3
    AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS = 10
    AUTHORIZATION_WRONG_UID = 11
    AUTHORIZATION_WRONG_PID = 12
    AUTHORIZATION_WRONG_TOKEN = 13
    INITIALIZATION_ALREADY_DONE = 20
    INITIALIZATION_NOT_DONE = 21

class ServerMessageType(Enum):
    RETURN = 0
    STDOUT = 1
    STDERR = 2

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

# Use instead of print to see results in client.
def log(string: str):
    sys.stdout.write(f"{string}\n")
    sys.stdout.flush()  # Ensure it's written immediately
def log_error(string: str):
    sys.stderr.write(f"{string}\n")
    sys.stderr.flush()  # Ensure it's written immediately

def root_function(func):
    """Registers a function to be allowed to call from client."""
    ROOT_FUNCTION_REGISTRY[func.__name__] = func

def _run_and_capture_target(func, args, kwargs, write_fd, result_queue):
    with os.fdopen(write_fd, 'w', buffering=1) as f:
        stdout_wrapper = StreamWrapper(f, StreamType.STDOUT)
        stderr_wrapper = StreamWrapper(f, StreamType.STDERR)
        with redirect_stdout(stdout_wrapper), redirect_stderr(stderr_wrapper):
            try:
                result = func(*args, **kwargs)
                result_queue.put(result)
            except Exception as e:
                result_queue.put(e)

def _run_function_with_streaming_output(func, args, kwargs, stdout_callback, stderr_callback) -> Any | None:
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
