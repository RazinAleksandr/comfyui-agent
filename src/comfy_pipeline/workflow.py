from __future__ import annotations

import json
from pathlib import Path


def load_workflow(filepath: str | Path) -> dict:
    with open(filepath) as f:
        return json.load(f)


def is_api_format(workflow: dict) -> bool:
    """API format: {node_id: {class_type, inputs}}. UI format: {nodes: [], links: []}."""
    return "nodes" not in workflow and "links" not in workflow


def convert_to_api_format(ui_workflow: dict, object_info: dict) -> dict:
    """Convert a ComfyUI UI-format workflow to API format using object_info metadata."""
    nodes_by_id = {node["id"]: node for node in ui_workflow["nodes"]}

    # Build link map: link_id -> (source_node_id, source_output_slot)
    link_map = {}
    for link in ui_workflow.get("links", []):
        link_id, from_node, from_slot = link[0], link[1], link[2]
        link_map[link_id] = (from_node, from_slot)

    # Resolve SetNode/GetNode variable passing (cg-use-everywhere)
    set_sources: dict[str, tuple[int, int]] = {}  # var_name -> (source_node, slot)
    set_node_ids: set[int] = set()
    get_node_outputs: dict[int, str] = {}  # get_node_id -> var_name

    for node in ui_workflow["nodes"]:
        ntype = node["type"]
        if ntype == "SetNode":
            set_node_ids.add(node["id"])
            var_name = _widget_val(node, 0, "")
            for inp in node.get("inputs", []):
                link_id = inp.get("link")
                if link_id is not None and link_id in link_map:
                    set_sources[var_name] = link_map[link_id]
        elif ntype == "GetNode":
            get_node_outputs[node["id"]] = _widget_val(node, 0, "")

    # Build API format
    skip_types = {"SetNode", "GetNode", "Note", "Reroute", "PrimitiveNode"}
    api_workflow = {}

    for node in ui_workflow["nodes"]:
        if node["type"] in skip_types:
            continue
        if node.get("mode", 0) in (2, 4):  # muted or bypassed
            continue

        node_id = str(node["id"])
        class_type = node["type"]
        inputs = {}
        connected_names: set[str] = set()

        # Process linked inputs
        for inp in node.get("inputs", []):
            link_id = inp.get("link")
            if link_id is None or link_id not in link_map:
                continue

            src_node, src_slot = link_map[link_id]

            # Resolve GetNode -> SetNode source
            if src_node in get_node_outputs:
                var_name = get_node_outputs[src_node]
                if var_name in set_sources:
                    src_node, src_slot = set_sources[var_name]
                else:
                    continue

            # Skip connections from SetNodes (shouldn't happen after resolution)
            if src_node in set_node_ids:
                continue

            # Skip if source node is bypassed/muted
            src = nodes_by_id.get(src_node)
            if src and src.get("mode", 0) in (2, 4):
                continue

            inputs[inp["name"]] = [str(src_node), src_slot]
            connected_names.add(inp["name"])

        # Process widget values
        widgets = node.get("widgets_values")
        if widgets is None:
            pass
        elif isinstance(widgets, dict):
            # Dict format - keys are explicit input names
            for key, val in widgets.items():
                if key not in connected_names and key != "videopreview":
                    inputs[key] = val
        elif isinstance(widgets, list) and class_type in object_info:
            # Array format - map by order using object_info
            widget_names = _get_widget_input_names(
                object_info[class_type], connected_names
            )
            for i, val in enumerate(widgets):
                if i < len(widget_names):
                    inputs[widget_names[i]] = val

        api_workflow[node_id] = {
            "class_type": class_type,
            "inputs": inputs,
        }

    return api_workflow


def inject_inputs(workflow: dict, injections: list[tuple[str, str, object]]) -> dict:
    """Inject values into workflow nodes.

    injections: list of (node_id, param_name, value)
    """
    workflow = json.loads(json.dumps(workflow))  # deep copy
    for node_id, param, value in injections:
        if node_id in workflow:
            workflow[node_id]["inputs"][param] = value
    return workflow


def apply_overrides(workflow: dict, overrides: dict[str, dict]) -> dict:
    """Apply parameter overrides to specific nodes."""
    workflow = json.loads(json.dumps(workflow))
    for node_id, params in overrides.items():
        if node_id in workflow:
            workflow[node_id]["inputs"].update(params)
    return workflow


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _widget_val(node: dict, index: int, default=None):
    widgets = node.get("widgets_values", [])
    if isinstance(widgets, list) and index < len(widgets):
        return widgets[index]
    return default


def _get_widget_input_names(
    node_info: dict, connected_names: set[str]
) -> list[str]:
    """Return ordered list of widget input names (excluding connected inputs).

    In object_info, connection-only inputs have spec like ["MODEL"] (1 element),
    while widget inputs have spec like ["INT", {config}] (2+ elements) or
    [["option1", "option2"], ...] (combo with list as first element).
    """
    names = []
    for section in ("required", "optional"):
        for name, spec in node_info.get("input", {}).get(section, {}).items():
            if name in connected_names:
                continue
            if not isinstance(spec, list) or len(spec) == 0:
                continue
            first = spec[0]
            # Combo widget: first element is a list of options
            if isinstance(first, list):
                names.append(name)
            # Basic widget types: INT, FLOAT, STRING, BOOLEAN + config dict
            elif isinstance(first, str) and len(spec) >= 2:
                names.append(name)
            # Single-element list like ["MODEL"] -> connection-only, skip
    return names
