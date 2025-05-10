#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, time, struct, signal, threading
import json, multiprocessing
from enum import Enum
from functools import wraps
from dataclasses import dataclass, asdict
from typing import Any
from contextlib import redirect_stdout, redirect_stderr
from typing import List
from datetime import datetime

class StreamPipe(Enum):
    RETURN = 0
    STDOUT = 1
    STDERR = 2

class ServerResponseStatusCode(Enum):
    OK = 0
    JOB_WAS_TERMINATED = 1
    COMMAND_EXECUTION_FAILED = 10
    COMMAND_DECODE_FAILED = 11
    COMMAND_UNSUPPORTED_FUNC = 12
    AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS = 20
    AUTHORIZATION_WRONG_UID = 21
    AUTHORIZATION_WRONG_PID = 22
    AUTHORIZATION_WRONG_TOKEN = 23
    INITIALIZATION_ALREADY_DONE = 30
    INITIALIZATION_NOT_DONE = 31

class RootHelperServer:

    ROOT_FUNCTION_REGISTRY = {} # Registry for collecting root functions.
    _instance: RootHelperServer | None = None # Singleton shared instance.
    hide_logs = False

    # --------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self):
        self.is_running = False
        self.server_socket: socket.socket | None = None
        self.pid_lock: int | None = None
        self.jobs: List[Job] = []
        self.last_ping: datetime | None = None
        print("[Server]: " + "Welcome")
        self.read_initial_session_data()
        devnull_read = open(os.devnull, 'r')
        devnull_write = open(os.devnull, 'w')
        sys.stdin = devnull_read
        if RootHelperServer.hide_logs:
            sys.stdout = devnull_write
            sys.stderr = devnull_write

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def read_initial_session_data(self):
        """Reads session uuid, token and runtime env from STDIN and os.env."""
        self.uid: int = int(os.environ.get("PKEXEC_UID"))
        print("[Server]: " + "Please provide session token:")
        self.session_token = sys.stdin.readline().strip()
        print("[Server]: " + "Please provide runtime dir:")
        os.environ["CL_SERVER_RUNTIME_DIR"] = sys.stdin.readline().strip()
        self.runtime_dir: str = RootHelperServer.get_runtime_dir(
            self.uid, runtime_env_name="CL_SERVER_RUNTIME_DIR"
        )
        self.socket_path: str = RootHelperServer.get_socket_path(
            self.uid, runtime_env_name="CL_SERVER_RUNTIME_DIR"
        )

    def validate_session(self):
        """Validates basic session information - run as root, token correct,"""
        """runtime dir, uuid."""
        # Validate that script was started as root:
        if os.geteuid() != 0:
            raise RuntimeError("This script must be run as root.")
        # Validate that the session token is a valid UUIDv4:
        try:
            token_obj = uuid.UUID(self.session_token, version=4)
            if str(token_obj) != self.session_token:
                raise RuntimeError("Token string doesn't match parsed UUID")
        except (ValueError, AttributeError):
            raise RuntimeError("Invalid session token: must be a valid UUIDv4.")
        # Ensure the runtime directory exists and the UID is valid:
        if self.uid is None:
            raise RuntimeError("Cannot determine calling user UID.")
        if self.runtime_dir is None:
            raise RuntimeError(f"Runtime directory not specified.")
        if not os.path.isdir(self.runtime_dir):
            raise RuntimeError(f"Runtime directory missing: {self.runtime_dir}")

    def start(self):
        """Run the root helper server."""
        if self.is_running:
            print("[Server]: ERROR: " + "Server is already running")
            return
        print("[Server]: " + "Starting server...")
        self.pid_lock = None
        self.server_socket = self.setup_socket(self.socket_path, self.uid)
        self.is_running = True
        self.listen_socket(
            server=self.server_socket,
            socket_path=self.socket_path,
            session_token=self.session_token,
            allowed_uid=self.uid
        )

    def stop(self, called_by_job: Job | None = None, after_jobs_cleaned: callable | None = None):
        """Stop the server, closing the socket and removing any resources."""
        if not self.is_running:
            print("[Server]: ERROR: Server is not running")
            return
        print("[Server]: Stopping server...")

        def safe_execute(func, *args, **kwargs):
            """Executes a function with error handling."""
            try:
                return func(*args, **kwargs)
            except Exception as e:
                print(f"[Server]: ERROR: {str(e)}")
                return None
        # Handle job termination
        jobs_to_wait = []
        for job in [j for j in self.jobs if j is not called_by_job]:
            job_to_wait = safe_execute(job.terminate)
            if job_to_wait:
                jobs_to_wait.append(job_to_wait)
        Job.join_all(jobs_to_wait, timeout=6)
        print("[Server]: Waiting done")
        # Force terminate remaining jobs and clear jobs list
        for job in self.jobs[:]:
            safe_execute(job.terminate, force=True)
        self.jobs = [called_by_job] if called_by_job in self.jobs else []
        # Call after_jobs_cleaned callback if provided
        if after_jobs_cleaned:
            safe_execute(after_jobs_cleaned)
        # Close server socket and remove socket file
        if self.server_socket:
            print("[Server]: Closing socket...")
            safe_execute(self.server_socket.close)
            self.server_socket = None
        if os.path.exists(self.socket_path):
            print("[Server]: Removing socket file...")
            safe_execute(os.remove, self.socket_path)
        # Close app
        print("[Server]: Server stopped.")
        self.is_running = False
        os._exit(0)

    # --------------------------------------------------------------------------
    # Socket management:

    def setup_socket(self, socket_path: str, uid: int):
        """Set up the server socket for communication."""
        print("[Server]: " + "Setting up socket...")
        socket_dir: str = os.path.dirname(socket_path)
        os.makedirs(socket_dir, exist_ok=True)
        os.chown(socket_dir, uid, uid)
        os.chmod(socket_dir, 0o700)
        if os.path.exists(socket_path):
            os.remove(socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        os.chown(socket_path, uid, uid)
        os.chmod(socket_path, 0o600)
        return server

    # --------------------------------------------------------------------------
    # Handling connections:

    def listen_socket(self, server: socket.socket, socket_path: str, session_token: str, allowed_uid: int):
        """Listen for incoming client connections and spawn threads to handle them."""
        print("[Server]: " + f"Listening on socket {socket_path}...")
        server.listen()
        while self.is_running:
            try:
                conn, _ = server.accept()
                print("[Server]: " + "Accepting connection...")
                job = Job(server=self, conn=conn)
                job.thread = threading.Thread(
                    target=self.handle_connection,
                    args=(job, conn, session_token, allowed_uid)
                )
                self.jobs.append(job)
                job.thread.start()
            except Exception as e:
                print("[Server]: ERROR: " + f"Error accepting connection: {e}")
                # Stop server after errors in single connection:
                self.stop()
                return
        self.stop()

    def handle_connection(self, job: Job, conn: socket.socket, session_token: str, allowed_uid: int):
        """Handle a single client connection in a separate thread."""
        try:
            # Validate peer credentials:
            try:
                ucred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", ucred)
            except Exception:
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS)
                return

            if uid != allowed_uid:
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.AUTHORIZATION_WRONG_UID)
                return
            if self.pid_lock is not None and self.pid_lock != pid:
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.AUTHORIZATION_WRONG_PID)
                return

            # Receive request data:
            data = conn.recv(4096).decode().strip()
            if not data.startswith(session_token):
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.AUTHORIZATION_WRONG_TOKEN)
                return
            full_payload = data[len(session_token) + 1:]
            if " " not in full_payload:
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)
                return
            request_type, payload = full_payload.split(" ", 1)

            # Process request:
            match request_type:
                case "command":
                    self.handle_command_request(job, conn, pid, payload)
                case "function":
                    self.handle_function_request(job, conn, pid, payload)
                case _:
                    self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

        except Exception as e:
            print("[Server]: ERROR: " + f"Unexpected error in connection handler: {e}")
            conn.close()

    def handle_command_request(self, job: Job, conn: socket.socket, pid: int, payload: str):
        print("[Server]: " + "Processing command request")
        try:
            cmd_enum = ServerCommand(payload)
            print("[Server]: " + f"Command: {cmd_enum}")
            match cmd_enum:
                case ServerCommand.EXIT:
                    self.respond(conn=conn, job=job, code=ServerResponseStatusCode.OK, pipe=StreamPipe.STDOUT, response="Exiting...")
                    self.stop(called_by_job=job, after_jobs_cleaned = lambda: self.respond(conn=conn, job=job, code=ServerResponseStatusCode.OK, response="Exited"))
                case ServerCommand.PING:
                    self.respond(conn=conn, job=job, code=ServerResponseStatusCode.OK, response="PONG")
                case ServerCommand.HANDSHAKE:
                    if self.pid_lock is None:
                        self.pid_lock = pid
                        self.respond(conn=conn, job=job, code=ServerResponseStatusCode.OK, response="Initialization succeeded")
                    else:
                        self.respond(conn=conn, job=job, code=ServerResponseStatusCode.INITIALIZATION_ALREADY_DONE, response="Initialization already finished")
        except ValueError as e:
            self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def handle_function_request(self, job: Job, conn: socket.socket, pid: int, payload: str):
        print("[Server]: " + "Processing function request")
        if self.pid_lock is None:
            self.respond(conn=conn, job=job, code=ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
        else:
            try:
                func_struct = ServerFunction.from_json(payload)
                print("[Server]: " + f"Function: {func_struct.function_name} ({func_struct.args}) ({func_struct.kwargs})")
                if func_struct.function_name not in RootHelperServer.ROOT_FUNCTION_REGISTRY:
                    self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_UNSUPPORTED_FUNC, response=f"{func_struct.function_name}")
                else:
                    try:
                        result = _run_function_with_streaming_output(
                            conn,
                            job,
                            RootHelperServer.ROOT_FUNCTION_REGISTRY[func_struct.function_name],
                            func_struct.args,
                            func_struct.kwargs,
                            self.respond
                        )
                        if not job.was_terminated:
                            self.respond(conn=conn, job=job, code=ServerResponseStatusCode.OK, response=result)
                    except Exception as e:
                        if not job.was_terminated:
                            self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_EXECUTION_FAILED, response=str(e))
            except ValueError:
                self.respond(conn=conn, job=job, code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def respond(self, conn: socket.socket, job: Job, code: ServerResponseStatusCode = ServerResponseStatusCode.OK, pipe: StreamPipe | int = StreamPipe.RETURN, response: str | None = None):
        # Final response, returning the result of function called.
        # Closes connection. Does not contain stdout and stderr produced by the function, just the returned value if any.
        if isinstance(pipe, int):
            # If pipe was passed by ID, convert it back to pipe object
            pipe = StreamPipe(pipe)
        if code != ServerResponseStatusCode.OK and pipe != StreamPipe.RETURN:
            raise RuntimeError("Return code != OK can be used only with RETURN pipe.")
        if conn.fileno() == -1:
            print("[Server]: ERROR: " + f"Connection already closed: {conn} / {job} [{response}]")
            return
        print("[Server]: " + f"Responding with code: {code}, response: {response}, on pipe: {pipe}")
        match pipe:
            case StreamPipe.RETURN:
                server_response = ServerResponse(code=code, response=response)
                response_formatted = server_response.to_json()
                close = True
            case StreamPipe.STDOUT | StreamPipe.STDERR:
                response_formatted = response
                close = False
        conn.sendall(f"{pipe.value}:{len(response_formatted)}:".encode() + response_formatted.encode())
        if close:
            conn.shutdown(socket.SHUT_WR)
            conn.close()
            self.jobs.remove(job)

    # --------------------------------------------------------------------------
    # Shared helper functions.

    @staticmethod
    def get_runtime_dir(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
        return os.path.join(os.environ.get(runtime_env_name) or f"/run/user/{uid}", "catalystlab-root-helper")

    @staticmethod
    def get_socket_path(uid: int, runtime_env_name: str = "XDG_RUNTIME_DIR") -> str:
        """Get the path to the socket file for communication."""
        runtime_dir = RootHelperServer.get_runtime_dir(uid, runtime_env_name=runtime_env_name)
        return os.path.join(runtime_dir, "root-service-socket")

# ------------------------------------------------------------------------------
# Server runtime lifecycle.
# ------------------------------------------------------------------------------

def __init_server__():
    RootHelperServer.shared().start()

# ------------------------------------------------------------------------------
# Helper functions and types.
# ------------------------------------------------------------------------------

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

# ------------------------------------------------------------------------------
# @root_function decorator.
# ------------------------------------------------------------------------------

def root_function(func):
    """Registers a function to be allowed to call from client."""
    """Server version of this decorator just collects these functions into ROOT_FUNCTION_REGISTRY."""
    RootHelperServer.ROOT_FUNCTION_REGISTRY[func.__name__] = func

# ------------------------------------------------------------------------------
# Function processing with output handlers support.
# ------------------------------------------------------------------------------

class Job:
    """Groups thread with additional process that is spawned by this thread."""
    """This is used for redirecting output from this process to StreamWrapper."""
    """process is None at start and it is set later in handle_function_request if needed."""
    """thread is none at init, but it is set right after creating."""
    """This is because we need to pass the Job itself to the thread handler, so that it is able to set process later."""

    def __init__(self, server: RootHelperServer, conn: socket.socket):
        self.server = server
        self.conn = conn
        self.thread: threading.Thread | None = None
        self.process: multiprocessing.Process | None = None
        self.was_terminated = False

    def terminate(self, force: bool = False) -> Job | None:
        """Schedules Job process to terminate and gives it 5 seconds to finish."""
        """Termination itself happens on separate thread so that multiple can be stopped at once."""
        """If force flag is set, termination happens instantly on current thread, blocking it."""
        """Returns Job if process needed to be terminated or None if it was not running."""
        current_thread = threading.current_thread()
        if self.process is None or self.thread is current_thread or not self.process.is_alive():
            return None
        self.was_terminated = True
        try:
            self.server.respond(conn=self.conn, job=self, code=ServerResponseStatusCode.OK, pipe=StreamPipe.STDERR, response="Job will be terminated...")
        except Exception as e:
            pass
        if force:
            self.process.kill()
            self.process.join()
            self.server.respond(conn=self.conn, job=self, code=ServerResponseStatusCode.JOB_WAS_TERMINATED)
        else:
            cleanup_thread = threading.Thread(target=self.terminate_and_cleanup)
            cleanup_thread.start()
        return self

    def terminate_and_cleanup(self):
        if self.process is None or not self.process.is_alive():
            print("[Server]: " + "Process already stopped")
            return
        self.process.terminate()
        self.thread.join(timeout=5) # Allow time for graceful termination for 5s
        if self.process.is_alive():
            print("[Server]: ERROR: " + "Process did not terminate. Killing forcefully.")
            self.process.kill()
            self.process.join()
            self.thread.join()
            self.server.respond(conn=self.conn, job=self, code=ServerResponseStatusCode.JOB_WAS_TERMINATED)
        else:
            print("[Server]: " + "Process did terminate.")
            self.server.respond(conn=self.conn, job=self, code=ServerResponseStatusCode.JOB_WAS_TERMINATED)

    @staticmethod
    def join_all(jobs: List[Job], timeout: float):
        """Waits for all job threads to finish or until the timeout expires."""
        start_time = time.time()
        while (time.time() - start_time < timeout and not all(job.thread is not None and not job.thread.is_alive() for job in jobs)):
            time.sleep(0.1)

class StreamWrapper:
    def __init__(self, stream, pipe: StreamPipe):
        self.stream = stream
        self.pipe = pipe
    def write(self, message):
        if not message.strip():
            return  # Skip empty messages
        payload = json.dumps({
            "pipe": self.pipe.value,
            "message": message.rstrip("\n")
        })
        self.stream.write(payload + "\n")
        self.stream.flush()
    def flush(self):
        self.stream.flush()

def _run_function_with_streaming_output(conn, job: Job, func, args, kwargs, respond_callback) -> Any | None:
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
    job.process = proc
    proc.start()
    os.close(write_fd)

    def stream_reader(job: Job):
        with os.fdopen(read_fd, 'r') as pipe:
            for line in pipe:
                try:
                    data = json.loads(line)
                    respond_callback(conn=conn, job=job, code=ServerResponseStatusCode.OK, pipe=data["pipe"], response=data["message"])
                except json.JSONDecodeError as e:
                    stderr_callback(f"[parser error] {e}: {line}")

    reader_thread = threading.Thread(
        target=stream_reader,
        args=(job,)
    )
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
        stdout_wrapper = StreamWrapper(f, StreamPipe.STDOUT)
        stderr_wrapper = StreamWrapper(f, StreamPipe.STDERR)
        with redirect_stdout(stdout_wrapper), redirect_stderr(stderr_wrapper):
            try:
                result = func(*args, **kwargs)
                result_queue.put(result)
            except Exception as e:
                result_queue.put(e)

class WatchDog:
    def __init__(self, func: callable, ns: float = 5.0):
        if not callable(func):
            raise ValueError("func must be callable")
        if not isinstance(ns, float) or ns <= 0:
            raise ValueError("ns must be a positive number")
        self.func = func
        self.ns = ns
        self._stop_event = threading.Event()
        self._thread = None
        self._started = False
        self._lock = threading.Lock()

    def _run(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(self.ns)
            try:
                if not self._stop_event.is_set():
                    self.func()
            except Exception as e:
                print(f"Exception in WatchDog function: {e}")

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            if not self._started:
                return
            self._stop_event.set()
            if self._thread != threading.current_thread():
                self._thread.join()
            self._started = False
            self._thread = None

