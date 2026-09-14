"""
Cross-platform, cross-build reader for Cinco Paus's .monkeystate save/live-state data.

Covers two very different storage mechanisms that both end up holding the exact
same comma/semicolon-separated string:

  - Desktop builds (itch.io, Windows/Mac/Linux): a plain ".monkeystate" file
    sitting next to the game, or inside "<Name>.app/Contents/Resources/" on Mac.
  - App Store build (Apple Silicon Mac "iPhone & iPad App"): no bare file at
    all -- the string lives inside the app's iOS-style preferences plist,
    under a key literally named ".monkeystate", in a sandbox container named
    by a random UUID rather than the bundle id (macOS only).

The save-string format itself (fields, offsets) was reverse-engineered from
AurenSnyder/automancia's decode()/doLogic() (github.com/AurenSnyder/automancia,
index.html), cross-checked against a real live save on 2026-09-10. This is an
informal reference for a companion tool, not a decompiled-source citation --
unrelated to the separate from-the-binary port2 transcription effort.
"""

import glob
import os
import platform
import plistlib
import time

BUNDLE_ID = "com.mightyvision.cinco"
GRID_W = 5
GRID_H = 5


class StateSource:
    """Points at wherever the live save actually is, and knows how to re-read it."""

    def __init__(self, kind, path):
        self.kind = kind  # "file" or "plist"
        self.path = path

    def read_raw(self):
        if self.kind == "file":
            with open(self.path, "r") as f:
                return f.read()
        elif self.kind == "plist":
            with open(self.path, "rb") as f:
                return plistlib.load(f).get(".monkeystate")
        raise ValueError(f"unknown source kind: {self.kind}")

    def __repr__(self):
        return f"StateSource({self.kind}, {self.path})"


def find_appstore_source():
    """macOS-only: the App Store build's sandboxed preferences plist."""
    if platform.system() != "Darwin":
        return None
    matches = glob.glob(os.path.expanduser(
        f"~/Library/Containers/*/Data/Library/Preferences/{BUNDLE_ID}.plist"
    ))
    return StateSource("plist", matches[0]) if matches else None


def find_desktop_source(game_dir):
    """
    Desktop (itch.io) builds on any OS: a bare .monkeystate file.

    game_dir is the folder you installed/extracted the game into. On Windows
    and (as far as documented -- not verified against an actual Linux build,
    confirm this yourself if it doesn't work) Linux, that's just
    "<game_dir>/.monkeystate". On Mac it's inside the .app bundle.
    """
    if not game_dir:
        return None
    candidates = [
        os.path.join(game_dir, ".monkeystate"),
        os.path.join(game_dir, "Cinco Paus.app", "Contents", "Resources", ".monkeystate"),
        os.path.join(game_dir, "Cinco Paus dev.app", "Contents", "Resources", ".monkeystate"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return StateSource("file", c)
    return None


def find_source(game_dir=None):
    """
    Try, in order: an explicit desktop game_dir, then (macOS only) the App
    Store container. Returns None if nothing is found.
    """
    src = find_desktop_source(game_dir)
    if src:
        return src
    return find_appstore_source()


def parse_monkeystate(raw):
    """
    Parse the raw save string into a plain dict. Field offsets per
    automancia's decode()/doLogic() -- see module docstring.
    """
    sections = raw.split(";")
    data = []
    for s in sections:
        if s == "":
            data.append([])
            continue
        data.append([float(x) for x in s.split(",") if x != "" and x != "-"])

    state = {"sections": data}
    if len(data) < 2:
        return state

    # Fields not touched below may legitimately be floats (animation timers,
    # RNG-derived offsets, etc.), so we int()-cast only the specific values
    # we actually read, on demand, rather than the whole array.
    z = data[1]
    if len(z) > 8:
        state["zone"] = int(z[1])
        state["keys"] = int(z[4])
        state["entrance"] = (int(z[5]), int(z[6]))
        state["exit"] = (int(z[7]), int(z[8]))

    # horizontal walls: 30 values @ [21,51)
    if len(z) >= 51:
        hwalls = {}
        for n in range(21, 51):
            x, y = (n - 21) // 6, (n - 21) % 6
            hwalls[(x, y)] = int(z[n])
        state["hwalls"] = hwalls

    # vertical walls: 30 values @ [51,81)
    if len(z) >= 81:
        vwalls = {}
        for n in range(51, 81):
            x, y = (n - 51) // 5, (n - 51) % 5
            vwalls[(x, y)] = int(z[n])
        state["vwalls"] = vwalls

    # player + enemies
    if len(z) > 246:
        count = int(z[245])
        base = 247
        player = None
        enemies = []
        for i in range(count):
            off = base + i * 11
            if off + 11 > len(z):
                break
            e = [int(v) for v in z[off:off + 11]]
            if e[0] == -1:
                player = {"x": e[1], "y": e[2], "hp": e[3]}
            else:
                enemies.append({
                    "type": e[0], "x": e[1], "y": e[2], "hp": e[3],
                    "sleep": e[4] == 1, "poisoned_by": e[5], "poison": e[6],
                    "friend": e[7] != 0, "trapped": e[10] != -1,
                })
        state["player"] = player
        state["enemies"] = enemies

    return state


def get_state(source):
    """Read + parse in one call."""
    return parse_monkeystate(source.read_raw())


def watch(source, callback, interval=0.2):
    """
    Block forever, calling callback(state_dict) each time the raw save
    string changes. Ctrl+C to stop.
    """
    last = None
    while True:
        try:
            raw = source.read_raw()
        except (FileNotFoundError, PermissionError):
            raw = None
        if raw and raw != last:
            last = raw
            callback(parse_monkeystate(raw))
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    game_dir = sys.argv[1] if len(sys.argv) > 1 else None
    src = find_source(game_dir)
    if not src:
        print("No save source found. Pass a game install folder as an argument "
              "for a desktop build, or make sure the App Store build has been "
              "opened at least once (macOS only).")
        sys.exit(1)
    print("source:", src)
    state = get_state(src)
    print("zone:", state.get("zone"), "keys:", state.get("keys"))
    print("player:", state.get("player"))
    print("enemies:", state.get("enemies"))
