"""Simulated lab bench equipment for the voice agent demo.

There is no real hardware here. Every tool just updates the in-memory STATE
dict, and app.py pushes that state to the browser, which draws the lab.

The module has three parts:
- STATE: the current room state
- TOOL_SCHEMAS: JSON-Schema definitions that create_agent.py attaches to the agent
- run_tool(): executes a tool call that the voice agent forwards to app.py
"""

import time

COLORS = ["white", "warm", "blue", "green", "red"]
PLAYLISTS = ["calm", "focus", "upbeat"]

STATE = {
    "scene": None,
    "lights": {"power": True, "color": "white", "brightness": 80},
    "blinds": "open",
    "music": {"playing": False, "playlist": "calm"},
    "camera": {"recording": False, "recording_since": None, "snapshots": 0},
}

# Presets that combine lights, blinds and music in one go.
SCENES = {
    "cell_culture": {"lights": {"power": True, "color": "white", "brightness": 100},
                     "blinds": "half", "music": {"playing": True, "playlist": "focus"}},
    "microscopy": {"lights": {"power": True, "color": "blue", "brightness": 15},
                   "blinds": "closed", "music": {"playing": True, "playlist": "calm"}},
    "cleanup": {"lights": {"power": True, "color": "white", "brightness": 100},
                "blinds": "open", "music": {"playing": True, "playlist": "upbeat"}},
    "end_of_day": {"lights": {"power": False, "color": "warm", "brightness": 40},
                   "blinds": "closed", "music": {"playing": False, "playlist": "calm"}},
}


# --- Tool implementations ----------------------------------------------------

def set_lights(power=None, color=None, brightness=None):
    lights = STATE["lights"]
    if power is not None:
        lights["power"] = power == "on"
    if color is not None:
        lights["color"] = color
        lights["power"] = True
    if brightness is not None:
        lights["brightness"] = max(0, min(100, int(brightness)))
        lights["power"] = lights["brightness"] > 0
    STATE["scene"] = None
    return {"ok": True, "lights": lights}


def set_blinds(position):
    STATE["blinds"] = position
    STATE["scene"] = None
    return {"ok": True, "blinds": position}


def set_scene(scene):
    preset = SCENES[scene]
    STATE["lights"] = dict(preset["lights"])
    STATE["blinds"] = preset["blinds"]
    STATE["music"] = dict(preset["music"])
    STATE["scene"] = scene
    return {"ok": True, "scene": scene, "applied": preset}


def play_music(action, playlist=None):
    music = STATE["music"]
    if playlist is not None:
        music["playlist"] = playlist
    music["playing"] = action == "play"
    return {"ok": True, "music": music}


def microscope_camera(action):
    camera = STATE["camera"]
    if action == "start_recording":
        if camera["recording"]:
            return {"ok": False, "message": "The microscope camera is already recording."}
        camera["recording"], camera["recording_since"] = True, time.time()
        return {"ok": True, "message": "Recording started."}
    if action == "stop_recording":
        if not camera["recording"]:
            return {"ok": False, "message": "The microscope camera is not recording."}
        seconds = int(time.time() - camera["recording_since"])
        camera["recording"], camera["recording_since"] = False, None
        return {"ok": True, "message": f"Recording stopped after {seconds} seconds."}
    camera["snapshots"] += 1
    return {"ok": True, "message": f"Snapshot {camera['snapshots']} saved."}


# --- Tool schemas (attached to the voice agent by create_agent.py) -----------

TOOL_SCHEMAS = [
    {
        "name": "set_lights",
        "description": "Control the lab bench lights: switch on or off, change colour, or dim.",
        "parameters": {
            "type": "object",
            "properties": {
                "power": {"type": "string", "enum": ["on", "off"]},
                "color": {"type": "string", "enum": COLORS},
                "brightness": {"type": "integer", "minimum": 0, "maximum": 100,
                               "description": "Brightness in percent."},
            },
        },
    },
    {
        "name": "set_blinds",
        "description": "Open, half-open, or close the window blinds.",
        "parameters": {
            "type": "object",
            "properties": {"position": {"type": "string", "enum": ["open", "half", "closed"]}},
            "required": ["position"],
        },
    },
    {
        "name": "set_scene",
        "description": "Apply a lab scene that sets lights, blinds, and music together.",
        "parameters": {
            "type": "object",
            "properties": {"scene": {"type": "string", "enum": list(SCENES)}},
            "required": ["scene"],
        },
    },
    {
        "name": "play_music",
        "description": "Play or pause background music, optionally switching the playlist.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["play", "pause"]},
                "playlist": {"type": "string", "enum": PLAYLISTS},
            },
            "required": ["action"],
        },
    },
    {
        "name": "microscope_camera",
        "description": "Control the microscope camera: start or stop a recording, or take a snapshot.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["start_recording", "stop_recording", "snapshot"]},
            },
            "required": ["action"],
        },
    },
]

_TOOLS = {
    "set_lights": set_lights,
    "set_blinds": set_blinds,
    "set_scene": set_scene,
    "play_music": play_music,
    "microscope_camera": microscope_camera,
}


def run_tool(name, args):
    """Execute a tool call from the voice agent and return a JSON-serializable result."""
    tool = _TOOLS.get(name)
    if tool is None:
        return {"ok": False, "message": f"Unknown tool '{name}'."}
    properties = next(t for t in TOOL_SCHEMAS if t["name"] == name)["parameters"]["properties"]
    for key, value in args.items():
        allowed = properties.get(key, {}).get("enum")
        if allowed and value not in allowed:
            return {"ok": False, "message": f"'{value}' is not valid for {key}. Options: {', '.join(allowed)}."}
    try:
        return tool(**args)
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "message": f"Invalid arguments for {name}: {exc}"}
