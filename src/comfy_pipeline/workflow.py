from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Frontend-only widget values inserted after seed/noise_seed inputs.
# These appear in widgets_values but NOT in object_info.
_SEED_CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}

# ComfyUI widget type names. Everything else is a connection type.
_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}


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

    # Collect Reroute node IDs for connection resolution
    reroute_ids: set[int] = set()

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
        elif ntype == "Reroute":
            reroute_ids.add(node["id"])

    # Build Reroute source map: reroute_node_id -> (upstream_node_id, upstream_slot)
    # Follow chains of Reroute nodes to their ultimate non-Reroute source.
    reroute_source: dict[int, tuple[int, int]] = {}
    for node_id in reroute_ids:
        src_node, src_slot = _resolve_reroute(
            node_id, nodes_by_id, link_map, reroute_ids
        )
        if src_node is not None:
            reroute_source[node_id] = (src_node, src_slot)

    # Node types to exclude from API workflow
    skip_types = {
        "SetNode", "GetNode", "Note", "Reroute", "PrimitiveNode",
        # rgthree UI-only nodes (not needed for API execution)
        "Fast Groups Bypasser (rgthree)", "Fast Groups Muter (rgthree)",
        "Image Comparer (rgthree)",
    }

    api_workflow = {}

    for node in ui_workflow["nodes"]:
        if node["type"] in skip_types:
            continue
        if node.get("mode", 0) in (2, 4):  # muted or bypassed
            continue
        # Skip nodes not recognized by the server
        if object_info and node["type"] not in object_info:
            logger.debug("Skipping unknown node type: %s", node["type"])
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

            # Resolve through Reroute, GetNode/SetNode, and bypassed nodes
            src_node, src_slot = _resolve_source(
                src_node, src_slot,
                nodes_by_id, link_map,
                reroute_ids, reroute_source,
                get_node_outputs, set_sources, set_node_ids,
            )
            if src_node is None:
                continue

            inputs[inp["name"]] = [str(src_node), src_slot]
            connected_names.add(inp["name"])

        # Determine which inputs are connection-only (no widget in UI).
        # These do NOT have entries in widgets_values even if their type
        # is STRING/INT/etc. Connection-only inputs appear in the node's
        # inputs array WITHOUT a "widget" key.
        connection_only_names: set[str] = set()
        for inp in node.get("inputs", []):
            if "widget" not in inp:
                connection_only_names.add(inp["name"])

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
            # Array format - map by order using object_info.
            # We need ALL widget names (including connected ones with "widget"
            # key) for positional alignment, but exclude connection-only inputs
            # which have no entry in widgets_values.
            all_widget_names = _get_widget_input_names(
                object_info[class_type], connection_only_names
            )
            _map_widget_values(widgets, all_widget_names, inputs, connected_names)

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


def _resolve_reroute(
    node_id: int,
    nodes_by_id: dict,
    link_map: dict,
    reroute_ids: set[int],
    _visited: set[int] | None = None,
) -> tuple[int | None, int]:
    """Follow a Reroute chain upstream until we reach a non-Reroute node.

    Returns (source_node_id, source_slot) or (None, 0) if the chain is broken.
    """
    if _visited is None:
        _visited = set()
    if node_id in _visited:
        return (None, 0)  # cycle detected
    _visited.add(node_id)

    node = nodes_by_id.get(node_id)
    if node is None:
        return (None, 0)

    # Reroute has a single input
    for inp in node.get("inputs", []):
        link_id = inp.get("link")
        if link_id is None or link_id not in link_map:
            continue
        src_node, src_slot = link_map[link_id]
        if src_node in reroute_ids:
            return _resolve_reroute(
                src_node, nodes_by_id, link_map, reroute_ids, _visited
            )
        return (src_node, src_slot)

    return (None, 0)


