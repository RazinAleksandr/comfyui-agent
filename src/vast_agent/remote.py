from __future__ import annotations

import subprocess
import sys
from pathlib import Path


class RemoteError(Exception):
    """Raised when a remote operation fails."""


def ssh_command(
    host: str,
    port: int,
    ssh_key: str = "",
    extra_options: list[str] | None = None,
) -> list[str]:
    """Build base SSH command with common options.

    All options are placed before ``root@{host}`` so that anything appended
    after the returned list is treated as the remote command by OpenSSH.

    Args:
        host: SSH host.
        port: SSH port.
        ssh_key: Path to the private key file (``-i``).  Tildes are expanded.
        extra_options: Additional ``-o Key=Value`` fragments inserted before
            the hostname.
    """
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=10",
        "-p", str(port),
    ]

    if ssh_key:
        expanded = str(Path(ssh_key).expanduser())
        cmd.extend(["-i", expanded])

    if extra_options:
        cmd.extend(extra_options)

    cmd.append(f"root@{host}")
    return cmd


def check_ssh(
    host: str,
    port: int,
    ssh_key: str = "",
    timeout: int = 10,
) -> bool:
    """Check if SSH connection is possible."""
    cmd = ssh_command(
        host, port,
        ssh_key=ssh_key,
        extra_options=["-o", f"ConnectTimeout={timeout}"],
    ) + ["echo", "ok"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def run_remote(
    host: str,
    port: int,
    command: str,
    ssh_key: str = "",
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command on the remote server via SSH.

    Args:
        host: SSH host.
        port: SSH port.
        command: Shell command to run remotely.
        ssh_key: Path to the private key file.
        capture: If True, capture stdout/stderr. Otherwise stream to terminal.
        check: If True, raise RemoteError on non-zero exit code.

    Returns:
        CompletedProcess with return code and optionally captured output.
    """
    cmd = ssh_command(host, port, ssh_key=ssh_key) + [command]

    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True)
    else:
        result = subprocess.run(cmd)

    if check and result.returncode != 0:
        stderr = result.stderr if capture else ""
        raise RemoteError(
            f"Remote command failed (exit {result.returncode}): {command}\n{stderr}"
        )

    return result


def run_ssh_interactive(host: str, port: int, ssh_key: str = "") -> None:
    """Open an interactive SSH session."""
    cmd = ssh_command(host, port, ssh_key=ssh_key)
    subprocess.run(cmd)


def _rsync_ssh_option(port: int, ssh_key: str = "") -> str:
    """Build the ``-e`` SSH transport string for rsync."""
    parts = f"ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ServerAliveInterval=30 -o ServerAliveCountMax=10"
    if ssh_key:
        expanded = str(Path(ssh_key).expanduser())
        parts += f" -i {expanded}"
    return parts


def rsync_push(
    host: str,
    port: int,
    local_path: str | Path,
    remote_path: str,
    ssh_key: str = "",
    exclude: list[str] | None = None,
) -> None:
    """Push local files to remote server via rsync."""
    local_str = str(local_path).rstrip("/") + "/"
    remote_str = f"root@{host}:{remote_path}/"

    cmd = [
        "rsync", "-avz", "--delete",
        "-e", _rsync_ssh_option(port, ssh_key),
        "--rsync-path", f"mkdir -p {remote_path} && rsync",
    ]

    excludes = exclude or [
        ".git",
        ".venv",
        "__pycache__",
        "*.egg-info",
        ".vast-instance.json",
        "output",
        ".claude",
    ]
    for pattern in excludes:
        cmd.extend(["--exclude", pattern])

    cmd.extend([local_str, remote_str])

    print(f"Pushing {local_path} -> {remote_path}", file=sys.stderr)
    result = subprocess.run(cmd, stdout=sys.stderr)
    if result.returncode != 0:
        raise RemoteError(f"rsync push failed (exit {result.returncode})")


def rsync_pull(
    host: str,
    port: int,
    remote_path: str,
    local_path: str | Path,
    ssh_key: str = "",
) -> None:
    """Pull files from remote server to local via rsync."""
    remote_str = f"root@{host}:{remote_path}"
    local_str = str(local_path)

    # Ensure local directory exists
    Path(local_str).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rsync", "-avz",
        "-e", _rsync_ssh_option(port, ssh_key),
        remote_str, local_str,
    ]

    print(f"Pulling {remote_path} -> {local_path}", file=sys.stderr)
    result = subprocess.run(cmd, stdout=sys.stderr)
    if result.returncode != 0:
        raise RemoteError(f"rsync pull failed (exit {result.returncode})")


def rsync_push_files(
    host: str,
    port: int,
    files: dict[str, Path],
    remote_dir: str,
    ssh_key: str = "",
) -> dict[str, str]:
    """Push individual files to a remote directory.

    Args:
        host: SSH host.
        port: SSH port.
        files: Mapping of logical name to local file path.
        remote_dir: Remote directory to place files in.
        ssh_key: Path to the private key file.

    Returns:
        Mapping of logical name to remote file path.
    """
    # Create remote directory
    run_remote(host, port, f"mkdir -p {remote_dir}", ssh_key=ssh_key, capture=True)

    remote_paths: dict[str, str] = {}
    for name, local_file in files.items():
        remote_file = f"{remote_dir}/{local_file.name}"
        cmd = [
            "rsync", "-avz",
            "-e", _rsync_ssh_option(port, ssh_key),
            str(local_file), f"root@{host}:{remote_file}",
        ]
        print(f"  Uploading {name}: {local_file.name}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RemoteError(
                f"Failed to upload {local_file}: {result.stderr}"
            )
        remote_paths[name] = remote_file

    return remote_paths
