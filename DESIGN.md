# Wokyis · Monochrome Instrument

User direction, 2026-10-17: retro geek/hacker, black and white, Hack font,
mechanical split-flap clocks, consistent material across calendar, Codex, Claude,
and a persistent terminal screen. These are project-specific choices.

Primary surface: Wokyis, roughly five inches, landscape. Observed native mode is
1280 × 720; also support the user's 1080 × 720 target. Physical legibility takes
precedence over fitting more information. Keep important text 27–32 px, headings
40–44 px, primary controls 60 px or taller. Summaries scroll, navigation stays fixed.

Use locally bundled Hack 3.003 regular/bold for Latin and digits, paired with
Sarasa Mono SC 1.0.42 regular/bold for CJK. Hack does not contain CJK glyphs.
All controls, preformatted text, headings and xterm share this explicit stack. The clock's large
dotted zero is intentional. Main tokens: background #080808, panel #141414,
text #eeeeee, secondary #b4b4b4, border #666666. Controlled semantic accents: green #8fe5a6, yellow #efd477, orange #f6ac6b,
red #ff7f82. Codex used-percent transitions at 70/85/95%; numbers always remain.

Signature: graphite split-flap plates with fine matte lines, shallow metal edges,
a black hinge seam, white printed digits, and a small side hinge. Other panels
use the same hard corners, thin bright edge, dark inset, and tactile button lip.
Texture stays behind text; no flickering CRT scan lines or bloom over content.

Clock motion is functional at digit changes. Respect reduced motion. Terminal
uses a steady block cursor. Preserve explicit status labels alongside color; never
use brightness alone to distinguish approval, running, or unknown status.

Seven bottom navigation buttons stay visible: New York, Shanghai, calendar,
To Do + health, Codex, Claude, terminal. Controls remain 60 px tall and over 110 px
wide at 1080 × 720. Horizontal trackpad gestures work over terminal content,
while vertical gestures, selection and keys remain terminal input. A wheel stream
locks its axis and may navigate once, including arbitrarily long momentum tails.
The native borderless window covers the menu bar without changing system defaults.

To Do + health is one balanced split view: readable task titles with one explicit
completion action; one large heart-rate number with freshness, then three smaller
recovery reference measurements. No invented health score or simulated heartbeat.
Recovery uses actual HRV, resting heart rate and sleep, each with a sample time.
Two tactile buttons acknowledge stand/water reminders and reset their timers.
Connection details live in a settings dialog, outside the primary glance view.

Terminal: locally hosted xterm.js with Hack 27 px and an isolated tmux socket.
Sessions survive page changes, reloads and application restarts, but not a system
reboot. Command-F7 previous; Command-F9 next; Command-F8 closes the visible
terminal. From another page, a shortcut opens Terminal first. New terminals are
explicitly created; no command is sent automatically on page visit.

Design references: [design-for-ai](https://github.com/ryanthedev/design-for-ai) by ryanthedev, for surface constraints and hierarchy;
[UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) by nextlevelbuilder, for touch/contrast guidance. These upstream skills are not included in this repository. The October 17 revision follows the user's updated
request for restrained terminal-green and quota warning colors. No general-purpose framework introduced.
