"""Test UI→API workflow conversion focusing on widget value mapping."""

from comfy_pipeline.workflow import (
    _get_widget_input_names,
    _is_widget_input,
    _map_widget_values,
    convert_to_api_format,
)


# ---------------------------------------------------------------------------
# _is_widget_input
# ---------------------------------------------------------------------------

def test_is_widget_int():
    assert _is_widget_input(["INT", {"default": 0, "min": 0, "max": 100}]) is True

def test_is_widget_float():
    assert _is_widget_input(["FLOAT", {"default": 1.0}]) is True

def test_is_widget_string():
    assert _is_widget_input(["STRING", {"default": "hello"}]) is True

def test_is_widget_boolean():
    assert _is_widget_input(["BOOLEAN", {"default": True}]) is True

def test_is_widget_combo():
    assert _is_widget_input([["euler", "heun", "dpm"], {}]) is True

def test_is_not_widget_model():
    """MODEL with tooltip metadata should NOT be a widget."""
    assert _is_widget_input(["MODEL", {"tooltip": "The model"}]) is False

def test_is_not_widget_model_bare():
    assert _is_widget_input(["MODEL"]) is False

def test_is_not_widget_conditioning():
    assert _is_widget_input(["CONDITIONING", {"tooltip": "cond"}]) is False

def test_is_not_widget_image():
    assert _is_widget_input(["IMAGE", {}]) is False

def test_is_not_widget_vae():
    assert _is_widget_input(["VAE"]) is False

def test_is_not_widget_latent():
    assert _is_widget_input(["LATENT", {"tooltip": "latent"}]) is False


# ---------------------------------------------------------------------------
# _get_widget_input_names
# ---------------------------------------------------------------------------

def test_ksampler_widget_names():
    """KSampler: connection inputs (even with metadata) should be excluded."""
    node_info = {
        "input": {
            "required": {
                "model": ["MODEL", {"tooltip": "The model used for denoising."}],
                "positive": ["CONDITIONING", {"tooltip": "positive conditioning"}],
                "negative": ["CONDITIONING", {"tooltip": "negative conditioning"}],
                "latent_image": ["LATENT", {"tooltip": "The latent image."}],
                "seed": ["INT", {"default": 0, "min": 0, "max": 2**64,
                          "control_after_generate": True}],
                "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                "cfg": ["FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0}],
                "sampler_name": [["euler", "heun", "dpm"], {}],
                "scheduler": [["simple", "karras", "normal"], {}],
                "denoise": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0}],
            }
        }
    }
    names = _get_widget_input_names(node_info, set())
    assert names == ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"]


def test_ksampler_widget_names_old_format():
    """KSampler with old-style single-element connection specs."""
    node_info = {
        "input": {
            "required": {
                "model": ["MODEL"],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 8.0}],
                "sampler_name": [["euler", "heun"], {}],
                "scheduler": [["normal", "karras"], {}],
                "denoise": ["FLOAT", {"default": 1.0}],
            }
        }
    }
    names = _get_widget_input_names(node_info, set())
    assert names == ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"]


# ---------------------------------------------------------------------------
# _map_widget_values: KSampler seed control skipping
# ---------------------------------------------------------------------------

def test_ksampler_seed_fixed():
    """KSampler with 'fixed' control_after_generate should skip it."""
    widget_names = ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"]
    widgets = [846593894047126, "fixed", 4, 1, "euler", "simple", 1]
    inputs = {}
    _map_widget_values(widgets, widget_names, inputs)
    assert inputs["seed"] == 846593894047126
    assert inputs["steps"] == 4
    assert inputs["cfg"] == 1
    assert inputs["sampler_name"] == "euler"
    assert inputs["scheduler"] == "simple"
    assert inputs["denoise"] == 1


def test_ksampler_seed_randomize():
    """KSampler with 'randomize' control_after_generate should skip it."""
    widget_names = ["seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"]
    widgets = [1035046569654569, "randomize", 4, 1, "euler", "simple", 0.13]
    inputs = {}
    _map_widget_values(widgets, widget_names, inputs)
    assert inputs["seed"] == 1035046569654569
    assert inputs["steps"] == 4
    assert inputs["denoise"] == 0.13


# ---------------------------------------------------------------------------
# _map_widget_values: connected widget inputs (positional alignment)
# ---------------------------------------------------------------------------

