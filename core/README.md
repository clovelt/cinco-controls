# cinco-controls

Keyboard movement AND wand casting for Cinco Paus (arrow keys / WASD, number
keys / zxcvb for wands), on any build, on any OS. Free, no paid automation
app, no OS-specific tool. Reads your live player position from the save data
and drags the mouse for you.

## How it works

1. `state_reader.py` finds and parses the live `.monkeystate` save data.
   - Desktop builds (itch.io, Windows/Mac/Linux): a plain `.monkeystate` file
     next to the game.
   - App Store build (Apple Silicon Mac only): that build doesn't write a
     bare file at all -- it stores the same string inside its own sandboxed
     preferences plist, under a key literally named `.monkeystate`. This is
     found automatically; nothing to configure.
2. `calibrate.py` records where the 5x5 board (and, with `--wands`, the 5
   wand icons) sit on your screen (run once).
3. `controller.py` only acts while Cinco Paus is the focused app (checked via
   `is_game_focused()`, macOS-only for now -- see Known Limitations), and
   listens for:
   - **arrow keys / WASD**: if no wand is held, swipes from a fixed anchor
     (the board's center) in that direction -- movement reads as a
     directional swipe gesture, not a drag to a specific tile, so it doesn't
     matter where on the board it starts.
   - **1-5 / z x c v b**: picks up that wand (mouse-down on its icon).
     Pressing the *same* slot again cancels it instead. While a wand is
     held, arrow keys / WASD aim + auto-cast it in that direction instead of
     moving -- mirroring the pickup -> aim -> hold -> release
     gesture an existing Hammerspoon build of this same idea already
     validated against the real game.

## Install

If you're using the `play_mac.command` / `play_win.bat` / `play_linux.sh`
launcher in the parent folder, this is fully automatic -- it creates a
private virtual environment here and installs everything into it. Skip to
Setup below.

Running these scripts directly instead, without the launcher:
```
pip install -r requirements.txt
```

## Setup

```
# Desktop build (itch.io) -- point at wherever you installed/extracted it:
python3 calibrate.py --wands
python3 controller.py --game-dir "/path/to/Cinco Paus"

# App Store build (Apple Silicon Mac) -- no --game-dir needed:
python3 calibrate.py --wands
python3 controller.py
```

`--wands` also asks you to hover over each of the 5 wand icons. Skip it if
you only want movement; add wand positions later any time by running
`python3 calibrate.py --wands-only` (keeps your existing board calibration).

Run `calibrate.py` again any time you move or resize the game window --
it's calibrated to on-screen pixel positions, not anything that adapts
automatically.

**Always try `--dry-run` first**: `python3 controller.py --dry-run` prints
what it *would* click, without moving your mouse at all. Confirm the
directions and coordinates look right before running it for real.

## Recovering from death automatically

Run `python3 calibrate.py --menu-only` once, hovering over a point that both
dismisses the death screen and (on the menu after it) presses start -- when
no active run is found, `controller.py` taps that point twice a beat apart
instead of just logging and doing nothing.

## Permissions

Both listening for global hotkeys and injecting mouse events require OS
input-monitoring permission for whatever runs `python3` (usually your
terminal app):

- **macOS**: System Settings -> Privacy & Security -> Accessibility, and
  also Input Monitoring. Add your terminal app, then restart it -- like any
  such permission on macOS, it only takes effect for processes launched
  *after* it's granted.
- **Windows**: no extra permission dialog, but some anti-cheat/anti-virus
  software flags synthetic input tools; whitelist it if that happens.
- **Linux**: see the Wayland section below -- this is the platform with
  real, by-design restrictions here, not just a permission checkbox.

## Known limitations

- **Focus-checking is macOS-only.** On Windows/Linux, hotkeys fire globally
  regardless of which window is active -- there's no `is_game_focused()`
  equivalent wired up for those platforms yet. Add one (e.g. checking the
  active window's title/process) before relying on this outside macOS.
- **A wand held when focus changes stays held** if you never press a
  direction/slot key again afterwards -- the mouse button that was
  synthetically pressed to pick it up never gets released. Press the same
  wand slot again (allowed even while unfocused, specifically for this) to
  release it safely if that happens.
- **Movement direction assumes a fixed screen orientation** (up arrow =
  decreasing grid y, etc. -- see the `DIRECTIONS` dict at the top of
  `controller.py`). This is a guess about how the board renders, not
  something read from the save file. If pressing up moves you sideways or
  down instead, flip the signs there.
- **Wall-aware move blocking isn't implemented.** The reader does parse wall
  data, but exact "which value means blocked" semantics weren't verified
  closely enough to gate input on. Worst case if you try to walk into a wall:
  the game just doesn't move you (the drag lands on a tile you can't reach,
  same as if you'd clicked it yourself) -- not a crash, just a no-op.
- **Casting aims `CAST_DRAG_CELLS` cell-widths past the player** (3 by
  default, i.e. well past the board edge in most positions) rather than
  landing on a specific tile. The original 1-cell version (matching the
  Hammerspoon reference literally) aimed unreliably in testing -- there's no
  retry/verification logic for casts even in that reference build, unlike
  movement, suggesting a short drag was always the fragile part. If it's
  still off, tune `CAST_DRAG_CELLS` at the top of `controller.py`.
- **No manual-confirm mode.** Casting always auto-releases after a short
  hold (`CAST_HOLD_DELAY`) instead of waiting for a separate confirm key --
  this matches the Hammerspoon reference's default setting, but there's no
  Enter-to-confirm alternative built in here if you'd prefer that.
- **Only tested against the App Store build's live save**, since that's what
  was available while building this. The desktop-build file paths in
  `state_reader.py` are based on inspecting the itch.io `.app` bundle
  structure on Mac; the Windows/Linux paths are a reasonable guess (same
  layout automancia itself assumes) but not verified against an actual
  Windows/Linux install -- if `--game-dir` doesn't find your save, check
  what's actually next to your game executable and adjust
  `find_desktop_source()` in `state_reader.py`.

## Linux + Wayland (read this before filing a "hotkeys don't work" issue)

If you're on Linux and your session is **Wayland** (increasingly the default
on modern distros -- GNOME, KDE, etc.), pynput's global hotkey listening and
mouse injection **will not work out of the box**. This isn't a bug in this
tool or in pynput: Wayland's security model deliberately blocks apps from
getting low-level keyboard/mouse access the way X11 allowed, specifically to
prevent exactly this kind of global input injection without the user's
explicit, per-session consent.

Workarounds if you're on Wayland:
- Check if your session is actually X11 first (many distros still default to
  it, or offer it as a login option) -- if so, none of this applies to you.
- Use **[ydotool](https://github.com/ydotool/ydotool)** instead of pynput's
  mouse/keyboard injection -- it works on both X11 and Wayland because it
  talks to the kernel's `uinput` module directly rather than going through
  the display server, but it needs its own daemon running (`ydotoold`) and
  typically a user group/permission setup to access `/dev/uinput`.
- Compositor-specific tools (e.g. on wlroots-based compositors like Sway)
  sometimes expose their own global-shortcut protocols; support varies a lot
  by compositor and isn't something this script tries to detect.

This is genuinely the least portable part of "free + cross-platform" input
automation in 2026 -- Windows and macOS don't have an equivalent wall.
