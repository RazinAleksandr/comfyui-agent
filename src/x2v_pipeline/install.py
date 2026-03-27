"""Remote bootstrap commands for LightX2V pipeline.

Generates shell commands to install LightX2V, dependencies, and models
on a remote GPU server.
"""

from __future__ import annotations

from x2v_pipeline.config import ModelEntry, X2VConfig


def build_install_commands(config: X2VConfig) -> list[str]:
    """Build shell commands for remote LightX2V bootstrap.

    Steps:
    1. Clone or update LightX2V repo
    2. pip install -e LightX2V
    3. Install extra pip dependencies
    4. Download all required models

    Args:
        config: Pipeline configuration.

    Returns:
        List of shell command strings.
    """
    repo_path = config.repo_path
    repo_url = config.repo_url
    commands: list[str] = []

    # Install system dependencies (OpenCV needs libxcb/libgl, video processing needs ffmpeg)
    commands.append(
        "apt-get update -qq && apt-get install -y -qq libxcb1 libgl1 ffmpeg > /dev/null 2>&1"
    )

    # Clone or update the repo
    commands.append(
        f"if [ -d {repo_path}/.git ]; then "
        f"cd {repo_path} && git pull; "
        f"else git clone {repo_url} {repo_path}; fi"
    )

    # Install LightX2V as editable + animate requirements
    commands.append(f"pip install -e {repo_path}")
    commands.append(
        f"pip install peft sentencepiece moviepy matplotlib"
    )
    # SAM2 needs CUDA dev headers — install from git (non-editable)
    commands.append(
        "pip install git+https://github.com/facebookresearch/sam2.git"
        "@0e78a118995e66bb27d78518c4bd9a3e95b4e266 --no-build-isolation"
    )

    # Install extra pip dependencies
    for dep in config.extra_pip:
        commands.append(f"pip install {dep}")

    # Download models
    model_path = "/workspace/models"
    commands.append(f"mkdir -p {model_path}")

    for model in config.models:
        # Skip refinement model if refinement is disabled
        if model.name == "refine_model" and not config.is_refinement_enabled():
            continue
        cmds = build_model_download_commands(config, model_path, model)
        commands.extend(cmds)

    # Apply patches to LightX2V source (after clone/install)
    commands.extend(build_patch_commands(config))

    return commands


def build_model_download_commands(
    config: X2VConfig,
    model_path: str,
    model: ModelEntry | None = None,
) -> list[str]:
    """Build download commands for a single model.

    Checks if the file already exists (and meets min_size) before downloading.
    Uses wget for direct URLs, huggingface-cli for hf_repo type, and a
    special flow for RIFE zip files.

    Args:
        config: Pipeline configuration.
        model_path: Remote base directory for models.
        model: Specific model entry to download. If None, returns empty list.

    Returns:
        List of shell command strings.
    """
    if model is None:
        return []

    commands: list[str] = []
    full_path = f"{model_path}/{model.path}"

    if model.type == "hf_repo":
        if model.hf_subfolder:
            # Download specific subfolder from HF repo
            target_dir = full_path.rstrip("/")
            commands.append(
                f"if [ ! -d {target_dir} ] || [ -z \"$(ls -A {target_dir} 2>/dev/null)\" ]; then "
                f"python3 -c \""
                f"from huggingface_hub import snapshot_download; "
                f"snapshot_download('{model.hf_repo}', "
                f"local_dir='{model_path}', "
                f"allow_patterns='{model.hf_subfolder}/**')\"; "
                f"fi"
            )
        else:
            # Full repo download (transformer, T5, CLIP, VAE, process_checkpoint, etc.)
            # path field is ignored — full repo always downloads to model_path root.
            # Use process_checkpoint/ dir as sentinel: it downloads late and confirms
            # the repo is complete (config.json alone is unreliable after partial downloads).
            sentinel = f"{model_path}/process_checkpoint"
            commands.append(
                f"if [ ! -d {sentinel} ] || [ -z \"$(ls -A {sentinel} 2>/dev/null)\" ]; then "
                f"python3 -c \""
                f"from huggingface_hub import snapshot_download; "
                f"snapshot_download('{model.hf_repo}', "
                f"local_dir='{model_path}')\"; "
                f"fi"
            )
    elif model.type == "rife_zip":
        # RIFE: download zip, extract flownet.pkl (use python zipfile — unzip may not be installed)
        rife_dir = full_path.rstrip("/")
        flownet_path = f"{rife_dir}/flownet.pkl"
        commands.append(
            f"if [ ! -f {flownet_path} ]; then "
            f"mkdir -p {rife_dir} && "
            f"TMPDIR=$(mktemp -d) && "
            f"wget -q -O $TMPDIR/rife.zip '{model.url}' && "
            f"python3 -c \""
            f"import zipfile, shutil, pathlib; "
            f"z=zipfile.ZipFile('$TMPDIR/rife.zip'); "
            f"z.extractall('$TMPDIR/rife_extract'); "
            f"f=next(pathlib.Path('$TMPDIR/rife_extract').rglob('flownet.pkl')); "
            f"shutil.copy2(str(f), '{flownet_path}')\" && "
            f"rm -rf $TMPDIR; "
            f"fi"
        )
    else:
        # Standard URL download with wget
        # Ensure parent directory exists
        parent_dir = "/".join(full_path.rsplit("/", 1)[:-1]) if "/" in model.path else model_path
        size_check = ""
        if model.min_size > 0:
            size_check = (
                f"|| [ $(stat -c%s {full_path} 2>/dev/null || echo 0) "
                f"-lt {model.min_size} ]"
            )

        commands.append(
            f"mkdir -p {parent_dir} && "
            f"if [ ! -f {full_path} ] {size_check}; then "
            f"wget -q --show-progress -O {full_path} '{model.url}'; "
            f"fi"
        )

    return commands