def test_imagescale_connected_width_height():
    """ImageScale: width/height connected but still in widgets_values."""
    widget_names = ["upscale_method", "width", "height", "crop"]
    widgets = ["lanczos", 512, 512, "center"]
    inputs = {}
    connected = {"image", "width", "height"}
    _map_widget_values(widgets, widget_names, inputs, connected)
    assert inputs["upscale_method"] == "lanczos"
    assert inputs["crop"] == "center"
    assert "width" not in inputs
    assert "height" not in inputs


def test_imagefrombatch_connected_batch_index():
    """ImageFromBatch: batch_index connected, length should get correct value."""
    widget_names = ["batch_index", "length"]
    widgets = [0, 4096]
    inputs = {}
    connected = {"image", "batch_index"}
    _map_widget_values(widgets, widget_names, inputs, connected)
    assert inputs["length"] == 4096
    assert "batch_index" not in inputs


# ---------------------------------------------------------------------------
# _map_widget_values: WanAnimateToVideo
# ---------------------------------------------------------------------------

def test_wanimate_widget_mapping():
    """WanAnimateToVideo: lots of connected inputs, few widget values."""
    # Only widget-type inputs (not connection types like CONDITIONING, VAE, etc.)
    widget_names = ["width", "height", "length", "batch_size",
                    "continue_motion_max_frames", "video_frame_offset"]
    widgets = [832, 480, 77, 1, 4097, 0]
    connected = {"positive", "negative", "vae", "clip_vision_output",
                 "reference_image", "face_video", "pose_video",
                 "background_video", "character_mask",
                 "width", "height", "length"}
    inputs = {}
    _map_widget_values(widgets, widget_names, inputs, connected)
    # width, height, length are connected → skipped
    assert "width" not in inputs
    assert "height" not in inputs
    assert "length" not in inputs
    # Remaining values assigned correctly
    assert inputs["batch_size"] == 1
    assert inputs["continue_motion_max_frames"] == 4097
    assert inputs["video_frame_offset"] == 0


# ---------------------------------------------------------------------------
# Full conversion test with minimal workflow
# ---------------------------------------------------------------------------

def test_full_conversion_ksampler():
    """End-to-end: UI workflow with KSampler converts correctly."""
    ui_workflow = {
        "nodes": [
            {
                "id": 1,
                "type": "KSampler",
                "mode": 0,
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": 1},
                    {"name": "positive", "type": "CONDITIONING", "link": 2},
                    {"name": "negative", "type": "CONDITIONING", "link": 3},
                    {"name": "latent_image", "type": "LATENT", "link": 4},
                ],
                "outputs": [{"name": "LATENT", "type": "LATENT", "links": [5]}],
                "widgets_values": [12345, "fixed", 20, 7.5, "euler", "normal", 1.0],
            },
            {
                "id": 2,
                "type": "CheckpointLoaderSimple",
                "mode": 0,
                "inputs": [],
                "outputs": [
                    {"name": "MODEL", "type": "MODEL", "links": [1]},
                    {"name": "CLIP", "type": "CLIP", "links": []},
                    {"name": "VAE", "type": "VAE", "links": []},
                ],
                "widgets_values": ["model.safetensors"],
            },
        ],
        "links": [
            [1, 2, 0, 1, 0, "MODEL"],
            [2, 2, 1, 1, 1, "CONDITIONING"],  # fake for test
            [3, 2, 2, 1, 2, "CONDITIONING"],
            [4, 2, 0, 1, 3, "LATENT"],  # fake
        ],
    }

    object_info = {
        "KSampler": {
            "input": {
                "required": {
                    "model": ["MODEL", {"tooltip": "The model"}],
                    "positive": ["CONDITIONING", {"tooltip": "pos"}],
                    "negative": ["CONDITIONING", {"tooltip": "neg"}],
                    "latent_image": ["LATENT", {"tooltip": "latent"}],
                    "seed": ["INT", {"default": 0, "min": 0, "max": 2**64,
                              "control_after_generate": True}],
                    "steps": ["INT", {"default": 20, "min": 1, "max": 10000}],
                    "cfg": ["FLOAT", {"default": 8.0}],
                    "sampler_name": [["euler", "heun"], {}],
                    "scheduler": [["normal", "karras"], {}],
                    "denoise": ["FLOAT", {"default": 1.0}],
                }
            }
        },
        "CheckpointLoaderSimple": {
            "input": {
                "required": {
                    "ckpt_name": [["model.safetensors"], {}],
                }
            }
        },
    }

    api = convert_to_api_format(ui_workflow, object_info)

    ks = api["1"]["inputs"]
    assert ks["seed"] == 12345, f"seed={ks.get('seed')}"
    assert ks["steps"] == 20, f"steps={ks.get('steps')}"
    assert ks["cfg"] == 7.5, f"cfg={ks.get('cfg')}"
    assert ks["sampler_name"] == "euler"
    assert ks["scheduler"] == "normal"
    assert ks["denoise"] == 1.0, f"denoise={ks.get('denoise')}"
    # Connection inputs should be references
    assert ks["model"] == ["2", 0]