def _resolve_source(
    src_node: int,
    src_slot: int,
    nodes_by_id: dict,
    link_map: dict,
    reroute_ids: set[int],
    reroute_source: dict[int, tuple[int, int]],
    get_node_outputs: dict[int, str],
    set_sources: dict[str, tuple[int, int]],
    set_node_ids: set[int],
) -> tuple[int | None, int]:
    """Resolve a source node through Reroute, GetNode/SetNode, and bypassed nodes.

    ComfyUI bypassed nodes (mode=4) act as pass-through: first input → first
    output. This function follows the chain until reaching a concrete active node.

    Returns (source_node_id, source_slot) or (None, 0) if unresolvable.
    """
    visited: set[int] = set()

    while True:
        if src_node in visited:
            return (None, 0)  # cycle
        visited.add(src_node)

        # Resolve Reroute
        if src_node in reroute_ids:
            resolved = reroute_source.get(src_node)
            if resolved is None:
                return (None, 0)
            src_node, src_slot = resolved
            continue

        # Resolve GetNode → SetNode
        if src_node in get_node_outputs:
            var_name = get_node_outputs[src_node]
            if var_name in set_sources:
                src_node, src_slot = set_sources[var_name]
                continue
            return (None, 0)

        # Skip SetNodes (shouldn't happen after proper resolution)
        if src_node in set_node_ids:
            return (None, 0)

        src_obj = nodes_by_id.get(src_node)
        if src_obj is None:
            return (None, 0)

        mode = src_obj.get("mode", 0)

        # Muted nodes (mode=2): connection is dead
        if mode == 2:
            return (None, 0)

        # Bypassed nodes (mode=4): pass-through first input
        if mode == 4:
            found = False
            for inp in src_obj.get("inputs", []):
                link_id = inp.get("link")
                if link_id is not None and link_id in link_map:
                    src_node, src_slot = link_map[link_id]
                    found = True
                    break
            if not found:
                return (None, 0)
            continue

        # Active node — resolved
        return (src_node, src_slot)


def _map_widget_values(
    widgets: list, widget_names: list[str], inputs: dict,
    connected_names: set[str] | None = None,
) -> None:
    """Map widgets_values array to named inputs, skipping frontend-only widgets.

    ComfyUI's frontend inserts extra widgets (like 'control_after_generate'
    after seed inputs) that appear in widgets_values but NOT in object_info.
    We detect and skip them to keep alignment.

    widget_names includes ALL widget-type inputs (even connected ones) for
    positional alignment. connected_names tells us which to skip assigning.
    """
    if connected_names is None:
        connected_names = set()

    wi = 0  # index into widgets array
    ni = 0  # index into widget_names

    while ni < len(widget_names) and wi < len(widgets):
        name = widget_names[ni]
        val = widgets[wi]

        # Only assign if not overridden by a connection
        if name not in connected_names:
            inputs[name] = val

        wi += 1
        ni += 1

        # After a seed-like INT input, skip the frontend "control_after_generate"
        # widget if present (values: "fixed", "increment", "decrement", "randomize")
        if _is_seed_input(name) and wi < len(widgets):
            next_val = widgets[wi]
            if isinstance(next_val, str) and next_val.lower() in _SEED_CONTROL_VALUES:
                wi += 1  # skip the frontend-only widget


def _is_seed_input(name: str) -> bool:
    """Check if an input name is a seed-type input that has a control_after_generate."""
    name_lower = name.lower()
    return "seed" in name_lower


def _is_widget_input(spec: list) -> bool:
    """Determine if an object_info input spec describes a widget (not a connection).

    Widget inputs:
    - ["INT", {config}] or ["FLOAT", {config}] etc. with known widget types
    - [["option1", "option2"], ...] combo widgets with list as first element

    Connection inputs:
    - ["MODEL"] or ["MODEL", {"tooltip": "..."}] — type not in _WIDGET_TYPES
    """
    if not isinstance(spec, list) or len(spec) == 0:
        return False
    first = spec[0]
    # Combo widget: first element is a list of options
    if isinstance(first, list):
        return True
    # Known widget types: INT, FLOAT, STRING, BOOLEAN
    if isinstance(first, str) and first in _WIDGET_TYPES:
        return True
    return False


def _get_widget_input_names(
    node_info: dict, connected_names: set[str]
) -> list[str]:
    """Return ordered list of widget input names (excluding connected inputs).

    Uses _is_widget_input to correctly distinguish widget inputs (INT, FLOAT,
    STRING, BOOLEAN, combos) from connection-only inputs (MODEL, VAE, IMAGE,
    CONDITIONING, etc.) even when connection inputs have metadata.
    """
    names = []
    for section in ("required", "optional"):
        for name, spec in node_info.get("input", {}).get(section, {}).items():
            if name in connected_names:
                continue
            if _is_widget_input(spec):
                names.append(name)
    return names
