"""
Unified single-window launcher: runs the game controller (keyboard -> mouse
bridge) AND the automancia web bridge (serves ../automancia-ios, mirrors the
live save into it) in one process, with a curses dashboard instead of raw
scrolling logs.

Usage: python3 dashboard.py [--game-dir PATH] [--dry-run] [--manual-cast]
(same flags as controller.py -- see its --help / README)

Dashboard controls (click or key, both work):
  Q / click [ QUIT ]                 - stop everything and exit
  M / click [ MANUAL CAST: ON/OFF ]  - toggle wand confirm-with-Enter mode
  A / click [ OPEN AUTOMANCIA ]      - open automancia in the browser
  W / click [ APP WINDOW ]           - open automancia in its own Chrome window
  R / click [ RECALIBRATE ]          - suspend the dashboard and run
                                        calibrate.py --wands in-place, no
                                        need to quit and retype a command
Game controls (arrows/WASD/ijkl, 1-5/zxcvb, Enter/Space) work globally,
exactly like controller.py -- only while Cinco Paus itself is focused.
"""

import argparse
import curses
import glob
import http.server
import os
import plistlib
import socketserver
import sys
import threading
import time
from collections import deque

from pynput import keyboard

import controller
from state_reader import find_source, get_state

HERE = os.path.dirname(os.path.abspath(__file__))
AUTOMANCIA_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "automancia-ios"))
BUNDLE_ID = "com.mightyvision.cinco"
HTTP_PORT = 8000

LOG_MAXLEN = 200

SYNCED_LABEL = "✓ Synced"
UNSYNCED_LABEL = "! Unsynced, wait"


class SyncStatus:
    """Tracks whether the last movement/cast has been reflected in the
    mirrored .monkeystate yet, so the dashboard can warn the user not to
    trust automancia's view of the game until the mirror catches up."""

    def __init__(self):
        self.lock = threading.Lock()
        self.synced = True

    def mark_action(self, **kwargs):
        with self.lock:
            self.synced = False

    def mark_synced(self):
        with self.lock:
            self.synced = True

    def is_synced(self):
        with self.lock:
            return self.synced


class LogBuffer:
    """Replaces print() with a bounded, thread-safe ring buffer the
    dashboard redraws from, instead of unbounded scrolling terminal spam."""

    def __init__(self, maxlen=LOG_MAXLEN):
        self.lines = deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def write(self, msg):
        text = msg.rstrip("\n")
        if not text:
            return
        text = text[0].upper() + text[1:]
        with self.lock:
            self.lines.append(time.strftime("%H:%M:%S ") + text)

    def flush(self):
        pass

    def tail(self, n):
        with self.lock:
            return list(self.lines)[-n:]


# ------------------------------------------------------------
# automancia bridge: same job as automancia-ios/serve.command, just as a
# background thread inside this process instead of its own window/process.
# ------------------------------------------------------------
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Same as SimpleHTTPRequestHandler but doesn't log every request --
    that's the "keeps logging almost the same line forever" complaint --
    and never lets the browser cache a response. automancia's files change
    while it's being used (and .monkeystate always needs a live read), and
    plain SimpleHTTPRequestHandler sends no cache headers at all, so
    browsers can silently keep showing a stale page until a hard refresh."""

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def find_plist():
    matches = glob.glob(os.path.expanduser(
        f"~/Library/Containers/*/Data/Library/Preferences/{BUNDLE_ID}.plist"
    ))
    return matches[0] if matches else None


def start_automancia_bridge(log, stop_event, sync_status):
    """Returns (ok, detail). Starts two daemon threads: one mirrors the
    live save into automancia-ios/.monkeystate, the other serves that
    folder over HTTP. No-op (returns False) if automancia-ios isn't next
    to this project or the App Store save can't be found -- the game
    controller itself doesn't need either of these to work."""
    if not os.path.isdir(AUTOMANCIA_DIR):
        return False, f"no automancia-ios folder at {AUTOMANCIA_DIR}"

    plist_path = find_plist()
    if not plist_path:
        return False, "App Store save plist not found (opened it at least once?)"

    out_path = os.path.join(AUTOMANCIA_DIR, ".monkeystate")

    def watch_loop():
        last = None
        while not stop_event.is_set():
            try:
                with open(plist_path, "rb") as f:
                    value = plistlib.load(f).get(".monkeystate")
                if value and value != last:
                    with open(out_path, "w") as f:
                        f.write(value)
                    last = value
                    sync_status.mark_synced()
                    log.write("Game saved, synced automancia")
            except Exception as e:
                log.write(f"automancia watch error: {e}")
            time.sleep(0.2)

    def serve_loop():
        os.chdir(AUTOMANCIA_DIR)
        socketserver.TCPServer.allow_reuse_address = True
        try:
            httpd = socketserver.TCPServer(("", HTTP_PORT), QuietHandler)
        except OSError as e:
            log.write(f"automancia server error: {e}")
            return
        httpd.timeout = 0.5
        while not stop_event.is_set():
            httpd.handle_request()
        httpd.server_close()

    threading.Thread(target=watch_loop, daemon=True).start()
    threading.Thread(target=serve_loop, daemon=True).start()
    return True, f"http://localhost:{HTTP_PORT}/"


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------
QUIT_LABEL = " QUIT (Q) "
TOGGLE_LABEL_FMT = " MANUAL CAST: {} (M) "
AUTOMANCIA_LABEL = " OPEN AUTOMANCIA (A) "
AUTOMANCIA_WINDOW_LABEL = " APP WINDOW (W) "
CALIBRATE_LABEL = " RECALIBRATE (R) "


