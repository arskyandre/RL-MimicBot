# Opponent Input Recorder

This RLBot records an opponent's controller inputs and replays them from the
same initial car and ball state.

## Quick Start

- Start a match with this bot and at least one opponent.
- Press **F3** to play the committed replay and automatically begin recording
  the opponent on the first game tick after playback finishes.
- Press **F4** after recording to confirm that the draft should replace the
  previously saved replay.
- Press **F5** to begin recording the first opponent on the other team.
- Press **F5** again to stop recording and create an unconfirmed draft.
- Press **F6** to replay.
- Press **F7** to export the current recording.
- Press **F8** to hide or show the on-screen status text.
- Press **F9** to choose and import a recording using a file dialog.

Until **F4** confirms a finished recording, **F6** and **F7** continue using the
previous replay. Starting another recording discards the unconfirmed draft.

When recording stops, the draft is trimmed to two recorded game ticks after the
opponent's final touch on the ball. If the opponent did not touch the ball, the
full recording is retained.

When replay begins, the bot is teleported to the opponent's recorded initial
position, rotation, velocity, angular velocity, boost, and jump state. The ball
is restored to its recorded initial physics state, and the bot reproduces the
opponent's controller changes with their original timing.

The opponent and ball physics are captured every game tick. During replay, the
current recorded car and ball state is reapplied every tick. Physics values are
interpolated between surrounding recorded ticks to reduce visible snapping.

Exports are saved as versioned, compressed JSON files in the project-level
`recordings` folder. The import dialog opens in that folder by default but can
load a recording from another location.

State setting must be enabled. The included `rlbot.cfg` already has
`enable_state_setting = True`.

Status and recording duration are shown in the upper-left corner in game.

## Replay Trimmer

Double-click recordings\Launch Replay Trimmer.cmd to open the standalone
editor. Select an exported replay, choose start and end times, and save a
trimmed .json.gz copy. The editor preserves the active controller input at
the new start and interpolates exact physics states at both trim boundaries.
