#!/usr/bin/env python3
import os, socket, subprocess, sys, uuid, pwd, tempfile, time, struct, signal, threading, json, inspect, re
from enum import Enum
from typing import Optional
from functools import wraps
from gi.repository import Gio
from .root_helper_server import ROOT_FUNCTION_REGISTRY, ServerCommand, ServerResponse, ServerResponseStatusCode, _get_socket_path, _get_runtime_dir

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
    def is_server_process_running(self):
        return False if self._process is None else self._process.poll() is None

    def send_command(self, command: ServerCommand, allow_auto_start=True) -> str:
        """Send a command to the root helper server."""
        if not self._ensure_server_ready(allow_auto_start):
            return "[SERVER NOT READY]"

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        s.sendall(f"{self.token} {command.value}".encode())

        response_chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response_chunks.append(chunk)
        return b"".join(response_chunks).decode()

    def send_command_async(self, command: ServerCommand, handler: callable, allow_auto_start=True) -> threading.Thread:
        """Send a command to the root helper server in async mode."""
        if not self._ensure_server_ready(allow_auto_start):
            return "[SERVER NOT READY]" # TODO: Change to excepting and handle in _wait_for_server_ready correctly

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        s.sendall(f"{self.token} {command.value}".encode())

        def reader_thread(sock, callback):
            try:
                buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        if buffer:
                            callback(buffer.decode())
                        break
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        callback(line.decode())
            finally:
                sock.close()

        thread = threading.Thread(target=reader_thread, args=(s, handler), daemon=True)
        thread.start()
        return thread

    def call_root_function(self, func_name: str, *args, **kwargs) -> str:
        """ Calls function registered in ROOT_FUNCTION_REGISTRY with @root_function by its name on the server. """
        """ This is just a helper function, decorator @root_function handles this communication and you can just """
        """ call these functions directly on client side - they will be passed to server using this function. """
        if not self._ensure_server_ready():
            raise RuntimeError("[SERVER NOT READY]")

        payload = {
            "function": func_name,
            "args": args,
            "kwargs": kwargs,
        }

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        s.sendall(f"{self.token} {json.dumps(payload)}".encode())

        response_chunks = []
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response_chunks.append(chunk)

        result = json.loads(b"".join(response_chunks).decode())
        if result.get("code") == ServerResponseStatusCode.OK.value:
            return result.get("response")
        else:
            message = result.get("response")
            raise RuntimeError(f"Root function error: {message}")

    def call_root_function_async(self, func_name: str, handler: callable, *args, **kwargs) -> threading.Thread:
        """
        Asynchronously call a root function and pass the decoded response (JSON) to the handler.
        """
        if not self._ensure_server_ready():
            raise RuntimeError("[SERVER NOT READY]")

        payload = {
            "function": func_name,
            "args": args,
            "kwargs": kwargs,
        }

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket_path)
        s.sendall(f"{self.token} {json.dumps(payload)}".encode())

        def reader_thread(sock, callback):
            try:
                buffer = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk
                if buffer:
                    try:
                        result = json.loads(buffer.decode())
                    except json.JSONDecodeError:
                        result = {"status": "error", "message": "Invalid JSON response"}
                    callback(result)
            finally:
                sock.close()

        thread = threading.Thread(target=reader_thread, args=(s, handler), daemon=True)
        thread.start()
        return thread

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

        from .environment import RuntimeEnv
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
        import errno
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                response = self.send_command(ServerCommand.INITIALIZE, allow_auto_start=False)
                print(f">>> {response}")
                try:
                    data = json.loads(response)
                    return data.get("code") == ServerResponseStatusCode.OK.value
                except json.JSONDecodeError:
                    time.sleep(1)
                    continue
            except (FileNotFoundError, ConnectionRefusedError, socket.error) as e:
                # If the socket isn't ready yet, retry after short sleep
                if isinstance(e, socket.error) and getattr(e, "errno", None) not in (errno.ECONNREFUSED, errno.ENOENT):
                    raise
            except Exception as e:
                print(f"Unexpected error while waiting for server: {e}")
                break
            time.sleep(1)

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
            else:
                raise RuntimeError("Root helper is not running.")
        if not self.is_server_process_running:
            raise RuntimeError("Failed to start the root helper server.")
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
