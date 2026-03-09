#!/bin/bash
# Create virtual environment and install the pipeline
set -e

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .

echo ""
echo "Virtual environment created. Activate with:"
echo "  source .venv/bin/activate"
echo ""
echo "Usage:"
echo "  comfy-pipeline setup -w wan_animate                           # Install ComfyUI + models"
echo "  comfy-pipeline server start -w wan_animate --wait             # Start server"
echo "  comfy-pipeline run -w wan_animate --input reference_image=ref.png --input reference_video=ref.mp4"
echo "  comfy-pipeline server stop -w wan_animate                     # Stop server"
