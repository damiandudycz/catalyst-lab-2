#!/usr/bin/env python3
from __future__ import annotations
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal
import threading, json, inspect, re
from enum import Enum
from typing import Optional, Any
from functools import wraps
from gi.repository import Gio
from .environment import RuntimeEnv
from .root_helper_server import ServerCommand, ServerFunction
from .root_helper_server import ServerResponse, ServerResponseStatusCode
from .root_helper_server import RootHelperServer, ServerMessageType
from .app_events import AppEvents, app_event_bus
from .settings import *

class RootHelperClient:

    ROOT_FUNCTION_REGISTRY = {} # Registry for collecting root functions.
    _instance: RootHelperClient | None = None # Singleton shared instance.

    # --------------------------------------------------------------------------
    # Lifecycle:

    def __init__(self):
        self.socket_path = RootHelperServer.get_socket_path(os.getuid())
        self.main_process = None
        self.running_actions = []
        self.token = None
        self.keep_unlocked = Settings.current.keep_root_unlocked
        Settings.current.event_bus.subscribe(
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
            app_event_bus.emit(AppEvents.CHANGE_ROOT_ACCESS, value)
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
        if self.running_actions:
            print("Error: Some actions are not finished yet.")
            return False
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
        self.main_process = subprocess.Popen(exec_call, stdin=subprocess.PIPE)

        # Waits until main_process finishes, to see if it was closed with error.
        def monitor_error_codes():
            if self.main_process.wait() != 0 and self.is_server_process_running:
                print("[Server process]! Authorization failed or cancelled.")
                self.is_server_process_running = False
        threading.Thread(target=monitor_error_codes, daemon=True).start()

        # Send token and runtime dir
        self.main_process.stdin.write(token.encode() + b'\n')
        self.main_process.stdin.flush()
        self.main_process.stdin.write(xdg_runtime_dir.encode() + b'\n')
        self.main_process.stdin.flush()

        # Wait for the server initialization to return result.
        if self.initialize_server_connectivity(token=token):
            self.token = token
            return True
        else:
            print("[Server process]! Server failed to initialize. Cleaning...")
            if self.main_process and self.main_process.poll() is None:
                print("[Server process]: Terminating main process...")
                self.main_process.terminate()
                try:
                    self.main_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print("[Server process]: Kill unresponsive main process...")
                    self.main_process.kill()
                    self.main_process.wait()
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
            # TODO: If this finished and there are still running processes, remove them from list, and print a warning that they failed to be killed.
            self.send_request(ServerCommand.EXIT, allow_auto_start=False, asynchronous=True, token=token)
        except Exception as e:
            print(f"Failed to stop root helper: {e}")
        finally:
            self.is_server_process_running = False
            if self.main_process and self.main_process.poll() is None:
                # Note: This only kills main process if it was left.
                # pkexec or flatpak-spawn.
                # Server needs to quit by itself after [EXIT] command.
                # That's why it's safe to kill this even before EXIT completes.
                self.main_process.kill()
                self.main_process.wait()
            self.main_process = None
            print("[Server process]: Closed.")

    def extract_root_helper_to_run_user(self, uid: int) -> str:
        """Extracts root helper server code and appends root functions."""
        import os

        # Runtime directory where the generated server code will be placed.
        runtime_dir = RootHelperServer.get_runtime_dir(uid)
        output_path = os.path.join(runtime_dir, "root-helper-server.py")

        # Ensure the directory exists
        os.makedirs(runtime_dir, exist_ok=True)

        # If the file already exists, remove it
        if os.path.exists(output_path):
            os.remove(output_path)

        # Load the embedded server code from resources
        resource_path = "/com/damiandudycz/CatalystLab/root_helper/root_helper_server.py"
        resource = Gio.Resource.load("/app/share/catalystlab/catalystlab.gresource")
        data = resource.lookup_data(resource_path, Gio.ResourceLookupFlags.NONE)
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
        print(f"Server initialization failed.")
        if self.main_process and self.main_process.poll() is None:
            self.main_process.terminate()
            try:
                self.main_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.main_process.kill()
                self.main_process.wait()
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
        allow_auto_start: bool = True,
        handler: callable = None,
        asynchronous: bool = False,
        raw: bool = False,
        completion_handler: callable = None,
        token: str | None = None
    ) -> ServerResponse | threading.Thread:
        """Send a command to the root helper server."""
        if request != ServerCommand.EXIT and not self.ensure_server_ready(allow_auto_start):
            if request != ServerCommand.HANDSHAKE and self.is_server_process_running:
                self.stop_root_helper()
            raise ServerCallError.SERVER_NOT_RESPONDING
        # Prepare message and type
        if isinstance(request, ServerFunction):
            message, request_type = request.to_json(), "function"
        elif isinstance(request, ServerCommand):
            message, request_type = request.value, "command"
        else:
            raise TypeError("command must be either a ServerCommand or ServerFunction instance")
        def worker() -> ServerResponse:
            try:
                used_token = token if token is not None else self.token
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(self.socket_path)
                    s.sendall(f"{used_token} {request_type} {message}".encode())
                    current_message_type: ServerMessageType | None = None
                    current_chars_left: int | None = None
                    current_buffer = ""
                    response_string: str = None

                    def handle_message(message_type: ServerMessageType, content: str):
                        nonlocal response_string
                        match message_type:
                            case ServerMessageType.RETURN:
                                response_string = content
                            case ServerMessageType.STDOUT:
                                if handler:
                                    handler(content)
                            case ServerMessageType.STDERR:
                                if handler:
                                    handler(content)

                    # Processing data returned over socket and combining them into messages.
                    # Format: <ServerMessageType.raw>:<Length>:<Message>. eq: 0:11:Hello World
                    def process_fragment(fragment: str):
                        nonlocal current_message_type, current_chars_left, current_buffer
                        if current_message_type is None:
                            current_message_type, current_chars_left, start_buffer = fragment.split(":", 2)
                            current_message_type = ServerMessageType(int(current_message_type))
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

                    while (chunk := s.recv(4096)):
                        fragment = chunk.decode()
                        process_fragment(fragment)

                    server_response = ServerResponse.from_json(response_string)
                    if completion_handler:
                        result = server_response if raw else server_response.response
                        completion_handler(result)
                    return server_response
            except Exception as e:
                return ServerResponse(code=ServerResponseStatusCode.COMMAND_EXECUTION_FAILED)
            finally:
                self.set_request_status(request, False)

        self.set_request_status(request, True)
        if asynchronous:
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            return thread
        else:
            return worker()

    def call_root_function(
        self,
        func_name: str,
        *args,
        handler: callable = None,
        asynchronous: bool = False,
        raw: bool = False,
        completion_handler: callable = None,
        **kwargs
    ) -> Any | ServerResponse | threading.Thread:
        """Calls function registered in ROOT_FUNCTION_REGISTRY with @root_function by its name on the server."""
        function = ServerFunction(func_name, *args, **kwargs)
        server_response = self.send_request(
            function,
            handler=handler,
            asynchronous=asynchronous,
            raw=raw,
            completion_handler=completion_handler
        )
        if asynchronous or raw: # Returns thread or whole structure directly
            return server_response
        if server_response.code == ServerResponseStatusCode.OK:
            return server_response.response
        else:
            raise RuntimeError(f"Root function error: {server_response.response}")

    def set_request_status(self, request: ServerCommand | ServerFunction, in_progress: bool):
        if in_progress:
            self.running_actions.append(request)
            app_event_bus.emit(AppEvents.ROOT_REQUEST_STATUS, self, request, True)
        else:
            self.running_actions.remove(request)
            app_event_bus.emit(AppEvents.ROOT_REQUEST_STATUS, self, request, False)
        if not self.running_actions and not self.keep_unlocked and self.server_handshake_established() and self.is_server_process_running:
            self.stop_root_helper()

    def keep_root_unlocked_changed(self, value: bool):
        self.keep_unlocked = value

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
    def _async(handler: callable = None, completion_handler: callable = None, *args, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            handler=handler,
            asynchronous=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _raw(*args, completion_handler: callable = None, **kwargs):
        return RootHelperClient.shared().call_root_function(
            func.__name__,
            *args,
            raw=True,
            completion_handler=completion_handler,
            **kwargs
        )
    def _async_raw(handler: callable = None, completion_handler: callable = None, *args, **kwargs):
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
ServerCallError.SERVER_PROCESS_NOT_AVAILABLE = ServerCallError(2, "The server starting process is not available.")

