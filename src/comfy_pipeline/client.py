from __future__ import annotations

import json
import time
import uuid
import urllib.parse
from pathlib import Path

import requests
import websocket


class ComfyUIClient:
    """HTTP/WebSocket client for ComfyUI API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188):
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.client_id = str(uuid.uuid4())

    def is_ready(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=5)
            return r.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False

    def wait_ready(self, timeout: int = 300):
        """Block until ComfyUI server is responsive."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_ready():
                return
            time.sleep(2)
        raise TimeoutError(f"ComfyUI not ready after {timeout}s")

    def upload_file(self, filepath: Path, subfolder: str = "") -> str:
        """Upload a file to ComfyUI input directory. Returns the server filename."""
        filepath = Path(filepath)
        with open(filepath, "rb") as f:
            r = requests.post(
                f"{self.base_url}/upload/image",
                files={"image": (filepath.name, f, "application/octet-stream")},
                data={"subfolder": subfolder, "overwrite": "true"},
            )
        r.raise_for_status()
        return r.json()["name"]

    def queue_prompt(self, workflow: dict) -> str:
        """Submit a workflow for execution. Returns prompt_id."""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        r = requests.post(f"{self.base_url}/prompt", json=payload)
        if r.status_code != 200:
            try:
                detail = json.dumps(r.json(), indent=2)
            except Exception:
                detail = r.text[:2000]
            raise RuntimeError(
                f"ComfyUI rejected prompt (HTTP {r.status_code}):\n{detail}"
            )
        result = r.json()
        if "error" in result:
            node_errors = result.get("node_errors", "")
            raise RuntimeError(f"Prompt error: {result['error']}\n{node_errors}")
        return result["prompt_id"]

    def get_history(self, prompt_id: str) -> dict:
        r = requests.get(f"{self.base_url}/history/{prompt_id}")
        r.raise_for_status()
        return r.json()

    def get_object_info(self) -> dict:
        """Get node type definitions (needed for UI->API workflow conversion)."""
        r = requests.get(f"{self.base_url}/object_info")
        r.raise_for_status()
        return r.json()

    def wait_for_completion(
        self,
        prompt_id: str,
        timeout: int = 3600,
        node_names: dict[str, str] | None = None,
    ) -> dict:
        """Wait for prompt execution via WebSocket. Returns history."""
        ws = websocket.WebSocket()
        ws.connect(f"{self.ws_url}?clientId={self.client_id}")
        ws.settimeout(timeout)

        try:
            while True:
                msg = ws.recv()
                if not isinstance(msg, str):
                    continue
                data = json.loads(msg)
                msg_type = data.get("type")

                if msg_type == "executing":
                    exec_data = data["data"]
                    if exec_data.get("prompt_id") != prompt_id:
                        continue
                    node = exec_data.get("node")
                    if node is None:
                        break  # execution complete
                    name = node_names.get(node, "") if node_names else ""
                    label = f"{node} ({name})" if name else node
                    print(f"  Executing node {label}...", file=__import__('sys').stderr)

                elif msg_type == "execution_error":
                    raise RuntimeError(f"Execution error: {data['data']}")

                elif msg_type == "execution_interrupted":
                    raise RuntimeError("Execution was interrupted")
        finally:
            ws.close()

        return self.get_history(prompt_id)

    def download_output(
        self, filename: str, subfolder: str, output_dir: Path
    ) -> Path:
        """Download an output file from ComfyUI."""
        params = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": "output"}
        )
        r = requests.get(f"{self.base_url}/view?{params}")
        r.raise_for_status()

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        with open(output_path, "wb") as f:
            f.write(r.content)
        return output_path
