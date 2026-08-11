import ctypes
import gzip
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

from rlbot.agents.base_agent import BaseAgent, SimpleControllerState
from rlbot.messages.flat.ControllerState import ControllerState
from rlbot.messages.flat.PlayerInputChange import PlayerInputChange
from rlbot.socket.socket_manager import SocketRelay
from rlbot.utils.game_state_util import (
    BallState,
    CarState,
    GameState,
    Physics,
    Rotator,
    Vector3,
)
from rlbot.utils.structures.game_data_struct import GameTickPacket


VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
RECORDING_FORMAT = 'rocketbot-input-recording'
RECORDING_VERSION = 1


def controls_from_flat(controls: ControllerState) -> SimpleControllerState:
    """Copy a socket controller message before its receive buffer is reused."""
    return SimpleControllerState(
        throttle=controls.Throttle(),
        steer=controls.Steer(),
        pitch=controls.Pitch(),
        yaw=controls.Yaw(),
        roll=controls.Roll(),
        jump=bool(controls.Jump()),
        boost=bool(controls.Boost()),
        handbrake=bool(controls.Handbrake()),
        use_item=bool(controls.UseItem()),
    )


def copy_controls(controls: SimpleControllerState) -> SimpleControllerState:
    return SimpleControllerState(
        throttle=controls.throttle,
        steer=controls.steer,
        pitch=controls.pitch,
        yaw=controls.yaw,
        roll=controls.roll,
        jump=controls.jump,
        boost=controls.boost,
        handbrake=controls.handbrake,
        use_item=controls.use_item,
    )


def copy_vector(vector) -> Vector3:
    return Vector3(x=vector.x, y=vector.y, z=vector.z)


def copy_rotator(rotation) -> Rotator:
    return Rotator(
        pitch=rotation.pitch,
        yaw=rotation.yaw,
        roll=rotation.roll,
    )


def copy_physics(physics) -> Physics:
    return Physics(
        location=copy_vector(physics.location),
        rotation=copy_rotator(physics.rotation),
        velocity=copy_vector(physics.velocity),
        angular_velocity=copy_vector(physics.angular_velocity),
    )


def controls_to_data(controls: SimpleControllerState):
    return [
        controls.throttle,
        controls.steer,
        controls.pitch,
        controls.yaw,
        controls.roll,
        controls.jump,
        controls.boost,
        controls.handbrake,
        controls.use_item,
    ]


def controls_from_data(data) -> SimpleControllerState:
    if len(data) != 9:
        raise ValueError('A controller entry must contain 9 values')
    return SimpleControllerState(
        throttle=float(data[0]),
        steer=float(data[1]),
        pitch=float(data[2]),
        yaw=float(data[3]),
        roll=float(data[4]),
        jump=bool(data[5]),
        boost=bool(data[6]),
        handbrake=bool(data[7]),
        use_item=bool(data[8]),
    )


def vector_to_data(vector):
    return [vector.x, vector.y, vector.z]


def vector_from_data(data) -> Vector3:
    if len(data) != 3:
        raise ValueError('A vector entry must contain 3 values')
    return Vector3(x=float(data[0]), y=float(data[1]), z=float(data[2]))


def physics_to_data(physics: Physics):
    return {
        'location': vector_to_data(physics.location),
        'rotation': [
            physics.rotation.pitch,
            physics.rotation.yaw,
            physics.rotation.roll,
        ],
        'velocity': vector_to_data(physics.velocity),
        'angular_velocity': vector_to_data(physics.angular_velocity),
    }


def physics_from_data(data) -> Physics:
    rotation = data['rotation']
    if len(rotation) != 3:
        raise ValueError('A rotation entry must contain 3 values')
    return Physics(
        location=vector_from_data(data['location']),
        rotation=Rotator(
            pitch=float(rotation[0]),
            yaw=float(rotation[1]),
            roll=float(rotation[2]),
        ),
        velocity=vector_from_data(data['velocity']),
        angular_velocity=vector_from_data(data['angular_velocity']),
    )


def car_state_to_data(car_state: CarState):
    return {
        'physics': physics_to_data(car_state.physics),
        'boost': car_state.boost_amount,
        'jumped': car_state.jumped,
        'double_jumped': car_state.double_jumped,
    }


def car_state_from_data(data) -> CarState:
    return CarState(
        physics=physics_from_data(data['physics']),
        boost_amount=float(data['boost']),
        jumped=bool(data['jumped']),
        double_jumped=bool(data['double_jumped']),
    )


