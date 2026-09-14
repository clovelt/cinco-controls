"""
Keyboard -> swipe/click bridge for Cinco Paus, using pynput (cross-platform:
Windows / macOS / Linux-X11 -- see README for the Linux/Wayland caveat).

Only acts while the game is the focused app (macOS-only check for now).
Movement swipes a fixed distance from a fixed board-center anchor in the
pressed direction. Wands are a pickup (mouse-down on the wand's icon) then a
direction key to aim + auto-release (or wait for Enter/Space with
--manual-cast). Dying/no active run taps a calibrated menu point twice to
dismiss the death screen and press start.

Needs config.json from calibrate.py before it can compute real screen
positions. Run with --dry-run to see what it *would* do without touching the
mouse at all -- do this first.
"""

import argparse
import json
import os
import platform
import sys
import threading
import time

from pynput import keyboard
from pynput.mouse import Button, Controller as MouseController

from state_reader import GRID_W, GRID_H, find_source, get_state

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

KNOWN_BUNDLE_IDS = {"com.mightyvision.cinco", "com.mightyvision.cincopaus"}


def frontmost_bundle_id():
    if platform.system() != "Darwin":
        return "(not macOS)"
    try:
        import Quartz
        from AppKit import NSRunningApplication
    except ImportError:
        return "(Quartz/AppKit unavailable)"
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in windows:
        if w.get("kCGWindowLayer") == 0:
            pid = w.get("kCGWindowOwnerPID")
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            return (app.bundleIdentifier() if app else None) or "(app with no bundle id)"
    return "(no on-screen app window found)"


def is_game_focused():
    if platform.system() != "Darwin":
        return True
    return frontmost_bundle_id() in KNOWN_BUNDLE_IDS


def get_game_window_bounds():
    """(x, y, width, height) of Cinco Paus's window in screen coordinates,
    fetched fresh every call -- never cached -- so calibration stays
    correct no matter where the window currently is or how big it is, even
    if it moved or was resized since the last check. Returns None if the
    game isn't running/visible (only meaningful to call while
    is_game_focused() is true anyway)."""
    if platform.system() != "Darwin":
        return None
    try:
        import Quartz
        from AppKit import NSRunningApplication
    except ImportError:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in windows:
        if w.get("kCGWindowLayer") != 0:
            continue
        pid = w.get("kCGWindowOwnerPID")
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app and app.bundleIdentifier() in KNOWN_BUNDLE_IDS:
            b = w.get("kCGWindowBounds")
            if b:
                return (b["X"], b["Y"], b["Width"], b["Height"])
    return None


def point_to_screen(bounds, frac):
    """frac = [fx, fy], each 0.0-1.0 relative to the window's top-left
    corner and width/height. This is the whole fix for "moving/resizing
    the window desyncs everything": nothing calibrated is ever stored as
    an absolute screen pixel, only as a fraction of the window's own
    current bounds, recomputed here every time from get_game_window_bounds()."""
    wx, wy, ww, wh = bounds
    return (wx + frac[0] * ww, wy + frac[1] * wh)


DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

KEY_TO_DIRECTION = {
    keyboard.Key.up: "up",
    keyboard.Key.down: "down",
    keyboard.Key.left: "left",
    keyboard.Key.right: "right",
}
CHAR_TO_DIRECTION = {
    "w": "up", "s": "down", "a": "left", "d": "right",
    "i": "up", "k": "down", "j": "left", "l": "right",
}

CHAR_TO_WAND_SLOT = {
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
    "z": 0, "x": 1, "c": 2, "v": 3, "b": 4,
}

MOVE_STEPS = 12
MOVE_STEP_DELAY = 0.010
# The anchor is the board's CENTER cell (index 2 of 0-4 on a 5-wide board),
# so the farthest any direction can go and stay ON the board is 2.0 cells.
# 2.5 was overshooting past the edge -- a drag that ends off the actual
# playable canvas is a solid explanation for "right" sometimes reading as
# something else entirely (down, in one report).
MOVE_DRAG_CELLS = 1.5
# Gap after releasing one swipe before the next one is allowed to start
# (reposition + press again). Without this, two swipes fired back-to-back
# (queued input, or just fast repeat) can have the cursor instantly jump
# backward to the next anchor a few ms after releasing -- close enough to
# the previous gesture that it can read as a single confused/reversed
# motion instead of two separate clean ones.
MOVE_SETTLE_DELAY = 0.08
# Wands pause after mouse-down before dragging (WAND_PICKUP_DELAY);
# movement never did -- it started interpolating on the very next line
# after press(). A gesture recognizer that samples direction from the
# first few post-touch-down events can misread it if motion starts before
# the touch-down itself has been fully registered. Give it the same dwell.
MOVE_PRESS_DELAY = 0.030