def build_patch_commands(config: X2VConfig) -> list[str]:
    """Build commands to patch LightX2V source for unload_modules support.

    Fixes WanAnimateRunner.run_vae_encoder() which is missing the
    load/unload guards that run_vae_decoder and run_image_encoder have.
    Without this, unload_modules=true + cpu_offload=false fails because
    the VAE encoder is never loaded before use.

    Args:
        config: Pipeline configuration.

    Returns:
        List of shell command strings.
    """
    target = (
        f"{config.repo_path}/lightx2v/models/runners/wan/wan_animate_runner.py"
    )
    # Use Python to patch: insert load guard after method signature,
    # insert unload guard before the return statement.
    # Idempotent — checks if patch is already applied.
    # Write patch as a standalone script to avoid shell quoting issues
    patch_py = (
        "import pathlib\n"
        f"p = pathlib.Path('{target}')\n"
        "src = p.read_text()\n"
        "marker = 'self.vae_encoder = self.load_vae_encoder()'\n"
        "if marker in src:\n"
        "    print('Patch already applied'); exit(0)\n"
        "load_guard = (\n"
        "    '        if self.config.get(\"lazy_load\", False) '\n"
        "    'or self.config.get(\"unload_modules\", False):\\n'\n"
        "    '            self.vae_encoder = self.load_vae_encoder()\\n'\n"
        ")\n"
        "src = src.replace(\n"
        "    '    ):\\n        H, W = self.refer_pixel_values.shape[-2]',\n"
        "    '    ):\\n' + load_guard + '        H, W = self.refer_pixel_values.shape[-2]'\n"
        ")\n"
        "unload_guard = (\n"
        "    '        if self.config.get(\"lazy_load\", False) '\n"
        "    'or self.config.get(\"unload_modules\", False):\\n'\n"
        "    '            del self.vae_encoder\\n'\n"
        "    '            torch.cuda.empty_cache()\\n'\n"
        "    '            gc.collect()\\n\\n'\n"
        ")\n"
        "src = src.replace(\n"
        "    '        return y, pose_latents',\n"
        "    unload_guard + '        return y, pose_latents'\n"
        ")\n"
        "p.write_text(src)\n"
        "print('Patched wan_animate_runner.py: added VAE encoder load/unload guards')\n"
    )
    return [
        f"cat > /tmp/patch_vae.py << 'PATCHEOF'\n{patch_py}PATCHEOF",
        "python3 /tmp/patch_vae.py",
    ]


def build_check_command() -> str:
    """Build a python one-liner to verify LightX2V is importable.

    Returns:
        Shell command string.
    """
    return (
        "python3 -c \"import lightx2v; print('LightX2V version:', "
        "getattr(lightx2v, '__version__', 'unknown'))\""
    )
