#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, time, struct, signal, threading
import json, multiprocessing, select
from enum import Enum, auto
from functools import wraps
from dataclasses import dataclass, asdict
from typing import Any, List
from contextlib import redirect_stdout, redirect_stderr

class StreamPipe(Enum):
    RETURN = auto()
    STDOUT = auto()
    STDERR = auto()
    EVENTS = auto() # Sends special events to inform about call state etc.

class ServerResponseStatusCode(Enum):
    OK = 0
    JOB_WAS_TERMINATED = auto()
    JOB_ALREADY_SCHEDULED_FOR_TERMINATION = auto()
    JOB_NOT_FOUND = auto()
    COMMAND_EXECUTION_FAILED = auto()
    COMMAND_DECODE_FAILED = auto()
    COMMAND_UNSUPPORTED_FUNC = auto()
    AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS = auto()
    AUTHORIZATION_WRONG_UID = auto()
    AUTHORIZATION_WRONG_PID = auto()
    AUTHORIZATION_WRONG_TOKEN = auto()
    INITIALIZATION_ALREADY_DONE = auto()
    INITIALIZATION_NOT_DONE = auto()

class RootHelperServer:

    ROOT_FUNCTION_REGISTRY = {} # Registry for collecting root functions.
    _instance: RootHelperServer | None = None # Singleton shared instance.
    hide_logs = True
    use_client_watchdog = True

    # --------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self):
        self.is_running = False
        self.server_socket: socket.socket | None = None
        self.pid_lock: int | None = None
        self._jobs_lock = threading.Lock()
        self._jobs: List[Job] = []
        self.read_initial_session_data()
        self.validate_session()
        self.client_watchdog = WatchDog(lambda: self.check_client())
        devnull_read = open(os.devnull, 'r')
        sys.stdin = devnull_read
        if RootHelperServer.hide_logs:
            devnull_write = open(os.devnull, 'w')
            sys.stdout = devnull_write
            sys.stderr = devnull_write

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def read_initial_session_data(self):
        """Reads session uuid, token and runtime env from STDIN and os.env."""
        print("[Server]: " + "Welcome")
        self.uid: int = int(os.environ.get("PKEXEC_UID"))
        print("[Server]: " + "Please provide session token and runtime dir:")
        self.session_token, os.environ["CL_SERVER_RUNTIME_DIR"] = sys.stdin.readline().strip().split(' ', 1)
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

    # --------------------------------------------------------------------------
    # Session lifecycle:

    def start(self):
        """Run the root helper server."""
        if self.is_running:
            print("[Server]: INFO: " + "Server is already running. Ignoring start().")
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

        self.client_watchdog.stop()

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
        Job.join_all(jobs_to_wait, timeout=5 + 1)
        print("[Server]: Waiting done")
        # Force terminate remaining jobs and clear jobs list
        for job in self.jobs[:]:
            safe_execute(job.terminate, instant=True)
        self.clear_jobs(keep=called_by_job)
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
        self.pid_lock = None
        os._exit(0)

    def check_client(self):
        """Checks if client is still running, and if not, stops the server."""
        if not self.pid_lock:
            return
        try:
            os.kill(self.pid_lock, 0)
        except ProcessLookupError:
            self.stop()
        except:
            pass

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
        try:
            while self.is_running:
                conn, _ = server.accept()
                print("[Server]: " + "Accepting connection...")
                job = Job(
                    server=self, conn=conn,
                    session_token=session_token, allowed_uid=allowed_uid
                )
                self.add_job(job)
                job.start()
        except Exception as e:
            print("[Server]: ERROR: " + f"Error accepting connection: {e}")
        finally:
            self.stop()

    # --------------------------------------------------------------------------
    # Jobs management (With thread safety built in).

    @property
    def jobs(self):
        with self._jobs_lock:
            return self._jobs
    def add_job(self, job: Job):
        with self._jobs_lock:
            self._jobs.append(job)
    def remove_job(self, job: Job):
        with self._jobs_lock:
            if job in self._jobs:
                self._jobs.remove(job)
    def get_job_by_call_id(self, call_id: uuid.UUID) -> Job | None:
        with self._jobs_lock:
            return next((j for j in self._jobs if j.call_id == call_id), None)
    def clear_jobs(self, keep: Job | None = None):
        with self._jobs_lock:
            self._jobs = [keep] if keep and keep in self._jobs else []

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

