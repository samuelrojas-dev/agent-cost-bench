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

# A clean, minimal prompt; hide the setup so the recording opens on an empty prompt.
Hide
Type "export PS1='$ '"
Enter
Type "clear"
Enter
Show

Sleep 800ms
Type "dowbench scan examples/agent_tools.json --format plain"
Sleep 600ms
Enter
Sleep 3500ms
