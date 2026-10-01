# demo.tape — the README's `dowbench scan` hero GIF, recorded with vhs.
#
# Reproduce (from the repo root):
#   pip install -e .
#   # and have vhs + its deps installed: https://github.com/charmbracelet/vhs
#   #   (vhs also needs `ttyd` and `ffmpeg` on PATH)
#   vhs docs/demo.tape          # writes docs/assets/scan-demo.gif
#
# The command shown reads tool schemas only — no API key, no provider call, no spend.

Output docs/assets/scan-demo.gif

Require dowbench

Set Shell bash
Set FontFamily "DejaVu Sans Mono"
Set FontSize 14
Set Width 1120
Set Height 900
Set Padding 24
Set Margin 0
Set Theme "Catppuccin Mocha"
Set Framerate 24
Set TypingSpeed 55ms

# A clean, minimal prompt. The leading newline means every prompt starts on its own line, so
# the next prompt never glues to the scan's last output line (the plain report, like many CLI
# outputs, ends without a trailing newline). Hide the setup so the recording opens clean.
Hide
Type "export PS1='\n$ '"
Enter
Type "clear"
Enter
Show

Sleep 800ms
Type "dowbench scan examples/agent_tools.json --format plain"
Sleep 600ms
Enter

# Hold on the full report; the next prompt lands cleanly on its own line (see PS1 above).
Sleep 3500ms
