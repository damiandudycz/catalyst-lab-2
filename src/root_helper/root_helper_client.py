#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect, re
from enum import Enum
from typing import Optional, Any
from functools import wraps
from gi.repository import Gio
from .environment import RuntimeEnv
from .root_helper_server import ROOT_FUNCTION_REGISTRY, ServerCommand, ServerFunction
from .root_helper_server import ServerResponse, ServerResponseStatusCode, ServerMessageType
from .root_helper_server import _get_socket_path, _get_runtime_dir
from .app_events import AppEvents, app_event_bus

class RootHelperClient:
    _instance = None

    def __init__(self):
        self.token = str(uuid.uuid4())
        self.socket_path = _get_socket_path(os.getuid())
        self._process = None
        self.running_actions = []
        self._is_server_process_running = False

    def set_request_status(self, request: ServerCommand | ServerFunction, in_progress: bool):
        if in_progress:
            self.running_actions.append(request)
            app_event_bus.emit(AppEvents.ROOT_REQUEST_STATUS, self, request, True)
        else:
            self.running_actions.remove(request)
            app_event_bus.emit(AppEvents.ROOT_REQUEST_STATUS, self, request, False)

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_server_process_running(self) -> bool:
        return self._is_server_process_running
    @is_server_process_running.setter
    def is_server_process_running(self, value: bool) -> None:
        if self._is_server_process_running == value:
            return
        self._is_server_process_running = value
        app_event_bus.emit(AppEvents.CHANGE_ROOT_ACCESS, value)

    def send_command(
        self,
        request: ServerCommand | ServerFunction,
        allow_auto_start: bool = True,
        handler: callable = None,
        asynchronous: bool = False,
        raw: bool = False,
        completion_handler: callable = None
    ) -> ServerResponse | threading.Thread:
        """Send a command to the root helper server."""
        if not self._ensure_server_ready(allow_auto_start):
            if request != ServerCommand.HANDSHAKE and self.is_server_process_running:
                self.stop_root_helper()
            raise ServerCallError.SERVER_NOT_READY
        # Prepare message and type
        if isinstance(request, ServerFunction):
            message, request_type = request.to_json(), "function"
        elif isinstance(request, ServerCommand):
            message, request_type = request.value, "command"
        else:
            raise TypeError("command must be either a ServerCommand or ServerFunction instance")
        def worker() -> ServerResponse:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.connect(self.socket_path)
                    s.sendall(f"{self.token} {request_type} {message}".encode())
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
        """ Calls function registered in ROOT_FUNCTION_REGISTRY with @root_function by its name on the server. """
        function = ServerFunction(func_name, *args, **kwargs)
        server_response = self.send_command(
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

    def start_root_helper(self):
        """Start the root helper process."""
        if self.is_server_process_running:
            print("Root helper is already running.")
            return
        self.is_server_process_running = True

        helper_host_path = self._extract_root_helper_to_run_user(os.getuid())
        socket_path = _get_socket_path(os.getuid())
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

        if os.path.exists(socket_path):
            os.remove(socket_path)

        cmd_prefix = ["flatpak-spawn", "--host"] if RuntimeEnv.current() == RuntimeEnv.FLATPAK else []
        cmd_authorize = ["pkexec"]
        exec_call = cmd_prefix + cmd_authorize + [helper_host_path]

        def stream_output(pipe, prefix=""):
            for line in iter(pipe.readline, b''):
                print(prefix + line.decode(), end='')
            pipe.close()

        # Start pkexec and pass token via stdin
        # Note: This is flatpak-spawn process, not server process itself.
        self._process = subprocess.Popen(
            exec_call,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Start threads to stream stdout and stderr
        # TODO: Terminate threads when server stops
        threading.Thread(target=stream_output, args=(self._process.stdout, '[SERVER] >> '), daemon=True).start()
        threading.Thread(target=stream_output, args=(self._process.stderr, '[SERVER] !> '), daemon=True).start()

        # Send token and runtime dir
        self._process.stdin.write(self.token.encode() + b'\n')
        self._process.stdin.flush()
        self._process.stdin.write(xdg_runtime_dir.encode() + b'\n')
        self._process.stdin.flush()

        # Wait for the server to send the "OK" message
        if not self._wait_for_server_ready():
            process_running = self._process and self._process.poll is None
            _, stderr = self._process.communicate(timeout=5) if process_running else (None, None)
            self.is_server_process_running = False
            raise RuntimeError(f"Error starting root helper: {stderr.decode() if stderr else 'No error message'}")

    def _wait_for_server_ready(self, timeout: int = 60) -> bool:
        """Wait for the server to send an 'OK' message indicating it's ready."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.send_command(ServerCommand.HANDSHAKE, allow_auto_start=False)
                return response.code == ServerResponseStatusCode.OK
            except ServerCallError as e:
                if e == ServerCallError.SERVER_NOT_READY:
                    time.sleep(1)
                    continue
                else:
                    break
            except Exception as e:
                print(f"Unexpected error while waiting for server: {e}")
                break
        print(f"Server initialization failed.")
        if self._process and self._process.poll is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None
        return False

    def stop_root_helper(self):
        """Stop the root helper process."""
        if not self.is_server_process_running:
            print(f"Server is already stopped")
            return
        try:
            self.send_command(ServerCommand.EXIT)
        except Exception as e:
            print(f"Failed to stop root helper: {e}")
        finally:
            if self._process and self._process.poll is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            self._process = None
            self.is_server_process_running = False
            print("Root helper process disconnected.")

    def _ensure_server_ready(self, allow_auto_start=True) -> bool:
        """Ensure the root helper server is running and the socket is available."""
        if not self.is_server_process_running:
            if allow_auto_start:
                print("[Root helper is not running, attempting to start it.]")
                self.start_root_helper()
                if not self.is_server_process_running:
                    raise RuntimeError("Failed to start the root helper server.")
            else:
                raise RuntimeError("Root helper is not running.")
        if not os.path.exists(self.socket_path):
            return False
        return True

    def _extract_root_helper_to_run_user(self, uid: int) -> str:
        """Extracts root helper server script and appends root-callable functions."""
        import os

        # Runtime directory where the generated root-helper script will be placed
        runtime_dir = _get_runtime_dir(uid)
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
        """Returns all registered root function sources as a single Python string,
        with @root_function decorators removed."""
        if not ROOT_FUNCTION_REGISTRY:
            return ""

        sources = []

        for func in ROOT_FUNCTION_REGISTRY.values():
            try:
                source = inspect.getsource(func)
                sources.append(source.strip())
            except OSError:
                print(f"Warning: could not get source for function {func.__name__}")
        return "\n\n# ---- Injected root functions ----\n\n" + "\n\n".join(sources)

def root_function(func):
    """Registers a function and replaces it with a proxy that calls the root server."""
    ROOT_FUNCTION_REGISTRY[func.__name__] = func
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
ServerCallError.SERVER_NOT_READY = ServerCallError(1, "The server is not ready.")

