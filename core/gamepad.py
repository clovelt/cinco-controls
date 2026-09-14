"""
Optional game controller support (needs `pip install pygame`, already in
requirements.txt). Polls the first connected controller: D-pad or left
stick -> movement directions, 4 face buttons -> wand slots 1-4, one shoulder
button -> wand slot 5, another -> confirm cast (Enter/Space equivalent).

Button layout varies a lot between controllers (Xbox/PlayStation/Switch/
generic all number things differently). The indices below match a typical
Xbox-style pad under SDL; if yours doesn't match, run with gamepad
debug=True (--gamepad-debug on both controller.py and dashboard.py) to
print every raw button index as you press it, then edit FACE_BUTTONS /
FIFTH_WAND_BUTTON / CONFIRM_BUTTON below.
"""

import os
import threading
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import controller as controller_module

DEADZONE = 0.5
POLL_INTERVAL = 0.05  # 20Hz -- plenty for turn-based input

FACE_BUTTONS = [0, 1, 2, 3]  # -> wand slots 1-4
FIFTH_WAND_BUTTON = 4        # -> wand slot 5
CONFIRM_BUTTON = 6           # -> Enter/Space (confirm cast)


def is_connected():
    try:
        import pygame
        return pygame.joystick.get_init() and pygame.joystick.get_count() > 0
    except Exception:
        return False


def start(ctrl, debug=False):
    """Best-effort: returns True if a controller was found and polling
    started, False (with a printed reason) otherwise. Never raises --
    this is always an optional extra on top of keyboard control."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    try:
        import pygame
    except ImportError:
        print("gamepad: pygame not installed (pip install pygame) -- skipping")
        return False

    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("gamepad: no controller detected -- skipping")
        return False

    joy = pygame.joystick.Joystick(0)
    joy.init()
    print(f"gamepad: using {joy.get_name()}")

    def loop():
        last_direction = None
        last_buttons = [False] * joy.get_numbuttons()
        while True:
            pygame.event.pump()

            if not controller_module.is_game_focused():
                # Same rule as keyboard: no action at all unless the game
                # is the focused app.
                time.sleep(POLL_INTERVAL)
                continue

            direction = None
            if joy.get_numhats() > 0:
                hx, hy = joy.get_hat(0)
                if hy == 1:
                    direction = "up"
                elif hy == -1:
                    direction = "down"
                elif hx == -1:
                    direction = "left"
                elif hx == 1:
                    direction = "right"
            if direction is None and joy.get_numaxes() >= 2:
                ax, ay = joy.get_axis(0), joy.get_axis(1)
                if abs(ax) > abs(ay) and abs(ax) > DEADZONE:
                    direction = "right" if ax > 0 else "left"
                elif abs(ay) > DEADZONE:
                    direction = "down" if ay > 0 else "up"

            if direction and direction != last_direction:
                if debug:
                    print(f"gamepad: direction {direction}")
                ctrl.queue_action("direction", direction)
            last_direction = direction

            buttons = [joy.get_button(i) for i in range(joy.get_numbuttons())]
            for i, pressed in enumerate(buttons):
                if pressed and not last_buttons[i]:
                    if debug:
                        print(f"gamepad: button {i} pressed")
                    if i in FACE_BUTTONS:
                        ctrl.queue_action("wand", FACE_BUTTONS.index(i))
                    elif i == FIFTH_WAND_BUTTON:
                        ctrl.queue_action("wand", 4)
                    elif i == CONFIRM_BUTTON:
                        ctrl.queue_action("confirm")
            last_buttons = buttons

            time.sleep(POLL_INTERVAL)

    threading.Thread(target=loop, daemon=True).start()
    return True
