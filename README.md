# MimicBot

![MimicBot demo](demo.webp)

MimicBot is an RLBot bot that records an opponent's controller inputs and game state, then reproduces the play with its own car. It captures the opponent car and ball physics alongside input changes so replays stay aligned with the original movement.

## Inspiration

I created MimicBot after watching the video below. I wanted to build a bot that could do something similar to the freestyle bot shown there by recording a player's inputs and game state, then reproducing the play.

[Watch the inspiration video on YouTube](https://youtu.be/i-jJSrdbpWA)

[![Watch the freestyle bot video that inspired MimicBot](https://img.youtube.com/vi/i-jJSrdbpWA/hqdefault.jpg)](https://youtu.be/i-jJSrdbpWA)

## Controls

| Key | Action |
| --- | --- |
| `F3` | Replay the confirmed recording, then start a new recording on the next active tick |
| `F4` | Confirm and save the current recording draft |
| `F5` | Start or stop recording the opponent |
| `F6` | Replay the confirmed recording |
| `F7` | Export the confirmed recording |
| `F8` | Toggle the on-screen status display |
| `F9` | Import a recording from a file |

## Recording and replaying

1. Start a match with MimicBot and an opponent on the other team.
2. Press `F5` to begin recording. MimicBot automatically targets the first opposing car it finds.
3. Press `F5` again to stop. The result is kept as a draft, while the previously confirmed recording remains available.
4. Press `F4` to confirm the draft.
5. Press `F6` to replay it.

When the recorded opponent touches the ball, MimicBot trims unused footage shortly after the final touch. During replay, recorded controller inputs are reproduced while interpolated car and ball checkpoints keep the playback synchronized.

Use `F3` when you want to replay the current recording and immediately capture the opponent's response. Recording begins on the next active game tick after playback finishes.

## Importing and exporting

Press `F7` to export the confirmed recording to the `recordings` directory. Files use a versioned, gzip-compressed JSON format and have the `.json.gz` extension.

Press `F9` to open a file picker and import a previously exported recording. Imported recordings can be replayed or used as the starting point for the replay-then-record workflow.

## Installation

1. Download and install [RLBot](https://rlbot.org/).
2. Open RLBot and import this bot folder.
3. Add MimicBot and an opponent to a match.
4. Make sure state setting is enabled for the match, since replay synchronizes the recorded car and ball physics.
5. Start the match and use the controls above.

You can also launch the included RLBot configuration with `python run.py`, or open RLBotGUI with `python run_gui.py`.

## Requirements

- Windows, for the global function-key controls
- Python with the packages listed in `requirements.txt`
- RLBot 1.x
- Game-state setting enabled

## Notes

- MimicBot records the first car it finds on the opposing team.
- A draft does not replace the last confirmed recording until you press `F4`.
- Starting another recording discards any older unconfirmed draft.
- Export and replay operate on the confirmed recording, not the current draft.