class StreamPipeEvent(Enum):
    CALL_WILL_TERMINATE = auto()

class ServerCommand(str, Enum):
    EXIT = "[EXIT]"
    HANDSHAKE = "[HANDSHAKE]"
    PING = "[PING]"
    CANCEL_CALL = "[CANCEL_CALL]"

    @property
    def function_name(self) -> str:
        return self.value

    @property
    def show_in_running_tasks(self) -> bool:
        match self:
            case ServerCommand.HANDSHAKE | ServerCommand.EXIT:
                return True
            case ServerCommand.PING | ServerCommand.CANCEL_CALL:
                return False
        raise RuntimeError("Unsupported ServerCommand case")

    def timeout(self) -> float | None:
        return 5.0

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
        data = json.loads(json_str)
        return cls(
            data["function"],
            *data.get("args", []),
            **data.get("kwargs", {})
        )

    @property
    def show_in_running_tasks(self):
        return True

    def timeout(self) -> float | None:
        return None

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

    def __init__(self, server: RootHelperServer, conn: socket.socket, session_token: str, allowed_uid: int):
        self.server = server
        self.conn = conn
        self.thread: threading.Thread | None = None
        self.process: multiprocessing.Process | None = None
        self.mark_terminated = False
        self.call_id: uuid | None = None # Set when handle_connection is called
        self.thread_lock = threading.Lock()
        self.thread = threading.Thread(
            target=self.handle_connection,
            args=(session_token, allowed_uid)
        )

    def start(self):
        self.thread.start()

    def terminate(self, instant: bool = False, completion: callable | None = None) -> Job | None:
        """Schedules Job process to terminate and gives it 5 seconds to finish."""
        """Termination itself happens on separate thread so that multiple can be stopped at once."""
        """If instant flag is set, termination happens instantly on current thread, blocking it."""
        """Returns Job if process needed to be terminated or None if it was not running."""
        if self.mark_terminated:
            if self.cleanup_thread and self.cleanup_thread.is_alive():
                if instant:
                    self.cleanup_thread.join()
                if completion:
                    completion(True)  # Indicates it was already being terminated
                return self
            else:
                if completion:
                    completion(False)  # Indicates it was already terminated and no cleanup needed
                return None
        current_thread = threading.current_thread()
        if self.process is None or self.thread is current_thread or not self.process.is_alive():
            if completion:
                completion(False)
            return None
        self.mark_terminated = True
        try:
            self.respond(pipe=StreamPipe.EVENTS, response=StreamPipeEvent.CALL_WILL_TERMINATE)
        except Exception as e:
            pass
        if instant:
            self.process.kill()
            self.process.join()
            self.respond(code=ServerResponseStatusCode.JOB_WAS_TERMINATED)
            if completion:
                completion(True)
        else:
            self.cleanup_thread = threading.Thread(target=self.terminate_and_cleanup, args=(completion,))
            self.cleanup_thread.start()
        return self

    def terminate_and_cleanup(self, completion: callable | None = None):
        if self.process is None or not self.process.is_alive():
            print("[Server]: " + "Process already stopped")
            if completion:
                completion(False)
            return
        self.process.terminate()
        self.thread.join(timeout=3) # Allow time for graceful termination for 3s
        if self.process.is_alive():
            print("[Server]: WARNING: " + "Process did not terminate. Killing forcefully.")
            self.process.kill()
            self.process.join()
            self.thread.join()
        else:
            print("[Server]: " + "Process did terminate.")
        self.respond(code=ServerResponseStatusCode.JOB_WAS_TERMINATED)
        if completion:
            completion(True)

    @staticmethod
    def join_all(jobs: List[Job], timeout: float):
        """Waits for all job threads to finish or until the timeout expires."""
        start_time = time.time()
        while (time.time() - start_time < timeout and not all(job.thread is not None and not job.thread.is_alive() for job in jobs)):
            time.sleep(0.1)

    def handle_connection(self, session_token: str, allowed_uid: int):
        """Handle a single client connection in a separate thread."""
        try:
            # Validate peer credentials:
            try:
                ucred = self.conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                pid, uid, gid = struct.unpack("3i", ucred)
            except Exception:
                self.respond(code=ServerResponseStatusCode.AUTHORIZATION_FAILED_TO_GET_CONNECTION_CREDENTIALS)
                return

            if uid != allowed_uid:
                self.respond(code=ServerResponseStatusCode.AUTHORIZATION_WRONG_UID)
                return
            if self.server.pid_lock is not None and self.server.pid_lock != pid:
                self.respond(code=ServerResponseStatusCode.AUTHORIZATION_WRONG_PID)
                return

            # Receive request data:
            data = self.conn.recv(4096).decode().strip()
            if not data.startswith(session_token):
                self.respond(code=ServerResponseStatusCode.AUTHORIZATION_WRONG_TOKEN)
                return
            full_payload = data[len(session_token) + 1:]
            if " " not in full_payload:
                self.respond(code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)
                return
            call_id_str, request_type, payload = full_payload.split(" ", 2)
            self.call_id = uuid.UUID(call_id_str)

            # Process request:
            match request_type:
                case "command":
                    self.handle_command_request(pid, payload)
                case "function":
                    self.handle_function_request(pid, payload)
                case _:
                    self.respond(code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

        except Exception as e:
            print("[Server]: ERROR: " + f"Unexpected error in connection handler: {e}")
            job.conn.close()

    def handle_command_request(self, pid: int, payload: str):
        print("[Server]: " + "Processing command request")
        try:
            parts = payload.split(" ", 1)
            cmd_type = parts[0]
            cmd_value = parts[1] if len(parts) > 1 else None
            cmd_enum = ServerCommand(cmd_type)
            print("[Server]: " + f"Command: {cmd_enum}")
            if self.server.pid_lock is None and cmd_enum != ServerCommand.HANDSHAKE:
                self.respond(code=ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
                return
            match cmd_enum:
                case ServerCommand.EXIT:
                    self.respond(pipe=StreamPipe.STDOUT, response="Exiting...")
                    self.server.stop(called_by_job=self, after_jobs_cleaned = lambda: self.respond(response="Exited"))
                case ServerCommand.PING:
                    self.respond(response="PONG")
                case ServerCommand.HANDSHAKE:
                    if self.server.pid_lock is None:
                        self.server.pid_lock = pid
                        if RootHelperServer.use_client_watchdog:
                            self.server.client_watchdog.start()
                        self.respond(response="Initialization succeeded")
                    else:
                        self.respond(code=ServerResponseStatusCode.INITIALIZATION_ALREADY_DONE, response="Initialization already finished")
                case ServerCommand.CANCEL_CALL:
                    # Handle call cancellation
                    call_id=uuid.UUID(cmd_value)
                    job_to_cancel = self.server.get_job_by_call_id(call_id)
                    if job_to_cancel:
                        def completion(did_schedule_for_termination: bool):
                            if did_schedule_for_termination:
                                self.respond(response="Job terminated")
                            else:
                                self.respond(code=ServerResponseStatusCode.JOB_ALREADY_SCHEDULED_FOR_TERMINATION, response="Job was already terminated or scheduled for termination")
                        job_to_cancel.terminate(completion=completion)
                    else:
                        self.respond(code=ServerResponseStatusCode.JOB_NOT_FOUND)
        except ValueError as e:
            self.respond(code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def handle_function_request(self, pid: int, payload: str):
        print("[Server]: " + "Processing function request")
        if self.server.pid_lock is None:
            self.respond(code=ServerResponseStatusCode.INITIALIZATION_NOT_DONE)
            return
        try:
            func_struct = ServerFunction.from_json(payload)
            print("[Server]: " + f"Function: {func_struct.function_name} ({func_struct.args}) ({func_struct.kwargs})")
            if func_struct.function_name not in RootHelperServer.ROOT_FUNCTION_REGISTRY:
                self.respond(code=ServerResponseStatusCode.COMMAND_UNSUPPORTED_FUNC, response=f"{func_struct.function_name}")
            else:
                try:
                    result = OutputCapture.run_function_with_streaming_output(
                        self,
                        RootHelperServer.ROOT_FUNCTION_REGISTRY[func_struct.function_name],
                        func_struct.args,
                        func_struct.kwargs
                    )
                    if not self.mark_terminated:
                        self.respond(response=result)
                except Exception as e:
                    if not self.mark_terminated:
                        self.respond(code=ServerResponseStatusCode.COMMAND_EXECUTION_FAILED, response=str(e))
        except ValueError:
            if not self.mark_terminated:
                self.respond(code=ServerResponseStatusCode.COMMAND_DECODE_FAILED)

    def respond(self, code: ServerResponseStatusCode = ServerResponseStatusCode.OK, pipe: StreamPipe | int = StreamPipe.RETURN, response: str | StreamPipeEvent | None = None):
        # Final response, returning the result of function called.
        # Closes connection. Does not contain stdout and stderr produced by the function, just the returned value if any.
        with self.thread_lock:
            if isinstance(pipe, int):
                # If pipe was passed by ID, convert it back to pipe object
                pipe = StreamPipe(pipe)
            if code != ServerResponseStatusCode.OK and pipe != StreamPipe.RETURN:
                raise RuntimeError("Return code != OK can be used only with RETURN pipe.")
            if self.conn.fileno() == -1:
                print("[Server]: ERROR: " + f"Connection already closed: {self.conn} / {self} [{response}]")
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
                case StreamPipe.EVENTS:
                    response_formatted = str(response.value)
                    close = False
            try:
                self.conn.sendall(f"{pipe.value}:{len(response_formatted)}:".encode() + response_formatted.encode())
            except Exception as e:
                print("[Server]: ERROR: " + f"{e}")
            finally:
                if close:
                    self.conn.shutdown(socket.SHUT_WR)
                    # Wait for ACK from client
                    try:
                        # Wait for socket to be ready to read
                        readable, writable, errored = select.select([self.conn], [], [], 5)
                        if not readable:
                            print("Socket not readable yet")
                        self.conn.settimeout(5)
                        if self.conn.recv(3) != b"ACK":
                            print(f"[Server]: Warning: Incorrect ACK response from client: {data.decode()}")
                    except Exception as e:
                        print(f"[Server]: Warning: Failed to receive ACK from client: {e}")
                    finally:
                        self.conn.close()
                        self.server.remove_job(self)

class OutputCapture:

    @staticmethod
    def run_function_with_streaming_output(job: Job, func, args, kwargs) -> Any | None:
        read_fd, write_fd = os.pipe()
        result_queue = multiprocessing.Queue()

        proc = multiprocessing.Process(target=OutputCapture._run_and_capture_streams, args=(func, args, kwargs, write_fd, result_queue))
        job.process = proc
        proc.start()
        os.close(write_fd)

        def stream_reader(job: Job):
            with os.fdopen(read_fd, 'r') as pipe:
                for line in pipe:
                    try:
                        pipe_id_str, message = line.rstrip("\n").split(":", 1)
                        pipe_id = int(pipe_id_str)
                        job.respond(pipe=pipe_id, response=message)
                    except ValueError as e:
                        print(f"[Server]: Error parsing line '{line}': {e}")

        reader_thread = threading.Thread(target=stream_reader, args=(job,))
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

    @staticmethod
    def _run_and_capture_streams(func, args, kwargs, write_fd, result_queue):
        # Create two pipes for capturing stdout and stderr separately
        # Never call this method directly, it needs to be spawned as multiprocessing.Process through _run_function_with_streaming_output.
        stdout_r, stdout_w = os.pipe()
        stderr_r, stderr_w = os.pipe()

        # Duplicate write ends over original stdout/stderr
        os.dup2(stdout_w, 1)
        os.dup2(stderr_w, 2)
        os.close(stdout_w)
        os.close(stderr_w)
        sys.stdout = open(1, 'w', buffering=1, encoding='utf-8', errors='replace')
        sys.stderr = open(2, 'w', buffering=1, encoding='utf-8', errors='replace')

        def forward_stream(read_fd, pipe_id):
            with os.fdopen(read_fd, 'r') as reader, os.fdopen(write_fd, 'w', buffering=1) as writer:
                for line in reader:
                    line_cleaned = line.rstrip("\n")
                    if line_cleaned:
                        payload = f"{pipe_id}:{line_cleaned}"
                        writer.write(payload + "\n")
                        writer.flush()

        # Start background threads to forward raw output as JSON
        threading.Thread(target=forward_stream, args=(stdout_r, StreamPipe.STDOUT.value), daemon=True).start()
        threading.Thread(target=forward_stream, args=(stderr_r, StreamPipe.STDERR.value), daemon=True).start()

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

