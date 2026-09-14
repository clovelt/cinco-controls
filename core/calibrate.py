"""
Interactive calibration.

Everything captured here is stored as a FRACTION of the game window's own
current bounds (0.0-1.0 relative to its top-left corner and width/height),
never as an absolute screen pixel. controller.py re-fetches the window's
actual bounds fresh on every single action and recomputes real screen
coordinates from these fractions each time -- so calibrating once keeps
working no matter where you later move the window or what size you resize
it to. This requires the game window to actually be findable (running,
on-screen) while you calibrate, since that's where the reference bounds
come from.

Board calibration (always runs): hover the mouse over the CENTER of two board
cells and press Enter for each, to teach controller.py where the 5x5 board
sits within the window.

Wand calibration (--wands): hover over each of the 5 wand icons (wherever
they sit in your UI) and press Enter, to teach controller.py where to
mouse-down to pick up each wand.

Menu calibration (--menu): hover over a point that's tappable both to
dismiss the death screen AND to press "start" on the menu that follows it
(both are the same spot for this game), used to auto-recover from death.
"""

import argparse
import json
import os
import sys

from pynput.mouse import Controller as MouseController

import controller
from state_reader import GRID_W, GRID_H

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def get_bounds_or_die():
    bounds = controller.get_game_window_bounds()
    if bounds is None:
        print("Could not find the Cinco Paus window. Make sure it's open, "
              "on-screen, and (on macOS) that this terminal has the "
              "permissions described in the README.")
        sys.exit(1)
    print(f"Using window bounds: {bounds}\n")
    return bounds


def to_frac(bounds, point):
    wx, wy, ww, wh = bounds
    x, y = point
    return [(x - wx) / ww, (y - wy) / wh]


def prompt_point(label):
    input(f"Hover the mouse over the CENTER of {label}, then press Enter...")
    x, y = MouseController().position
    print(f"  -> captured ({x:.0f}, {y:.0f})")
    return x, y


def load_existing():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def calibrate_board(config, bounds):
    print(f"Calibrating a {GRID_W}x{GRID_H} board.\n")
    top_left = prompt_point("the TOP-LEFT cell (grid 0,0)")
    bottom_right = prompt_point(f"the BOTTOM-RIGHT cell (grid {GRID_W - 1},{GRID_H - 1})")

    origin_frac = to_frac(bounds, top_left)
    br_frac = to_frac(bounds, bottom_right)
    step_frac = [
        (br_frac[0] - origin_frac[0]) / (GRID_W - 1),
        (br_frac[1] - origin_frac[1]) / (GRID_H - 1),
    ]
    config["origin_frac"] = origin_frac
    config["step_frac"] = step_frac

    print("\nSanity check -- computed screen position for each cell "
          "(should match the actual board right now):")
    for gy in range(GRID_H):
        row = []
        for gx in range(GRID_W):
            fx = origin_frac[0] + gx * step_frac[0]
            fy = origin_frac[1] + gy * step_frac[1]
            sx, sy = controller.point_to_screen(bounds, (fx, fy))
            row.append(f"({sx:.0f},{sy:.0f})")
        print("  " + " ".join(row))


def calibrate_wands(config, bounds):
    print("\nCalibrating 5 wand slots.\n")
    fracs = []
    for slot in range(1, 6):
        point = prompt_point(f"wand slot {slot}'s icon")
        fracs.append(to_frac(bounds, point))
    config["wand_positions_frac"] = fracs


def calibrate_menu(config, bounds):
    print("\nCalibrating the death/menu/start tap point.\n")
    point = prompt_point(
        "a spot that dismisses the death screen AND presses start on the "
        "menu after it (same point for both, one at a time)"
    )
    config["menu_point_frac"] = to_frac(bounds, point)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wands", action="store_true",
                         help="Also (or only, with --wands-only) calibrate wand icon positions")
    parser.add_argument("--wands-only", action="store_true",
                         help="Skip board calibration, only redo wand positions")
    parser.add_argument("--menu", action="store_true",
                         help="Also calibrate the death/menu/start tap point")
    parser.add_argument("--menu-only", action="store_true",
                         help="Skip board calibration, only redo the menu point")
    args = parser.parse_args()

    bounds = get_bounds_or_die()
    config = load_existing()

    if not args.wands_only and not args.menu_only:
        calibrate_board(config, bounds)
    if args.wands or args.wands_only:
        calibrate_wands(config, bounds)
    if args.menu or args.menu_only:
        calibrate_menu(config, bounds)

    save_config(config)
    print(f"\nSaved to {CONFIG_PATH}:")
    print(json.dumps(config, indent=2))
    print("\nThis is relative to the window -- you can move or resize it "
          "from now on and it'll keep working. Only recalibrate if you "
          "change the layout *within* the window (e.g. a different zoom "
          "level that changes where things sit proportionally).")


if __name__ == "__main__":
    main()