class Dashboard:
    def __init__(self, stdscr, ctrl, log, automancia_status, sync_status):
        self.stdscr = stdscr
        self.ctrl = ctrl
        self.log = log
        self.automancia_status = automancia_status
        self.sync_status = sync_status
        self.should_quit = False
        self.buttons = {}  # label -> (y, x_start, x_end)

    def run(self):
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.timeout(150)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass

        while not self.should_quit:
            self.draw()
            self.handle_input()

    def draw(self):
        s = self.stdscr
        s.erase()
        h, w = s.getmaxyx()

        title = " cinco-controls dashboard "
        s.addstr(0, max(0, (w - len(title)) // 2), title, curses.A_REVERSE)

        ok, _ = self.automancia_status
        if ok:
            synced = self.sync_status.is_synced()
            sync_text = SYNCED_LABEL if synced else UNSYNCED_LABEL
            try:
                s.addstr(0, 1, sync_text,
                          curses.A_DIM if synced else curses.A_STANDOUT)
            except curses.error:
                pass

        focused = controller.is_game_focused()
        manual = "ON" if self.ctrl.manual_cast_confirm else "OFF"

        focus_text = f"Game focused: {'YES' if focused else 'no'}"
        s.addstr(0, max(0, w - len(focus_text) - 1), focus_text,
                  curses.A_REVERSE if focused else curses.A_DIM)

        try:
            import gamepad
            if gamepad.is_connected():
                gp_text = "Gamepad detected"
                s.addstr(1, max(0, w - len(gp_text) - 1), gp_text, curses.A_DIM)
        except ImportError:
            pass

        row = 2

        # Buttons (clickable AND keyboard-bound)
        self.buttons = {}
        bx = 2
        ok, detail = self.automancia_status
        button_defs = [(QUIT_LABEL, True), (TOGGLE_LABEL_FMT.format(manual), True),
                       (CALIBRATE_LABEL, True)]
        if ok:
            button_defs.append((AUTOMANCIA_LABEL, True))
            button_defs.append((AUTOMANCIA_WINDOW_LABEL, True))
        for label, on in button_defs:
            if not on:
                continue
            # Wrap to a new row instead of overrunning the terminal width --
            # addstr() raises if the string would extend past the last
            # column, and a 5th button (RECALIBRATE) was enough to do that
            # in an 80-column terminal, crashing the whole dashboard.
            if bx > 2 and bx + len(label) > w - 1:
                row += 1
                bx = 2
            try:
                s.addstr(row, bx, label, curses.A_STANDOUT)
                self.buttons[label] = (row, bx, bx + len(label))
            except curses.error:
                pass
            bx += len(label) + 2
        row += 2

        if ok:
            prefix = "Automancia: runs at "
            s.addstr(row, 2, prefix, curses.A_DIM)
            url_x = 2 + len(prefix)
            s.addstr(row, url_x, detail, curses.A_UNDERLINE)
            self.buttons["url"] = (row, url_x, url_x + len(detail))
        else:
            s.addstr(row, 2, f"Automancia: off ({detail})", curses.A_DIM)
        row += 1

        hint = "Keys: arrows/WASD/ijkl move, 1-5/zxcvb wands"
        if self.ctrl.manual_cast_confirm:
            hint += ", Enter/Space confirm cast"
        s.addstr(row, 2, hint, curses.A_DIM)
        row += 2

        s.addstr(row, 2, "-- Recent activity " + "-" * max(0, w - 24))
        row += 1
        for line in self.log.tail(h - row - 1):
            if row >= h - 1:
                break
            s.addstr(row, 2, line[:w - 4])
            row += 1

        s.refresh()

    def handle_input(self):
        try:
            ch = self.stdscr.getch()
        except curses.error:
            return
        if ch == -1:
            return

        if ch == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
            except curses.error:
                return
            if bstate & curses.BUTTON1_CLICKED:
                self.handle_click(my, mx)
            return

        try:
            c = chr(ch).lower()
        except ValueError:
            return
        if c == "q":
            self.should_quit = True
        elif c == "m":
            self.toggle_manual_cast()
        elif c == "a":
            self.open_automancia()
        elif c == "w":
            self.open_automancia_window()
        elif c == "r":
            self.run_calibration()

    def handle_click(self, y, x):
        for label, (row, x0, x1) in self.buttons.items():
            if y == row and x0 <= x < x1:
                if label == QUIT_LABEL:
                    self.should_quit = True
                elif label.startswith(" MANUAL CAST"):
                    self.toggle_manual_cast()
                elif label in (AUTOMANCIA_LABEL, "url"):
                    self.open_automancia()
                elif label == AUTOMANCIA_WINDOW_LABEL:
                    self.open_automancia_window()
                elif label == CALIBRATE_LABEL:
                    self.run_calibration()
                return

    def toggle_manual_cast(self):
        self.ctrl.manual_cast_confirm = not self.ctrl.manual_cast_confirm
        self.log.write(f"manual cast confirm -> {self.ctrl.manual_cast_confirm}")

    def open_automancia(self):
        ok, detail = self.automancia_status
        if not ok:
            self.log.write("automancia isn't running, nothing to open")
            return
        import subprocess
        subprocess.Popen(["open", detail])
        self.log.write(f"opened {detail} in browser")

    def open_automancia_window(self):
        ok, detail = self.automancia_status
        if not ok:
            self.log.write("automancia isn't running, nothing to open")
            return
        import subprocess
        if os.path.isdir("/Applications/Google Chrome.app"):
            subprocess.Popen([
                "open", "-na", "Google Chrome", "--args",
                f"--app={detail}",
            ])
            self.log.write(f"opened {detail} in its own window")
        else:
            subprocess.Popen(["open", detail])
            self.log.write(f"Chrome not found, opened {detail} in browser")

    def run_calibration(self):
        # calibrate.py is a plain blocking terminal script (input()/print()),
        # not curses-aware -- has to run with curses fully torn down first,
        # not just drawn over, or the two fight over the same terminal.
        curses.endwin()
        import subprocess
        try:
            print("\n--- Recalibrating: calibrate.py --wands ---\n")
            subprocess.call([sys.executable, os.path.join(HERE, "calibrate.py"), "--wands"])
            input("\nDone -- press Enter to return to the dashboard...")
        finally:
            self.stdscr.clear()
            curses.curs_set(0)
            self.stdscr.refresh()
        self.log.write("recalibrated (--wands)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manual-cast", action="store_true")
    parser.add_argument("--gamepad", action="store_true")
    parser.add_argument("--gamepad-debug", action="store_true")
    args = parser.parse_args()

    # Anything that can legitimately exit early (missing save, missing
    # config.json) must run BEFORE stdout gets redirected into the curses
    # log buffer -- otherwise the error message is swallowed into a buffer
    # nothing ever displays (curses never got a chance to start), and it
    # looks like the app just silently does nothing.
    source = find_source(args.game_dir)
    if not source:
        print("No save source found. Pass --game-dir for a desktop build, or "
              "make sure the macOS App Store build has been opened at least once.")
        sys.exit(1)

    config = controller.load_config()  # exits with a clear message if missing

    log = LogBuffer()
    sys.stdout = log  # only NOW does controller.py's print() land in the dashboard

    sync_status = SyncStatus()
    ctrl = controller.Controller(source, config, dry_run=args.dry_run,
                                  on_status=sync_status.mark_action)
    ctrl.manual_cast_confirm = args.manual_cast

    automancia_stop = threading.Event()
    automancia_status = start_automancia_bridge(log, automancia_stop, sync_status)

    ctrl.start_worker()
    ctrl.start_focus_watchdog()
    if args.gamepad or args.gamepad_debug:
        import gamepad
        gamepad.start(ctrl, debug=args.gamepad_debug)
    listener = keyboard.Listener(on_press=controller.make_on_press(ctrl))
    listener.start()

    def run_dashboard(stdscr):
        Dashboard(stdscr, ctrl, log, automancia_status, sync_status).run()

    try:
        curses.wrapper(run_dashboard)
    finally:
        sys.stdout = sys.__stdout__
        automancia_stop.set()
        listener.stop()
        ctrl.stop_worker()
        ctrl.stop_focus_watchdog()
        if ctrl.held_wand is not None:
            ctrl._force_release()
        print("stopped.")


if __name__ == "__main__":
    main()