class MyBot(BaseAgent):

    def __init__(self, name, team, index):
        super().__init__(name, team, index)

        self.target_index = None
        self.target_name = 'No opponent'
        self.latest_target_controls = SimpleControllerState()

        self.recording = False
        self.record_start_time = None
        self.recording_duration = 0.0
        self.recorded_opponent_name = 'Unknown opponent'
        self.recorded_inputs = []
        self.recorded_car_state = None
        self.recorded_ball_state = None
        self.recorded_checkpoints = []
        self.recording_backup = None
        self.pending_recording = None
        self.last_seen_touch_time = 0.0
        self.last_touch_checkpoint_index = None

        self.replaying = False
        self.replay_start_time = None
        self.replay_duration = 0.0
        self.replay_inputs = []
        self.replay_input_index = 0
        self.replay_controls = SimpleControllerState()
        self.replay_checkpoints = []
        self.replay_checkpoint_index = 0
        self.record_after_replay = False
        self.start_recording_on_next_tick = False

        self.status_message = 'F3: replay then record | F5: record'
        self.render_text = True
        self.recordings_directory = (
            Path(__file__).resolve().parent.parent / 'recordings'
        )
        self.import_dialog_open = False
        self.pending_import_result = None
        self.key_was_down = {
            VK_F3: False,
            VK_F4: False,
            VK_F5: False,
            VK_F6: False,
            VK_F7: False,
            VK_F8: False,
            VK_F9: False,
        }
        self.input_lock = threading.Lock()

        self.socket_relay = SocketRelay()
        self.socket_relay.player_input_change_handlers.append(
            self.track_opponent_inputs
        )
        self.socket_thread = None

    def initialize_agent(self):
        self.socket_thread = threading.Thread(
            target=self.run_socket_relay,
            name=f'{self.name}-input-recorder',
            daemon=True,
        )
        self.socket_thread.start()

    def run_socket_relay(self):
        self.socket_relay.connect_and_run(
            wants_quick_chat=False,
            wants_game_messages=True,
            wants_ball_predictions=False,
        )

    def track_opponent_inputs(
        self,
        change: PlayerInputChange,
        seconds: float,
        frame_num: int,
    ):
        if change.PlayerIndex() != self.target_index:
            return

        controls = controls_from_flat(change.ControllerState())
        with self.input_lock:
            self.latest_target_controls = controls
            if self.recording and self.record_start_time is not None:
                relative_time = max(0.0, seconds - self.record_start_time)
                self.recorded_inputs.append(
                    (relative_time, copy_controls(controls))
                )

    def get_output(self, packet: GameTickPacket) -> SimpleControllerState:
        now = packet.game_info.seconds_elapsed
        self.find_opponent(packet)
        self.consume_import_dialog_result()

        if self.start_recording_on_next_tick:
            if packet.game_info.is_round_active:
                self.start_recording_on_next_tick = False
                self.start_recording(packet, now)
            else:
                self.draw_status()
                return SimpleControllerState()

        replay_then_record_pressed = self.key_pressed(VK_F3)
        confirm_pressed = self.key_pressed(VK_F4)
        record_pressed = self.key_pressed(VK_F5)
        replay_pressed = self.key_pressed(VK_F6)
        export_pressed = self.key_pressed(VK_F7)
        render_toggle_pressed = self.key_pressed(VK_F8)
        import_pressed = self.key_pressed(VK_F9)

        if render_toggle_pressed:
            self.render_text = not self.render_text

        if replay_then_record_pressed:
            if self.recording:
                self.stop_recording(now)
            self.record_after_replay = True
            self.start_replay(now)
            if self.replaying:
                self.status_message = 'REPLAYING, THEN RECORDING'
            else:
                self.record_after_replay = False

        if confirm_pressed:
            self.confirm_pending_recording()

        if record_pressed:
            self.record_after_replay = False
            self.start_recording_on_next_tick = False
            if self.recording:
                self.stop_recording(now)
            else:
                self.start_recording(packet, now)

        if replay_pressed:
            self.record_after_replay = False
            self.start_recording_on_next_tick = False
            self.start_replay(now)

        if export_pressed:
            if self.recording:
                self.stop_recording(now)
            self.export_recording()

        if import_pressed:
            if self.recording:
                self.stop_recording(now)
            self.request_import_dialog()

        if not packet.game_info.is_round_active:
            if self.recording:
                self.stop_recording(now)
            self.replaying = False
            self.record_after_replay = False
            self.start_recording_on_next_tick = False
            self.draw_status()
            return SimpleControllerState()

        if self.recording:
            self.recording_duration = max(0.0, now - self.record_start_time)
            self.record_physics_checkpoints(packet)
            self.track_recorded_ball_touch(packet)
            self.status_message = (
                f'RECORDING {self.recording_duration:.2f}s '
                f'({len(self.recorded_inputs)} inputs, '
                f'{len(self.recorded_checkpoints)} checkpoints)'
            )
            self.draw_status()
            return SimpleControllerState()

        if self.replaying:
            controls = self.get_replay_controls(now)
            self.draw_status()
            return controls

        self.draw_status()
        return SimpleControllerState()

    def find_opponent(self, packet: GameTickPacket):
        if (
            self.target_index is not None
            and self.target_index < packet.num_cars
            and packet.game_cars[self.target_index].team != self.team
        ):
            self.target_name = packet.game_cars[self.target_index].name
            return

        self.target_index = None
        self.target_name = 'No opponent'
        for index in range(packet.num_cars):
            car = packet.game_cars[index]
            if index != self.index and car.team != self.team:
                self.target_index = index
                self.target_name = car.name
                return

    def start_recording(self, packet: GameTickPacket, now: float):
        if self.target_index is None:
            self.status_message = 'Cannot record: no opponent found'
            return

        opponent = packet.game_cars[self.target_index]
        draft_car_state = CarState(
            physics=copy_physics(opponent.physics),
            boost_amount=opponent.boost,
            jumped=opponent.jumped,
            double_jumped=opponent.double_jumped,
        )
        draft_ball_state = BallState(
            physics=copy_physics(packet.game_ball.physics)
        )

        with self.input_lock:
            # A new draft discards any older unconfirmed draft, but preserves
            # the last committed replay until F4 is pressed.
            self.pending_recording = None
            self.recording_backup = self.capture_recording()
            self.recorded_opponent_name = self.target_name
            self.recorded_car_state = draft_car_state
            self.recorded_ball_state = draft_ball_state
            self.recorded_checkpoints = [
                (0.0, draft_car_state, draft_ball_state)
            ]
            initial_controls = copy_controls(self.latest_target_controls)
            self.recorded_inputs = [(0.0, initial_controls)]
            self.record_start_time = now
            self.recording_duration = 0.0
            self.recording = True
            self.last_seen_touch_time = (
                packet.game_ball.latest_touch.time_seconds
            )
            self.last_touch_checkpoint_index = None

        self.replaying = False
        self.start_recording_on_next_tick = False
        self.status_message = f'RECORDING {self.target_name}'

    def stop_recording(self, now: float):
        with self.input_lock:
            if self.record_start_time is not None:
                self.recording_duration = max(
                    0.0, now - self.record_start_time
                )
            self.recording = False
            was_trimmed = self.trim_recording_after_last_touch()
            self.pending_recording = self.capture_recording()
            draft_duration = self.recording_duration
            self.restore_recording(self.recording_backup)
            self.recording_backup = None

        trim_label = ' (trimmed after last touch)' if was_trimmed else ''
        self.status_message = (
            f'Draft {draft_duration:.2f}s ready{trim_label}: '
            f'press F4 to confirm'
        )

    def confirm_pending_recording(self):
        with self.input_lock:
            if self.recording:
                self.status_message = 'Stop recording with F5 before confirming'
                return
            if self.pending_recording is None:
                self.status_message = 'No recording draft to confirm'
                return

            self.restore_recording(self.pending_recording)
            self.pending_recording = None

        self.status_message = (
            f'Saved {self.recording_duration:.2f}s recording '
            f'from {self.recorded_opponent_name}'
        )

    def capture_recording(self):
        return {
            'opponent_name': self.recorded_opponent_name,
            'duration': self.recording_duration,
            'inputs': list(self.recorded_inputs),
            'car_state': self.recorded_car_state,
            'ball_state': self.recorded_ball_state,
            'checkpoints': list(self.recorded_checkpoints),
        }

    def restore_recording(self, recording):
        if recording is None:
            self.recorded_opponent_name = 'Unknown opponent'
            self.recording_duration = 0.0
            self.recorded_inputs = []
            self.recorded_car_state = None
            self.recorded_ball_state = None
            self.recorded_checkpoints = []
            return

        self.recorded_opponent_name = recording['opponent_name']
        self.recording_duration = recording['duration']
        self.recorded_inputs = recording['inputs']
        self.recorded_car_state = recording['car_state']
        self.recorded_ball_state = recording['ball_state']
        self.recorded_checkpoints = recording['checkpoints']

    def record_physics_checkpoints(self, packet: GameTickPacket):
        if self.target_index is None or self.target_index >= packet.num_cars:
            return

        # start_recording already captured this exact tick.
        if (
            self.recorded_checkpoints
            and self.recording_duration <= self.recorded_checkpoints[-1][0]
        ):
            return

        opponent = packet.game_cars[self.target_index]
        car_state = CarState(
            physics=copy_physics(opponent.physics),
            boost_amount=opponent.boost,
            jumped=opponent.jumped,
            double_jumped=opponent.double_jumped,
        )
        ball_state = BallState(
            physics=copy_physics(packet.game_ball.physics)
        )
        self.recorded_checkpoints.append(
            (self.recording_duration, car_state, ball_state)
        )

    def track_recorded_ball_touch(self, packet: GameTickPacket):
        touch = packet.game_ball.latest_touch
        if touch.time_seconds <= self.last_seen_touch_time:
            return

        self.last_seen_touch_time = touch.time_seconds
        if touch.player_index == self.target_index:
            self.last_touch_checkpoint_index = (
                len(self.recorded_checkpoints) - 1
            )

    def trim_recording_after_last_touch(self) -> bool:
        if self.last_touch_checkpoint_index is None:
            return False

        final_checkpoint_index = min(
            self.last_touch_checkpoint_index + 2,
            len(self.recorded_checkpoints) - 1,
        )
        if final_checkpoint_index >= len(self.recorded_checkpoints) - 1:
            return False

        self.recorded_checkpoints = self.recorded_checkpoints[
            :final_checkpoint_index + 1
        ]
        cutoff_time = self.recorded_checkpoints[-1][0]
        self.recorded_inputs = [
            (event_time, controls)
            for event_time, controls in self.recorded_inputs
            if event_time <= cutoff_time + 1e-6
        ]
        self.recording_duration = cutoff_time
        return True

    def export_recording(self):
        if not self.recorded_inputs or not self.recorded_checkpoints:
            self.status_message = 'Nothing recorded to export'
            return

        recording_data = {
            'format': RECORDING_FORMAT,
            'version': RECORDING_VERSION,
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'opponent': self.recorded_opponent_name,
            'duration': self.recording_duration,
            'inputs': [
                [event_time, controls_to_data(controls)]
                for event_time, controls in self.recorded_inputs
            ],
            'checkpoints': [
                [
                    checkpoint_time,
                    car_state_to_data(car_state),
                    physics_to_data(ball_state.physics),
                ]
                for checkpoint_time, car_state, ball_state
                in self.recorded_checkpoints
            ],
        }

        safe_name = ''.join(
            character if character.isalnum() or character in '-_' else '_'
            for character in self.recorded_opponent_name
        ).strip('_') or 'opponent'
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        output_path = self.recordings_directory / (
            f'{timestamp}-{safe_name}.json.gz'
        )

        try:
            self.recordings_directory.mkdir(parents=True, exist_ok=True)
            with gzip.open(output_path, 'wt', encoding='utf-8') as recording_file:
                json.dump(
                    recording_data,
                    recording_file,
                    separators=(',', ':'),
                )
        except OSError as error:
            self.status_message = f'Export failed: {error}'
            return

        self.status_message = f'Exported {output_path.name}'

    def request_import_dialog(self):
        with self.input_lock:
            if self.import_dialog_open:
                self.status_message = 'Import dialog is already open'
                return
            self.import_dialog_open = True
            self.pending_import_result = None

        self.status_message = 'Choose a recording to import...'
        dialog_thread = threading.Thread(
            target=self.run_import_dialog,
            name=f'{self.name}-recording-file-dialog',
            daemon=True,
        )
        dialog_thread.start()

    def run_import_dialog(self):
        root = None
        result = None
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            root.update()
            initial_directory = (
                self.recordings_directory
                if self.recordings_directory.exists()
                else self.recordings_directory.parent
            )
            selected_file = filedialog.askopenfilename(
                parent=root,
                title='Import RocketBot recording',
                initialdir=str(initial_directory),
                filetypes=[
                    ('RocketBot recordings', '*.json.gz'),
                    ('All files', '*.*'),
                ],
            )
            if selected_file:
                result = ('selected', Path(selected_file))
            else:
                result = ('cancelled', None)
        except Exception as error:
            result = ('error', str(error))
        finally:
            if root is not None:
                root.destroy()
            with self.input_lock:
                self.pending_import_result = result
                self.import_dialog_open = False

    def consume_import_dialog_result(self):
        with self.input_lock:
            result = self.pending_import_result
            self.pending_import_result = None

        if result is None:
            return

        result_type, value = result
        if result_type == 'selected':
            self.import_recording(value)
        elif result_type == 'cancelled':
            self.status_message = 'Import cancelled'
        else:
            self.status_message = f'File dialog failed: {value}'

    def import_recording(self, input_path: Path):
        try:
            with gzip.open(input_path, 'rt', encoding='utf-8') as recording_file:
                recording_data = json.load(recording_file)

            if recording_data.get('format') != RECORDING_FORMAT:
                raise ValueError('Unrecognized recording format')
            if recording_data.get('version') != RECORDING_VERSION:
                raise ValueError(
                    f'Unsupported recording version: '
                    f'{recording_data.get("version")}'
                )

            imported_inputs = [
                (float(event[0]), controls_from_data(event[1]))
                for event in recording_data['inputs']
            ]
            imported_checkpoints = [
                (
                    float(checkpoint[0]),
                    car_state_from_data(checkpoint[1]),
                    BallState(physics=physics_from_data(checkpoint[2])),
                )
                for checkpoint in recording_data['checkpoints']
            ]
            if not imported_inputs or not imported_checkpoints:
                raise ValueError('Recording contains no replay data')

            duration = float(recording_data['duration'])
            if duration < 0:
                raise ValueError('Recording duration cannot be negative')
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.status_message = f'Import failed: {error}'
            return

        with self.input_lock:
            self.recording = False
            self.replaying = False
            self.record_after_replay = False
            self.start_recording_on_next_tick = False
            self.pending_recording = None
            self.recording_backup = None
            self.record_start_time = None
            self.recording_duration = duration
            self.recorded_opponent_name = str(
                recording_data.get('opponent', 'Unknown opponent')
            )
            self.recorded_inputs = imported_inputs
            self.recorded_checkpoints = imported_checkpoints
            self.recorded_car_state = imported_checkpoints[0][1]
            self.recorded_ball_state = imported_checkpoints[0][2]

        self.status_message = f'Imported {input_path.name}'

    def start_replay(self, now: float):
        if self.recording:
            self.stop_recording(now)

        with self.input_lock:
            if (
                not self.recorded_inputs
                or self.recorded_car_state is None
                or self.recorded_ball_state is None
            ):
                self.status_message = 'Nothing recorded yet'
                return

            self.replay_inputs = [
                (event_time, copy_controls(controls))
                for event_time, controls in self.recorded_inputs
            ]
            self.replay_checkpoints = list(self.recorded_checkpoints)

        self.replay_start_time = now
        self.replay_duration = self.recording_duration
        self.replay_input_index = 0
        self.replay_controls = copy_controls(self.replay_inputs[0][1])
        self.replay_checkpoint_index = 0
        self.replaying = True
        self.apply_physics_checkpoint(self.replay_checkpoints[0])
        self.status_message = 'REPLAYING'

    def get_replay_controls(self, now: float) -> SimpleControllerState:
        elapsed = max(0.0, now - self.replay_start_time)

        while (
            self.replay_input_index + 1 < len(self.replay_inputs)
            and self.replay_inputs[self.replay_input_index + 1][0] <= elapsed
        ):
            self.replay_input_index += 1
            self.replay_controls = copy_controls(
                self.replay_inputs[self.replay_input_index][1]
            )

        while (
            self.replay_checkpoint_index + 1 < len(self.replay_checkpoints)
            and self.replay_checkpoints[
                self.replay_checkpoint_index + 1
            ][0] <= elapsed
        ):
            self.replay_checkpoint_index += 1

        # Interpolate between recorded ticks before reasserting physics. This
        # avoids holding one snapshot and visibly snapping to the next.
        checkpoint = self.interpolated_checkpoint(elapsed)
        self.apply_physics_checkpoint(checkpoint)

        if elapsed >= self.replay_duration:
            self.replaying = False
            if self.record_after_replay:
                self.record_after_replay = False
                self.start_recording_on_next_tick = True
                self.status_message = 'Replay finished; recording next tick'
            else:
                self.status_message = (
                    f'Replay finished ({self.replay_duration:.2f}s)'
                )
            return SimpleControllerState()

        action_label = ' -> RECORD' if self.record_after_replay else ''
        self.status_message = (
            f'REPLAYING {elapsed:.2f}/{self.replay_duration:.2f}s'
            f'{action_label}'
        )
        return copy_controls(self.replay_controls)

    def interpolated_checkpoint(self, elapsed: float):
        current = self.replay_checkpoints[self.replay_checkpoint_index]
        next_index = self.replay_checkpoint_index + 1
        if next_index >= len(self.replay_checkpoints):
            return current

        following = self.replay_checkpoints[next_index]
        current_time, current_car, current_ball = current
        following_time, following_car, following_ball = following
        time_span = following_time - current_time
        if time_span <= 0:
            return following

        alpha = max(0.0, min(1.0, (elapsed - current_time) / time_span))
        return (
            elapsed,
            self.interpolate_car_state(current_car, following_car, alpha),
            BallState(
                physics=self.interpolate_physics(
                    current_ball.physics,
                    following_ball.physics,
                    alpha,
                )
            ),
        )

    def interpolate_car_state(
        self,
        current: CarState,
        following: CarState,
        alpha: float,
    ) -> CarState:
        return CarState(
            physics=self.interpolate_physics(
                current.physics,
                following.physics,
                alpha,
            ),
            boost_amount=self.lerp(
                current.boost_amount,
                following.boost_amount,
                alpha,
            ),
            # Jump flags are discrete, so do not apply them before their tick.
            jumped=current.jumped,
            double_jumped=current.double_jumped,
        )

    def interpolate_physics(
        self,
        current: Physics,
        following: Physics,
        alpha: float,
    ) -> Physics:
        return Physics(
            location=self.interpolate_vector(
                current.location, following.location, alpha
            ),
            rotation=Rotator(
                pitch=self.lerp_angle(
                    current.rotation.pitch,
                    following.rotation.pitch,
                    alpha,
                ),
                yaw=self.lerp_angle(
                    current.rotation.yaw,
                    following.rotation.yaw,
                    alpha,
                ),
                roll=self.lerp_angle(
                    current.rotation.roll,
                    following.rotation.roll,
                    alpha,
                ),
            ),
            velocity=self.interpolate_vector(
                current.velocity, following.velocity, alpha
            ),
            angular_velocity=self.interpolate_vector(
                current.angular_velocity,
                following.angular_velocity,
                alpha,
            ),
        )

    @staticmethod
    def interpolate_vector(current, following, alpha: float) -> Vector3:
        return Vector3(
            x=MyBot.lerp(current.x, following.x, alpha),
            y=MyBot.lerp(current.y, following.y, alpha),
            z=MyBot.lerp(current.z, following.z, alpha),
        )

    @staticmethod
    def lerp(current: float, following: float, alpha: float) -> float:
        return current + (following - current) * alpha

    @staticmethod
    def lerp_angle(current: float, following: float, alpha: float) -> float:
        difference = (following - current + math.pi) % (2 * math.pi) - math.pi
        return current + difference * alpha

    def apply_physics_checkpoint(self, checkpoint):
        _, car_state, ball_state = checkpoint
        self.set_game_state(
            GameState(
                cars={self.index: car_state},
                ball=ball_state,
            )
        )

    def key_pressed(self, virtual_key: int) -> bool:
        try:
            is_down = bool(
                ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 0x8000
            )
        except AttributeError:
            is_down = False

        was_down = self.key_was_down[virtual_key]
        self.key_was_down[virtual_key] = is_down
        return is_down and not was_down

    def draw_status(self):
        if not self.render_text:
            return

        white = self.renderer.white()
        status_color = self.renderer.red() if self.recording else white
        self.renderer.draw_string_2d(
            30, 30, 2, 2, self.status_message, status_color
        )
        self.renderer.draw_string_2d(
            30, 62, 1, 1, f'Opponent: {self.target_name}', white
        )
        self.renderer.draw_string_2d(
            30,
            82,
            1,
            1,
            'F3 replay then record | F4 confirm draft',
            white,
        )
        self.renderer.draw_string_2d(
            30,
            102,
            1,
            1,
            'F5 record | F6 replay | F7 export',
            white,
        )
        self.renderer.draw_string_2d(
            30,
            122,
            1,
            1,
            'F8 text | F9 import',
            white,
        )

    def retire(self):
        self.socket_relay.disconnect()