def test_sam2_connection_only_string_inputs():
    """Sam2Segmentation: STRING connection-only inputs should not consume widget values.

    coordinates_positive/coordinates_negative are STRING type but connection-only
    (no "widget" key in the inputs array). widgets_values only has entries for
    actual widget toggles (individual_objects, multimask_output).
    """
    ui_workflow = {
        "nodes": [
            {
                "id": 365,
                "type": "Sam2Segmentation",
                "mode": 0,
                "inputs": [
                    {"name": "sam2_model", "type": "SAM2MODEL", "link": 1},
                    {"name": "image", "type": "IMAGE", "link": 2},
                    # connection-only STRING inputs (no "widget" key)
                    {"name": "coordinates_positive", "type": "STRING",
                     "shape": 7, "link": None},
                    {"name": "coordinates_negative", "type": "STRING",
                     "shape": 7, "link": None},
                    {"name": "bboxes", "type": "BBOX", "shape": 7, "link": 3},
                    {"name": "mask", "type": "MASK", "shape": 7, "link": None},
                ],
                "outputs": [{"name": "mask", "type": "MASK", "links": [10]}],
                "widgets_values": [False, False],
            },
            {
                "id": 100,
                "type": "DummySource",
                "mode": 0,
                "inputs": [],
                "outputs": [
                    {"name": "OUT", "type": "SAM2MODEL", "links": [1]},
                    {"name": "IMG", "type": "IMAGE", "links": [2]},
                    {"name": "BOX", "type": "BBOX", "links": [3]},
                ],
                "widgets_values": [],
            },
        ],
        "links": [
            [1, 100, 0, 365, 0, "SAM2MODEL"],
            [2, 100, 1, 365, 1, "IMAGE"],
            [3, 100, 2, 365, 4, "BBOX"],
        ],
    }

    object_info = {
        "Sam2Segmentation": {
            "input": {
                "required": {
                    "sam2_model": ["SAM2MODEL"],
                    "image": ["IMAGE"],
                    "individual_objects": ["BOOLEAN", {"default": False}],
                    "multimask_output": ["BOOLEAN", {"default": False}],
                },
                "optional": {
                    "coordinates_positive": ["STRING", {"default": ""}],
                    "coordinates_negative": ["STRING", {"default": ""}],
                    "bboxes": ["BBOX"],
                    "mask": ["MASK"],
                },
            }
        },
        "DummySource": {"input": {"required": {}}},
    }

    api = convert_to_api_format(ui_workflow, object_info)
    sam = api["365"]["inputs"]

    # The boolean widgets should get correct values from widgets_values
    assert sam["individual_objects"] is False
    assert sam["multimask_output"] is False
    # Connection-only STRING inputs should NOT be assigned boolean values
    assert "coordinates_positive" not in sam or sam["coordinates_positive"] != False
    assert "coordinates_negative" not in sam or sam["coordinates_negative"] != False