RECOVERY_COOLDOWN = 1.5
RECOVERY_TAP_GAP = 0.6
TAP_HOLD_DELAY = 0.08

FOCUS_POLL_INTERVAL = 0.25

WAND_PICKUP_DELAY = 0.030
WAND_DRAG_STEPS = 16
WAND_DRAG_STEP_DELAY = 0.013
CAST_HOLD_DELAY = 0.18
CAST_DRAG_CELLS = 1.5


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"No {CONFIG_PATH} found -- run calibrate.py first.")
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def cell_to_screen(config, bounds, gx, gy):
    """config stores origin/step as fractions of the window (origin_frac,
    step_frac) -- see calibrate.py. gx/gy are board-cell coordinates (can be
    fractional/out of 0-4 range for cast targeting, same as before)."""
    origin = config["origin_frac"]
    step = config["step_frac"]
    frac = (origin[0] + gx * step[0], origin[1] + gy * step[1])
    return point_to_screen(bounds, frac)


class Controller:
    def __init__(self, source, config, dry_run=False, on_status=None):
        self.source = source
        self.config = config
        self.dry_run = dry_run
        self.mouse = MouseController()
        self.recovery_busy_until = 0.0
        self.held_wand = None
        self.wand_origin = None
        self.manual_cast_confirm = False
        self.aiming = False
        self._watchdog_stop = threading.Event()
        self._worker_stop = threading.Event()
        # Single pending slot, not a queue: a new keypress OVERWRITES
        # whatever hasn't been picked up yet, it never appends. Mashing a
        # direction 4x while one swipe is still executing must result in at
        # most one more swipe after it settles, never a replay of all 4.
        self._pending_lock = threading.Lock()
        self._pending_action = None
        self._worker_wake = threading.Event()
        self.on_status = on_status or (lambda **kw: None)

    def queue_action(self, kind, value=None):
        with self._pending_lock:
            self._pending_action = (kind, value)
        self._worker_wake.set()

    def start_worker(self):
        def loop():
            while not self._worker_stop.is_set():
                if not self._worker_wake.wait(timeout=0.2):
                    continue
                with self._pending_lock:
                    action = self._pending_action
                    self._pending_action = None
                    self._worker_wake.clear()
                if action is None:
                    continue
                kind, value = action
                try:
                    if kind == "direction":
                        self.handle_direction(value)
                    elif kind == "wand":
                        self.select_wand(value)
                    elif kind == "confirm":
                        self.confirm_cast()
                except Exception as e:
                    print(f"worker error on {kind} {value}: {e}")
        threading.Thread(target=loop, daemon=True).start()

    def stop_worker(self):
        self._worker_stop.set()

    def start_focus_watchdog(self):
        def loop():
            while not self._watchdog_stop.wait(FOCUS_POLL_INTERVAL):
                if self.held_wand is not None and not is_game_focused():
                    print("focus lost with a wand held -- force-releasing")
                    self._force_release()
                    self.held_wand = None
                    self.wand_origin = None
                    self.aiming = False
        threading.Thread(target=loop, daemon=True).start()

    def stop_focus_watchdog(self):
        self._watchdog_stop.set()

    def _force_release(self):
        try:
            self.mouse.release(Button.left)
        except Exception:
            pass

    def _press_at(self, pos):
        """Post mouse-down AT AN EXPLICIT POSITION, instead of
        self.mouse.press() -- which posts at self.mouse.position, a LIVE
        read-back of the real system cursor via NSEvent.mouseLocation().
        `self.mouse.position = pos` just fires an async CGEventPost and
        returns immediately; there's no guarantee the OS has actually
        applied it before the very next line reads it back. When it
        hasn't, the down-event lands wherever the cursor was left over
        from before (e.g. the previous swipe's target) -- nowhere near
        `pos` -- and the game computes its swipe direction starting from
        THAT real touch-down point, not from where we meant to press. This
        is the root cause of "cursor visibly swipes the right way, game
        reads it as some other direction almost every time": the visible
        animation is correct, the touch-down coordinate silently wasn't."""
        if platform.system() != "Darwin":
            self.mouse.position = pos
            self.mouse.press(Button.left)
            return
        import Quartz
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, pos, Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        # Mirror pynput's own bookkeeping so a later self.mouse.position =
        # ... (used by _smooth_move right after) posts "Dragged" events
        # instead of plain "Moved", and self.mouse.release() targets the
        # right button.
        self.mouse._drag_button = Button.left

    def _release_at(self, pos):
        """Same fix as _press_at, for mouse-up: post it at an explicit
        position instead of trusting self.mouse.position's live read-back
        to already reflect where our own drag just finished."""
        if platform.system() != "Darwin":
            self.mouse.position = pos
            self.mouse.release(Button.left)
            return
        import Quartz
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, pos, Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self.mouse._drag_button = None

    def handle_direction(self, direction):
        if self.held_wand is not None:
            self.cast_direction(direction)
        else:
            self.move_direction(direction)

    def move_direction(self, direction):
        confirmed, player = self._read_player()
        if confirmed and player is None:
            self._handle_no_player()
            return

        bounds = get_game_window_bounds()
        if bounds is None:
            print("move: game window not found, ignoring")
            return

        dx, dy = DIRECTIONS[direction]
        cx, cy = (GRID_W - 1) / 2, (GRID_H - 1) / 2
        anchor = cell_to_screen(self.config, bounds, cx, cy)
        target = cell_to_screen(self.config, bounds,
                                 cx + dx * MOVE_DRAG_CELLS, cy + dy * MOVE_DRAG_CELLS)

        print(f"move {direction}: swipe {anchor} -> {target}")

        if self.dry_run:
            return

        self._press_at(anchor)
        time.sleep(MOVE_PRESS_DELAY)
        self._smooth_move(anchor, target)
        self._release_at(target)
        time.sleep(MOVE_SETTLE_DELAY)
        self.on_status(action=True)

    def _handle_no_player(self):
        now = time.time()
        if now < self.recovery_busy_until:
            return
        self.recovery_busy_until = now + RECOVERY_COOLDOWN

        menu_frac = self.config.get("menu_point_frac")
        if not menu_frac:
            print("no menu point calibrated -- run calibrate.py --menu "
                  "so death/menu screens can be dismissed automatically")
            return

        bounds = get_game_window_bounds()
        if bounds is None:
            print("no active run, but game window not found -- can't tap menu")
            return
        pos = point_to_screen(bounds, menu_frac)

        print(f"no active run -- tapping {pos} to dismiss death screen, "
              f"then again to press start")
        if self.dry_run:
            return

        self._tap(pos)
        time.sleep(RECOVERY_TAP_GAP)
        self._tap(pos)

    def _tap(self, pos):
        self._force_release()
        self._press_at(pos)
        time.sleep(TAP_HOLD_DELAY)
        self._release_at(pos)

    def select_wand(self, slot):
        wand_fracs = self.config.get("wand_positions_frac")
        if not wand_fracs or slot >= len(wand_fracs) or not wand_fracs[slot]:
            print(f"wand {slot + 1}: not calibrated -- run calibrate.py --wands")
            return

        if self.held_wand == slot:
            print(f"wand {slot + 1}: same slot, cancelling")
            self._cancel_wand()
            return

        if self.held_wand is not None:
            self._cancel_wand()

        bounds = get_game_window_bounds()
        if bounds is None:
            print(f"wand {slot + 1}: game window not found, ignoring")
            return
        pos = point_to_screen(bounds, wand_fracs[slot])
        print(f"wand {slot + 1}: pick up at {pos}")
        self.held_wand = slot
        self.wand_origin = pos

        if self.dry_run:
            return

        self._press_at(pos)
        time.sleep(WAND_PICKUP_DELAY)

    def cast_direction(self, direction):
        confirmed, player = self._read_player()
        if not confirmed or not player:
            self._cancel_wand()
            return

        bounds = get_game_window_bounds()
        if bounds is None:
            print("cast: game window not found, cancelling")
            self._cancel_wand()
            return

        dx, dy = DIRECTIONS[direction]
        target_screen = cell_to_screen(
            self.config, bounds,
            player["x"] + dx * CAST_DRAG_CELLS,
            player["y"] + dy * CAST_DRAG_CELLS,
        )

        verb = "aim" if self.manual_cast_confirm else "cast"
        print(f"{verb} wand {self.held_wand + 1} {direction}: "
              f"from {self.wand_origin} -> {target_screen}")

        if not self.dry_run:
            self._smooth_move(self.wand_origin, target_screen,
                               steps=WAND_DRAG_STEPS, step_delay=WAND_DRAG_STEP_DELAY)

        self.aiming = True

        if self.manual_cast_confirm:
            return

        if not self.dry_run:
            time.sleep(CAST_HOLD_DELAY)
            self._release_at(target_screen)
        self.held_wand = None
        self.wand_origin = None
        self.aiming = False
        self.on_status(action=True)

    def confirm_cast(self):
        if self.held_wand is None or not self.aiming:
            return
        print(f"confirm cast wand {self.held_wand + 1}")
        if not self.dry_run:
            time.sleep(CAST_HOLD_DELAY)
            self.mouse.release(Button.left)
        self.held_wand = None
        self.wand_origin = None
        self.aiming = False
        self.on_status(action=True)

    def _cancel_wand(self):
        if self.held_wand is None:
            return
        if not self.dry_run and self.wand_origin:
            self._smooth_move(self.mouse.position, self.wand_origin,
                               steps=WAND_DRAG_STEPS, step_delay=WAND_DRAG_STEP_DELAY)
            self._release_at(self.wand_origin)
        self.held_wand = None
        self.wand_origin = None
        self.aiming = False

    def _read_player(self):
        try:
            state = get_state(self.source)
        except Exception as e:
            print(f"state read glitch, ignoring ({type(e).__name__}: {e})")
            return False, None
        return True, state.get("player")

    def _smooth_move(self, src, dst, steps=MOVE_STEPS, step_delay=MOVE_STEP_DELAY):
        self.mouse.position = src
        for i in range(1, steps + 1):
            t = i / steps
            x = src[0] + (dst[0] - src[0]) * t
            y = src[1] + (dst[1] - src[1]) * t
            self.mouse.position = (x, y)
            time.sleep(step_delay)


