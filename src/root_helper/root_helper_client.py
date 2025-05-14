#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal
import threading, json, inspect, re, select
from enum import Enum
from typing import Any, Callable
from functools import wraps
from gi.repository import Gio, GLib
from dataclasses import dataclass, field
from .runtime_env import RuntimeEnv
from .app_events import AppEvents, app_event_bus
from .settings import *
from .root_helper_server import ServerCommand, ServerFunction
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .root_helper_server import RootHelperServer, StreamPipe, StreamPipeEvent, WatchDog

class RootHelperClient:

    ROOT_FUNCTION_REGISTRY = {} # Registry for collecting root functions.
    _instance: RootHelperClient | None = None # Singleton shared instance.
    use_server_watchdog = True # Enable for release. Might disable for debugging.

    # --------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self):
        self.event_bus: EventBus[RootHelperClientEvents] = EventBus[RootHelperClientEvents]()
        self.socket_path = RootHelperServer.get_socket_path(os.getuid())
        self.main_process = None
        self.running_actions: List[ServerCall] = []
        self.token = None
        self.keep_unlocked = Settings.current().keep_root_unlocked
        self.server_watchdog = WatchDog(lambda: self.ping_server())
        self.set_request_status_lock = threading.RLock()
        Settings.current().event_bus.subscribe(
            SettingsEvents.KEEP_ROOT_UNLOCKED_CHANGED,
            self.keep_root_unlocked_changed
        )

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    is_server_process_running = property(
        # Note: Use with self.
        fget=lambda self: getattr(self, "_is_server_process_running", False),
        fset=lambda self, value: (
            setattr(self, "_is_server_process_running", value),
            self.event_bus.emit(RootHelperClientEvents.CHANGE_ROOT_ACCESS, value),
            (not value and self.server_watchdog.stop() or None)
        )[0]
    )

    def server_handshake_established(self) -> bool:
        return self.token is not None

    # --------------------------------------------------------------------------
    # Server lifecycle management:

    def start_root_helper(self) -> bool:
        """Start the root helper process."""
        if self.is_server_process_running:
            print("Root helper is already running.")
            return True
        with self.set_request_status_lock:
            if self.running_actions:
                print("Error: Some actions are not finished yet.")
                return False
        try:
            if os.path.exists(self.socket_path):
                os.remove(self.socket_path)
            self.is_server_process_running = True
            token = str(uuid.uuid4())
            self.token = None

            helper_host_path = self.extract_root_helper_to_run_user(os.getuid())
            xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
            if RuntimeEnv.current() == RuntimeEnv.FLATPAK:
                cmd_prefix = ["flatpak-spawn", "--host"]
            else:
                cmd_prefix = []
            cmd_authorize = ["pkexec"]
            exec_call = cmd_prefix + cmd_authorize + [helper_host_path]

            # Start pkexec and pass token via stdin
            # Note: This is flatpak-spawn process, not server process itself.
            self.main_process = subprocess.Popen(exec_call, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(0.5) # This helps prevent an issue with server not being ready to read from stdin at start. Probably not the best solution, might find better one.

            # Waits until main_process finishes, to see if it was closed with error.
            def monitor_error_codes():
                if self.main_process.wait() != 0 and self.is_server_process_running:
                    print("[Server process]! Authorization failed or cancelled.")
                    self.is_server_process_running = False
            threading.Thread(target=monitor_error_codes, daemon=True).start()

            # Send token and runtime dir
            # TODO: This method of passing sometimes fails, leaving server waiting for that data.
            self.main_process.stdin.write(token.encode() + b' ')
            self.main_process.stdin.write(xdg_runtime_dir.encode() + b'\n')
            self.main_process.stdin.flush()

            # Wait for the server initialization to return result.
            if self.initialize_server_connectivity(token=token):
                self.token = token
                return True
            else:
                raise RuntimeError("Failed to initialize connection.")
        except Exception as e:
            print(f"FAILED TO START SERVER: {e}")
            self.is_server_process_running = False
            self.main_process = None
            return False

    def stop_root_helper(self):
        """Stop the root helper process."""
        if not self.is_server_process_running:
            print(f"Server is already stopped")
            return
        try:
            token = self.token
            self.token = None
            self.server_watchdog.stop()
            self.send_request(ServerCommand.EXIT, allow_auto_start=False, asynchronous=True, completion_handler=self.clean_unfinished_jobs, token=token)
        except Exception as e:
            print("[Server process]: Warning: Failed to send EXIT command. Some process might be left working orphined.")
            self.clean_unfinished_jobs()
        finally:
            self.is_server_process_running = False
            self.main_process = None
            print("[Server process]: Closed.")

    def clean_unfinished_jobs(self, exit_result: callable | None = None):
        with self.set_request_status_lock:
            for call in self.running_actions[:]:
                print(f"[Server process]: Warning: Call {call} was left orphined.")
                self.set_request_status(call, False)

    def ping_server(self):
        try:
            self.send_request(ServerCommand.PING, allow_auto_start=False)
        finally:
            pass # Server disconnection is handled in send_request.

    def extract_root_helper_to_run_user(self, uid: int) -> str:
        """Extracts root helper server code and appends root functions."""
        # Runtime directory where the generated server code will be placed.
        runtime_dir = RootHelperServer.get_runtime_dir(uid)
        output_path = os.path.join(runtime_dir, "root-helper-server.py")

        # Ensure the directory exists
        os.makedirs(runtime_dir, exist_ok=True)

        # If the file already exists, remove it
        if os.path.exists(output_path):
            os.remove(output_path)

        # Load the embedded server code from resources
        data = Gio.resources_lookup_data('/com/damiandudycz/CatalystLab/root_helper/root_helper_server.py', Gio.ResourceLookupFlags.NONE)
        server_code = data.get_data().decode()

        # Collect the root functions (dynamically registered)
        injected_functions = self.collect_root_function_sources()

        # Combine the server code with the dynamically injected functions
        full_code = server_code + "\n\n" + injected_functions

        # Add the `if __name__ == "__main__":` block to run the server
        # This needs to be added bellow dynamic functions.
        full_code += """\n\nif __name__ == "__main__":\n    __init_server__()"""

        # Write the full code to the output file
        with open(output_path, "w") as f:
            f.write(full_code)

        # Make the script executable
        os.chmod(output_path, 0o700)

        return output_path

    def collect_root_function_sources(self) -> str:
        """Returns all registered root function sources as a single"""
        """Python string, with @root_function decorators removed."""
        if not RootHelperClient.ROOT_FUNCTION_REGISTRY:
            return ""
        sources = []
        for func in RootHelperClient.ROOT_FUNCTION_REGISTRY.values():
            try:
                source = inspect.getsource(func)
                sources.append(source.strip())
            except OSError:
                print(f"Warning: could not get source for function {func.__name__}")
        return "\n\n# ---- Injected root functions ----\n\n" + "\n\n".join(sources)

    def initialize_server_connectivity(self, token: str, timeout: int | None = None) -> bool:
        """Wait for the server to send an 'OK' message indicating it's ready."""
        start_time = time.time()
        while timeout is None or time.time() - start_time < timeout:
            # If server was never started or killed before initialization finished.
            if not self.is_server_process_running:
                break
            try:
                response = self.send_request(ServerCommand.HANDSHAKE, allow_auto_start=False, token=token)
                if RootHelperClient.use_server_watchdog:
                    self.server_watchdog.start()
                return response.code == ServerResponseStatusCode.OK
            except ServerCallError as e:
                if e == ServerCallError.SERVER_NOT_RESPONDING:
                    time.sleep(1)
                    continue
                else:
                    break
            except Exception as e:
                print(f"Unexpected error while waiting for server: {e}")
                break
        return False

    def ensure_server_ready(self, allow_auto_start=True) -> bool:
        """Ensure the root helper server is running and the socket is"""
        """available. If allowed automatically start the server (This should"""
        """be used only by handshake request)."""
        if self.is_server_process_running:
            return os.path.exists(self.socket_path)
        elif allow_auto_start:
            print("[Root helper is not running, attempting to start it.]")
            return self.start_root_helper()
        else:
            return False

    # --------------------------------------------------------------------------
    # Handling requests to server / root functions:

    def send_request(
        self,
        request: ServerCommand | ServerFunction,
        command_value: str | None = None,
        allow_auto_start: bool = True,
        handler: callable = None,
        asynchronous: bool = False,
        raw: bool = False,
        completion_handler: callable = None,
        token: str | None = None
    ) -> ServerResponse | ServerCall: # For async always returns ServerCall or throws.
        """Send a command to the root helper server."""
        if request != ServerCommand.EXIT and not self.ensure_server_ready(allow_auto_start):
            if request != ServerCommand.HANDSHAKE and self.is_server_process_running:
                print("[Server process] Server communication broke.")
                self.stop_root_helper() # This should never happen.
            raise ServerCallError.SERVER_NOT_RESPONDING
        if request == ServerCommand.EXIT and not self.ensure_server_ready(allow_auto_start=False):
            raise ServerCallError.SERVER_NOT_RESPONDING

        # Prepare message and type
        if isinstance(request, ServerFunction):
            message, request_type = request.to_json(), "function"
        elif isinstance(request, ServerCommand):
            request_type = "command"
            message = request.value if command_value is None else request.value + " " + command_value
        else:
            raise TypeError("command must be either a ServerCommand or ServerFunction instance")
        def worker(call: ServerCall) -> ServerResponse:
            try:
                used_token = token if token is not None else self.token
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.connect(self.socket_path)
                    conn.sendall(f"{used_token} {call.call_id} {request_type} {message}".encode())
                    current_message_type: StreamPipe | None = None
                    current_chars_left: int | None = None
                    current_buffer = ""
                    response_string: str = None

                    def handle_message(message_type: StreamPipe, content: str):
                        nonlocal response_string
                        match message_type:
                            case StreamPipe.RETURN:
                                response_string = content
                            case StreamPipe.STDOUT:
                                call.output_append(content)
                                if handler:
                                    GLib.idle_add(handler, content)
                            case StreamPipe.STDERR:
                                call.output_append(content)
                                if handler:
                                    GLib.idle_add(handler, content)
                            case StreamPipe.EVENTS:
                                # Handle EVENTS pipe:
                                try:
                                    event = StreamPipeEvent(int(content))
                                    match event:
                                        case StreamPipeEvent.CALL_WILL_TERMINATE:
                                            call.mark_terminated()
                                        case _:
                                           print(f"[Server process]: Warning: Received unsupported event: {event}")
                                except Exception as e:
                                       print(f"[Server process]: Warning: Failed to process event: {content}")

                    # Processing data returned over socket and combining them into messages.
                    # Format: <StreamPipe.raw>:<Length>:<Message>. eq: 0:11:Hello World
                    def process_fragment(fragment: str):
                        nonlocal current_message_type, current_chars_left, current_buffer
                        if current_message_type is None:
                            current_message_type, current_chars_left, start_buffer = fragment.split(":", 2)
                            current_message_type = StreamPipe(int(current_message_type))
                            current_chars_left = int(current_chars_left)
                            process_fragment(start_buffer)
                        else:
                            append_fragment = fragment[:current_chars_left]
                            remaining_fragment = fragment[current_chars_left:]
                            current_buffer += append_fragment
                            current_chars_left -= len(append_fragment)
                            if current_chars_left == 0:
                                handle_message(current_message_type, current_buffer)
                                current_buffer = ""
                                current_message_type = None
                                if remaining_fragment:
                                    process_fragment(remaining_fragment)

                    while (chunk := conn.recv(4096)):
                        fragment = chunk.decode()
                        process_fragment(fragment)

                    # Send ACK
                    try:
                        readable, writable, errored = select.select([], [conn], [], 5)
                        if writable:
                            conn.sendall(b"ACK")
                            conn.shutdown(socket.SHUT_WR)
                        else:
                            print("[Server process]: Warning: Failed to send ACK back. Socket not ready for writing.")
                    except Exception as e:
                        print(f"[Server process]: Warning: Failed to send ACK back: {e}")

                    server_response = ServerResponse.from_json(response_string)
                    if completion_handler:
                        result = server_response if raw else server_response.response
                        GLib.idle_add(completion_handler, result)
            except Exception as e:
                server_response = ServerResponse(code=ServerResponseStatusCode.COMMAND_EXECUTION_FAILED)
            finally:
                if request.show_in_running_tasks:
                    self.set_request_status(call, False)
                return server_response

        if asynchronous:
            async_call = ServerCall(request=request, thread=None, client=self)
            thread = threading.Thread(target=worker, args=(async_call,), daemon=True)
            async_call.thread = thread
            if request.show_in_running_tasks:
                self.set_request_status(async_call, True)
            thread.start()
            return async_call
        else:
            sync_call = ServerCall(request=request, thread=None, client=self)
            if request.show_in_running_tasks:
                self.set_request_status(sync_call, True)
            return worker(call=sync_call)

    def call_root_function(
        self,
        func_name: str,
        *args,
        handler: callable = None,
        asynchronous: bool = False,
        raw: bool = False,
        completion_handler: callable = None,
        **kwargs
    ) -> Any | ServerResponse | ServerCall:
        """Calls function registered in ROOT_FUNCTION_REGISTRY with @root_function by its name on the server."""
        function = ServerFunction(func_name, *args, **kwargs)
        server_response = self.send_request(
            function,
            handler=handler,
            asynchronous=asynchronous,
            raw=raw,
            completion_handler=completion_handler
        )
        if asynchronous or raw: # Returns ServerCall for async or whole structure directly for sync_raw
            return server_response
        if server_response.code == ServerResponseStatusCode.OK:
            return server_response.response
        else:
            raise RuntimeError(f"Root function error: {server_response.response}")

    def set_request_status(self, call: ServerCall, in_progress: bool):
        with self.set_request_status_lock:
            if in_progress:
                self.running_actions.append(call)
                self.event_bus.emit(RootHelperClientEvents.ROOT_REQUEST_STATUS, self, call, True)
            else:
                self.running_actions.remove(call)
                self.event_bus.emit(RootHelperClientEvents.ROOT_REQUEST_STATUS, self, call, False)
            if not self.running_actions and not self.keep_unlocked and self.server_handshake_established() and self.is_server_process_running:
                self.stop_root_helper()

    def keep_root_unlocked_changed(self, value: bool):
        self.keep_unlocked = value

@final
class RootHelperClientEvents(Enum):
    CHANGE_ROOT_ACCESS = auto() # root_helper_client unlocked / locked root access
    ROOT_REQUEST_STATUS = auto() # calls when state of root_function is changed (in progress / finished)

@final
class ServerCallEvents(Enum):
    NEW_OUTPUT_LINE = auto() # new line added to collected output
    CALL_WILL_TERMINATE = auto()

@dataclass
class ServerCall:
    """Captures details about ongoing server call."""
    """Can be used to join the thread later or send cancel request for single request."""
    request: ServerCommand | ServerFunction
    thread: threading.Thread | None
    client: RootHelperClient
    call_id: uuid.UUID = field(default_factory=uuid.uuid4)
    terminated: bool = False # Mark as terminated. Might still be terminating.
    output: List[str] = field(default_factory=list) # Contains output lines from stdout and stderr
    output_lock: threading.Lock = field(default_factory=threading.Lock)
    event_bus: EventBus[ServerCallEvents] = field(default_factory=lambda: EventBus[ServerCallEvents]())

    @property
    def is_cancellable(self) -> bool:
        """Only ServerFunctions are cancellable"""
        return isinstance(self.request, ServerFunction)

    def cancel(self):
        """Sends CANCEL_CALL <ID> to server. Can be used only with async calls that already started."""
        if not self.is_cancellable:
            print("[Server process]: Warning: Tried to cancel a call that is not cancellable")
            return
        if self.thread:
            self.client.send_request(ServerCommand.CANCEL_CALL, command_value=str(self.call_id), allow_auto_start=False, asynchronous=True)

    def output_append(self, line: str):
        with self.output_lock:
            self.output.append(line)
            self.event_bus.emit(ServerCallEvents.NEW_OUTPUT_LINE, line)

    def get_output(self) -> List[str]:
        with self.output_lock:
            return self.output

    def mark_terminated(self):
        self.terminated = True
        self.event_bus.emit(ServerCallEvents.CALL_WILL_TERMINATE)

# ------------------------------------------------------------------------------
# @root_function decorator.
# ------------------------------------------------------------------------------

def root_function(func):
    """Registers a function and replaces it with a proxy that calls the root server."""
    """All these calls can throw in case server call fails to start."""
    RootHelperClient.ROOT_FUNCTION_REGISTRY[func.__name__] = func
    @wraps(func)
    def proxy_function(*args, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            **kwargs
        )
    def _async(handler: callable | None = None, completion_handler: callable | None = None, *args, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            handler=handler,
            asynchronous=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _raw(*args, completion_handler: callable | None = None, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            raw=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _async_raw(handler: callable | None = None, completion_handler: callable | None = None, *args, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            handler=handler,
            asynchronous=True,
            raw=True,
            completion_handler=completion_handler,
            **kwargs
        )
    # Attach variants
    proxy_function._async = _async
    proxy_function._raw = _raw
    proxy_function._async_raw = _async_raw
    return proxy_function

# ------------------------------------------------------------------------------
# Helper functions and types.
# ------------------------------------------------------------------------------

class ServerCallError(Exception):
    """Custom exception with predefined error codes and messages."""
    def __init__(self, error_code: int, message: str):
        # You can pass either a predefined error code or a custom message
        self.error_code = error_code
        self.message = message
        super().__init__(f"Error code {error_code}: {message}")
    def __str__(self):
        return f"Server error (code={self.error_code}): {self.message}"
# Define error codes and their corresponding messages as class variables
ServerCallError.SERVER_NOT_RESPONDING = ServerCallError(1, "The server is not responding.")
