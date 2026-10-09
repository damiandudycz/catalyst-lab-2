from __future__ import annotations
import os, shlex, stat, subprocess, threading, uuid

# ------------------------------------------------------------------------------
# SSH connection to machine where build is deployed (usually booted from Gentoo LiveCD).
# Uses ssh of this computer with shared master connection: password is used once when connecting (passed to ssh
# through SSH_ASKPASS from environment of ssh process, never stored), next commands reuse authenticated connection.
# Host keys are not checked, LiveCD generates new ones on every boot.

class SSHConnection:

    def __init__(self, host: str, port: int = 22, user: str = "root"):
        self.host = host
        self.port = port
        self.user = user
        # Short path in /tmp, sockets paths are limited to about 100 characters.
        self.control_path = os.path.join("/tmp", f"catalystlab-ssh-{uuid.uuid4().hex[:12]}")
        self.connected = False

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"

    def _command(self, *arguments: str, master: bool = False) -> list[str]:
        options = {
            "ControlPath": self.control_path,
            "StrictHostKeyChecking": "no",
            "UserKnownHostsFile": "/dev/null",
            "LogLevel": "ERROR",
            "ServerAliveInterval": "15",
            "ConnectTimeout": "15",
        }
        if master:
            options |= {"ControlMaster": "yes", "ControlPersist": "yes"}
        else:
            options |= {"ControlMaster": "no", "BatchMode": "yes"}
        command = ["ssh", "-p", str(self.port)]
        for key, value in options.items():
            command += ["-o", f"{key}={value}"]
        return command + [self.destination, *arguments]

    # Connection:

    def connect(self, password: str | None) -> str | None:
        """Opens master connection. Returns error message, or None when connected."""
        environment = dict(os.environ)
        askpass = None
        arguments = ["-f", "-N"]
        if password:
            # Script printing password from environment of ssh process.
            askpass = os.path.join("/tmp", f"catalystlab-askpass-{uuid.uuid4().hex[:12]}")
            with open(askpass, "w", encoding="utf-8") as file:
                file.write('#!/bin/sh\nprintf "%s\\n" "$CATALYSTLAB_SSH_PASSWORD"\n')
            os.chmod(askpass, stat.S_IRWXU)
            environment |= {"SSH_ASKPASS": askpass, "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": environment.get("DISPLAY", ":0"),
                            "CATALYSTLAB_SSH_PASSWORD": password}
            arguments = ["-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=keyboard-interactive,password,publickey"] + arguments
        command = self._command(master=True)
        command = command[:-1] + arguments + [command[-1]]
        try:
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return f"Connection to {self.host} timed out"
        finally:
            if askpass and os.path.exists(askpass):
                os.remove(askpass)
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip().splitlines()
            return message[-1] if message else f"Failed to connect to {self.host}"
        self.connected = True
        return None

    def close(self):
        if not self.connected:
            return
        subprocess.run(["ssh", "-o", f"ControlPath={self.control_path}", "-O", "exit", self.destination],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        self.connected = False

    def is_alive(self) -> bool:
        if not self.connected:
            return False
        result = subprocess.run(["ssh", "-o", f"ControlPath={self.control_path}", "-O", "check", self.destination],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        return result.returncode == 0

    # Commands:

    def start(self, script: str) -> subprocess.Popen:
        """Starts bash script on machine, output (with errors) is in stdout of returned process."""
        process = subprocess.Popen(self._command("bash -s"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1, start_new_session=True)
        process.stdin.write(script)
        process.stdin.close()
        return process

    def run(self, script: str, output_handler=print, process_holder: list | None = None) -> bool:
        """Runs bash script on machine, output goes to handler line by line."""
        process = self.start(script)
        if process_holder is not None:
            process_holder.append(process)
        for line in process.stdout:
            output_handler(line.rstrip("\n"))
        return process.wait() == 0

    def output(self, script: str) -> str:
        """Output of script, raises when it fails."""
        lines = []
        if not self.run(script, lines.append):
            raise RuntimeError(lines[-1] if lines else "Command failed on remote machine")
        return "\n".join(lines)

    def stream_file(self, path: str, command: str, output_handler=print, progress_handler=None,
                    process_holder: list | None = None) -> bool:
        """Sends file to stdin of command on machine (eg. tar extracting it). Progress handler gets fraction of sent
        data."""
        process = subprocess.Popen(self._command(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        if process_holder is not None:
            process_holder.append(process)
        def read_output():
            for line in process.stdout:
                output_handler(line.decode(errors="replace").rstrip("\n"))
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        size = os.path.getsize(path) or 1
        sent = 0
        reported = -1
        try:
            with open(path, "rb") as file:
                while chunk := file.read(1024 * 1024):
                    process.stdin.write(chunk)
                    sent += len(chunk)
                    percent = sent * 100 // size
                    if progress_handler and percent != reported:
                        reported = percent
                        progress_handler(sent / size)
            process.stdin.close()
        except (BrokenPipeError, ValueError):
            pass # Command ended (failed or cancelled), its exit code is reported.
        code = process.wait()
        reader.join(timeout=10)
        return code == 0

def quote(value: str) -> str:
    return shlex.quote(value)
