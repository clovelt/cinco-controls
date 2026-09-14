# cinco-controls

Keyboard movement AND wand casting for [Cinco Paus](https://mightyvision.itch.io/cinco-paus)
(arrow keys / WASD, number keys / zxcvb for wands), on any build, on any OS.
Free, no paid automation app, no OS-specific tool. Reads your live player
position from the save data and drags the mouse for you. Optional game
controller support too (D-pad/stick to move, face buttons for wands).

## Quickstart

1. Double-click the play file for your OS:
   - **macOS**: `play_mac.command` (first launch: right-click it and choose
     "Open" once, since it's an unsigned script and Gatekeeper will
     otherwise block it)
   - **Windows**: `play_win.bat`
   - **Linux**: `play_linux.sh`

   First run sets itself up automatically -- creates a private Python
   environment in `core/venv` and installs everything it needs into it.
   Every run after that skips straight to launching. The only thing you
   need pre-installed yourself is Python 3 (the script tells you where to
   get it if it can't find one).

2. The very first time, run calibration so it knows where the board and
   wand icons sit on your screen:
   ```
   core/venv/bin/python3 core/calibrate.py --wands
   ```
   (`core\venv\Scripts\python.exe` on Windows). Re-run it any time you move
   or resize the game window.

3. Run the play file again -- you now have a dashboard showing what it's
   doing, with arrow keys/WASD to move and 1-5/zxcvb to cast wands, only
   while Cinco Paus is the focused window.

See [core/README.md](core/README.md) for the full details: how the
movement/casting gestures work, permissions each OS requires, gamepad
support, automatic death-screen recovery, and known limitations (Linux
Wayland in particular needs a workaround -- read that section before filing
a "hotkeys don't work" issue there).

## Companion: automancia

If you also have [automancia](https://github.com/AurenSnyder/automancia)'s
`index.html`, `engine.js`, and `cincomancia/` folder placed in a sibling
`automancia-ios` folder next to this one, the dashboard can also serve and
open it for you (the `[ OPEN AUTOMANCIA ]` button). This is entirely
optional -- movement and wand casting work with or without it.

## Credits

Movement/casting design mirrors an existing Hammerspoon-based build of the
same idea, already validated against the real game; this is a from-scratch,
cross-platform (not just macOS, not tied to Hammerspoon) reimplementation.
Had a bit of help from Claude building this out.
