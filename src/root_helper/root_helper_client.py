#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect, re
from enum import Enum
from typing import Optional
from functools import wraps
from gi.repository import Gio
from .root_helper_server import ROOT_FUNCTION_REGISTRY, ServerCommand, ServerFunction, ServerResponse, ServerResponseStatusCode, _get_socket_path, _get_runtime_dir
from .environment import RuntimeEnv

class RootHelperClient:
    _instance = None

    def __init__(self):
        self.token = str(uuid.uuid4())
        self.socket_path = _get_socket_path(os.getuid())
        self._process = None

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_server_process_running(self) -> bool:
        return False if self._process is None else self._process.poll() is None

    def send_command(self, command: ServerCommand | ServerFunction, allow_auto_start=True, handler: callable = None, asynchronous: bool = False) -> str | threading.Thread:
        """Send a command to the root helper server."""
        if not self._ensure_server_ready(allow_auto_start):
            raise ServerCallError.SERVER_NOT_READY

        # Ensure that command is either ServerCommand or ServerFunction
        if isinstance(command, ServerFunction):
            message = command.to_json()
        elif isinstance(command, ServerCommand):
            message = command.value
        else:
            raise TypeError("command must be either a ServerCommand or ServerFunction instance")
        # Function to handle the socket communication
        def worker():
            # TODO: Proper cleaning and closing socket if thread was stopped.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(self.socket_path)
                s.sendall(f"{self.token} {message}".encode())
                response_chunks = []
                handler_chunks = []
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response_chunks.append(chunk)
                    # If handler is provided, pass each chunk to the handler
                    if handler:
                        handler_chunks.append(chunk)
                        # Try to decode handler_chunks if they already created a full json
                        handler_line = b"".join(handler_chunks).decode()
                        try:
                            json_response = json.loads(handler_line)
                            handler_chunks = []
                            handler(json_response.get("response"))
                            # Ignore error codes in callback handler
                        except Exception:
                            pass
                # Combine chunks and decode
                return b"".join(response_chunks).decode()
        if asynchronous:
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            return thread
        else:
            return worker()

    def call_root_function(self, func_name: str, *args, handler: callable = None, asynchronous: bool = False, **kwargs) -> str | threading.Thread:
        """ Calls function registered in ROOT_FUNCTION_REGISTRY with @root_function by its name on the server. """
        """ This is just a helper function, decorator @root_function handles this communication and you can just """
        """ call these functions directly on client side - they will be passed to server using this function. """
        function = ServerFunction(func_name, *args, **kwargs)
        command_response = self.send_command(function, handler=handler, asynchronous=asynchronous)
        if asynchronous:
            return command_response  # Return the thread object for async execution
        # Process response for sync execution
        json_response = json.loads(command_response)
        if json_response.get("code") == ServerResponseStatusCode.OK.value:
            return json_response.get("response")
        else:
            message = json_response.get("response")
            raise RuntimeError(f"Root function error: {message}")

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
            _, stderr = self._process.communicate(timeout=5) if self.is_server_process_running else (None, None)
            raise RuntimeError(f"Error starting root helper: {stderr.decode() if stderr else 'No error message'}")
        print("Root helper process started and is ready.")

    def _wait_for_server_ready(self, timeout: int = 60) -> bool:
        """Wait for the server to send an 'OK' message indicating it's ready."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.send_command(ServerCommand.INITIALIZE, allow_auto_start=False)
                data = json.loads(response)
                return data.get("code") == ServerResponseStatusCode.OK.value
            except ServerCallError as e:
                if e == ServerCallError.SERVER_NOT_READY:
                    time.sleep(1)
                    continue
            except Exception as e:
                print(f"Unexpected error while waiting for server: {e}")
                break
        print(f"Server did not respond to {ServerCommand.INITIALIZE.value} in time.")
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
            self._process = None
            print("Root helper process stopped.")

    def _ensure_server_ready(self, allow_auto_start=True):
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
            print("[Waiting for server socket]")
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
    # Example:
    # @root_function
    # def add(a, b):
    #     return a + b
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
        # Now calling call_root_function with asynchronous=True
        return RootHelperClient.shared().call_root_function(func.__name__, *args, handler=handler, asynchronous=True, **kwargs)
    # Add ._async to the proxy
    proxy_function._async = async_variant
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

class ThreadWorker:
    def __init__(self, target_function):
        """Initialize the thread worker with the target function to run in a separate thread."""
        self.target_function = target_function
        self.stop_thread = False  # Flag to stop the thread
        self.thread = threading.Thread(target=self._worker, daemon=True)

    def _worker(self):
        """Worker thread that executes the provided target function."""
        try:
            self.result = self.target_function()
        except Exception as e:
            self.error = e
            self.result = None

    def start(self):
        """Start the thread."""
        self.thread.start()

    def stop(self):
        """Set the stop flag to True to signal the thread to stop."""
        self.stop_thread = True

    def join(self):
        """Join the thread, blocking until it finishes."""
        self.thread.join()

    def get_result(self):
        """Return the result of the worker function if finished."""
        return getattr(self, 'result', None)

    def get_error(self):
        """Return any error that occurred in the worker thread."""
        return getattr(self, 'error', None)