def make_on_press(ctrl):
    def on_press(key):
        if not is_game_focused():
            return

        if key in (keyboard.Key.enter, keyboard.Key.space):
            ctrl.queue_action("confirm")
            return

        direction = KEY_TO_DIRECTION.get(key)
        char = getattr(key, "char", None)
        if direction is None and char:
            direction = CHAR_TO_DIRECTION.get(char.lower())

        if direction:
            ctrl.queue_action("direction", direction)
            return

        if char and char.lower() in CHAR_TO_WAND_SLOT:
            ctrl.queue_action("wand", CHAR_TO_WAND_SLOT[char.lower()])

    return on_press


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manual-cast", action="store_true")
    parser.add_argument("--gamepad", action="store_true",
                         help="Also accept input from a connected game controller "
                              "(needs pygame -- pip install pygame)")
    parser.add_argument("--gamepad-debug", action="store_true",
                         help="Print raw gamepad axis/button indices as you press "
                              "them, to help remap gamepad.py for your controller")
    args = parser.parse_args()

    source = find_source(args.game_dir)
    if not source:
        print("No save source found. Pass --game-dir for a desktop build, or "
              "make sure the macOS App Store build has been opened at least once.")
        sys.exit(1)
    print("reading state from:", source)

    config = load_config()
    ctrl = Controller(source, config, dry_run=args.dry_run)
    ctrl.manual_cast_confirm = args.manual_cast

    if args.dry_run:
        print("--dry-run: will print moves without touching the mouse\n")

    print("listening for arrow keys / WASD / ijkl (move) and 1-5 / zxcvb "
          "(wands), Enter/Space (confirm cast), Ctrl+C to stop...")
    if platform.system() != "Darwin":
        print("NOTE: no focus-check on this OS yet.")

    ctrl.start_worker()
    ctrl.start_focus_watchdog()
    if args.gamepad or args.gamepad_debug:
        import gamepad
        gamepad.start(ctrl, debug=args.gamepad_debug)
    try:
        with keyboard.Listener(on_press=make_on_press(ctrl)) as listener:
            listener.join()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        ctrl.stop_worker()
        ctrl.stop_focus_watchdog()
        if ctrl.held_wand is not None:
            ctrl._force_release()


if __name__ == "__main__":
    main()