def test_full_conversion_reroute():
    """Reroute nodes should be resolved transparently."""
    ui_workflow = {
        "nodes": [
            {
                "id": 10,
                "type": "SourceNode",
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "OUT", "type": "AUDIO", "links": [100]}],
                "widgets_values": [],
            },
            {
                "id": 20,
                "type": "Reroute",
                "mode": 0,
                "inputs": [{"name": "", "type": "*", "link": 100}],
                "outputs": [{"name": "AUDIO", "type": "AUDIO", "links": [200]}],
            },
            {
                "id": 30,
                "type": "DestNode",
                "mode": 0,
                "inputs": [{"name": "audio", "type": "AUDIO", "link": 200}],
                "outputs": [],
                "widgets_values": [],
            },
        ],
        "links": [
            [100, 10, 0, 20, 0, "AUDIO"],
            [200, 20, 0, 30, 0, "AUDIO"],
        ],
    }

    object_info = {
        "SourceNode": {"input": {"required": {}}},
        "DestNode": {"input": {"required": {"audio": ["AUDIO"]}}},
    }

    api = convert_to_api_format(ui_workflow, object_info)

    # Reroute (20) should not be in API workflow
    assert "20" not in api
    # DestNode should reference SourceNode directly, not the Reroute
    assert api["30"]["inputs"]["audio"] == ["10", 0]


def test_bypassed_node_passthrough():
    """Bypassed nodes (mode=4) should pass through first input to output."""
    ui_workflow = {
        "nodes": [
            {
                "id": 10,
                "type": "LoadImage",
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [100]}],
                "widgets_values": ["image.png"],
            },
            {
                "id": 20,
                "type": "ImageCrop",
                "mode": 4,  # BYPASSED
                "inputs": [
                    {"name": "image", "type": "IMAGE", "link": 100},
                    {"name": "mask", "type": "MASK", "link": None},
                ],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [200]}],
                "widgets_values": [],
            },
            {
                "id": 30,
                "type": "DestNode",
                "mode": 0,
                "inputs": [{"name": "image", "type": "IMAGE", "link": 200}],
                "outputs": [],
                "widgets_values": [],
            },
        ],
        "links": [
            [100, 10, 0, 20, 0, "IMAGE"],
            [200, 20, 0, 30, 0, "IMAGE"],
        ],
    }

    object_info = {
        "LoadImage": {"input": {"required": {"image": [["image.png"], {}]}}},
        "DestNode": {"input": {"required": {"image": ["IMAGE"]}}},
    }

    api = convert_to_api_format(ui_workflow, object_info)

    # Bypassed node (20) should not be in API workflow
    assert "20" not in api
    # DestNode should reference LoadImage directly, skipping bypassed node
    assert api["30"]["inputs"]["image"] == ["10", 0]


def test_bypassed_node_via_setget():
    """Bypassed node in SetNode/GetNode chain should resolve through."""
    ui_workflow = {
        "nodes": [
            {
                "id": 10,
                "type": "LoadImage",
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [100]}],
                "widgets_values": ["image.png"],
            },
            {
                "id": 20,
                "type": "ImageCrop",
                "mode": 4,  # BYPASSED
                "inputs": [{"name": "image", "type": "IMAGE", "link": 100}],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [200]}],
                "widgets_values": [],
            },
            {
                "id": 30,
                "type": "SetNode",
                "mode": 0,
                "inputs": [{"name": "IMAGE", "type": "IMAGE", "link": 200}],
                "outputs": [{"name": "*", "type": "*", "links": None}],
                "title": "Set_ref_img",
                "widgets_values": ["ref_img"],
            },
            {
                "id": 40,
                "type": "GetNode",
                "mode": 0,
                "inputs": [],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [300]}],
                "title": "Get_ref_img",
                "widgets_values": ["ref_img"],
            },
            {
                "id": 50,
                "type": "DestNode",
                "mode": 0,
                "inputs": [{"name": "image", "type": "IMAGE", "link": 300}],
                "outputs": [],
                "widgets_values": [],
            },
        ],
        "links": [
            [100, 10, 0, 20, 0, "IMAGE"],
            [200, 20, 0, 30, 0, "IMAGE"],
            [300, 40, 0, 50, 0, "IMAGE"],
        ],
    }

    object_info = {
        "LoadImage": {"input": {"required": {"image": [["image.png"], {}]}}},
        "DestNode": {"input": {"required": {"image": ["IMAGE"]}}},
    }

    api = convert_to_api_format(ui_workflow, object_info)

    # Bypassed node, SetNode, GetNode should all be absent
    assert "20" not in api
    assert "30" not in api
    assert "40" not in api
    # DestNode should resolve through GetNode → SetNode → bypassed → LoadImage
    assert api["50"]["inputs"]["image"] == ["10", 0]
