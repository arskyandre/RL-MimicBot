import gzip
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageDraw, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


RECORDING_FORMAT = 'rocketbot-input-recording'
RECORDING_VERSION = 1
ANTIALIAS_SCALE = 4

BACKGROUND = '#F7F2FA'
SURFACE = '#FFFBFE'
SURFACE_VARIANT = '#E7E0EC'
PRIMARY = '#6750A4'
PRIMARY_HOVER = '#7965AF'
PRIMARY_CONTAINER = '#EADDFF'
ON_PRIMARY = '#FFFFFF'
ON_PRIMARY_CONTAINER = '#21005D'
ON_SURFACE = '#1D1B20'
ON_SURFACE_VARIANT = '#49454F'
OUTLINE = '#79747E'
ERROR = '#B3261E'


def lerp(first, second, amount):
    return first + (second - first) * amount


def lerp_vector(first, second, amount):
    return [
        lerp(float(first[index]), float(second[index]), amount)
        for index in range(3)
    ]


def lerp_angle(first, second, amount):
    difference = (second - first + math.pi) % (2 * math.pi) - math.pi
    return first + difference * amount


def interpolate_physics(first, second, amount):
    return {
        'location': lerp_vector(
            first['location'], second['location'], amount
        ),
        'rotation': [
            lerp_angle(
                float(first['rotation'][index]),
                float(second['rotation'][index]),
                amount,
            )
            for index in range(3)
        ],
        'velocity': lerp_vector(
            first['velocity'], second['velocity'], amount
        ),
        'angular_velocity': lerp_vector(
            first['angular_velocity'],
            second['angular_velocity'],
            amount,
        ),
    }


def interpolate_car(first, second, amount):
    return {
        'physics': interpolate_physics(
            first['physics'], second['physics'], amount
        ),
        'boost': lerp(
            float(first['boost']), float(second['boost']), amount
        ),
        'jumped': bool(first['jumped']),
        'double_jumped': bool(first['double_jumped']),
    }


def validate_recording(recording):
    if recording.get('format') != RECORDING_FORMAT:
        raise ValueError('This is not a RocketBot input recording.')
    if recording.get('version') != RECORDING_VERSION:
        raise ValueError(
            f'Unsupported recording version: {recording.get("version")}'
        )
    if not recording.get('inputs'):
        raise ValueError('The recording contains no controller inputs.')
    if not recording.get('checkpoints'):
        raise ValueError('The recording contains no physics checkpoints.')
    if float(recording.get('duration', -1)) < 0:
        raise ValueError('The recording has an invalid duration.')


def state_at_time(checkpoints, requested_time):
    previous = checkpoints[0]
    following = checkpoints[-1]

    for checkpoint in checkpoints:
        checkpoint_time = float(checkpoint[0])
        if checkpoint_time <= requested_time:
            previous = checkpoint
        if checkpoint_time >= requested_time:
            following = checkpoint
            break

    previous_time = float(previous[0])
    following_time = float(following[0])
    time_span = following_time - previous_time
    if time_span <= 0:
        amount = 0.0
    else:
        amount = max(
            0.0,
            min(1.0, (requested_time - previous_time) / time_span),
        )

    return [
        requested_time,
        interpolate_car(previous[1], following[1], amount),
        interpolate_physics(previous[2], following[2], amount),
    ]


def trim_recording(recording, start_time, end_time, source_name):
    validate_recording(recording)
    duration = float(recording['duration'])
    if start_time < 0:
        raise ValueError('Start time cannot be negative.')
    if end_time > duration:
        raise ValueError('End time exceeds the recording duration.')
    if end_time <= start_time:
        raise ValueError('End time must be greater than start time.')

    inputs = sorted(recording['inputs'], key=lambda event: float(event[0]))
    checkpoints = sorted(
        recording['checkpoints'],
        key=lambda checkpoint: float(checkpoint[0]),
    )
    trimmed_duration = end_time - start_time

    active_controls = inputs[0][1]
    for event_time, controls in inputs:
        if float(event_time) <= start_time:
            active_controls = controls
        else:
            break

    trimmed_inputs = [[0.0, active_controls]]
    trimmed_inputs.extend(
        [float(event_time) - start_time, controls]
        for event_time, controls in inputs
        if start_time < float(event_time) <= end_time
    )

    trimmed_checkpoints = [state_at_time(checkpoints, start_time)]
    trimmed_checkpoints[0][0] = 0.0
    trimmed_checkpoints.extend(
        [float(checkpoint[0]) - start_time, checkpoint[1], checkpoint[2]]
        for checkpoint in checkpoints
        if start_time < float(checkpoint[0]) < end_time
    )
    final_checkpoint = state_at_time(checkpoints, end_time)
    final_checkpoint[0] = trimmed_duration
    trimmed_checkpoints.append(final_checkpoint)

    trimmed = dict(recording)
    trimmed.update(
        {
            'duration': trimmed_duration,
            'inputs': trimmed_inputs,
            'checkpoints': trimmed_checkpoints,
            'trimmed_utc': datetime.now(timezone.utc).isoformat(),
            'trimmed_from': {
                'source': source_name,
                'start': start_time,
                'end': end_time,
            },
        }
    )
    return trimmed


def draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius, fill, tag):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    if PIL_AVAILABLE and getattr(canvas, '_use_antialias', True):
        width = max(1, int(math.ceil(x2 - x1)))
        height = max(1, int(math.ceil(y2 - y1)))
        image = Image.new(
            'RGBA',
            (width * ANTIALIAS_SCALE, height * ANTIALIAS_SCALE),
            (0, 0, 0, 0),
        )
        drawer = ImageDraw.Draw(image)
        drawer.rounded_rectangle(
            (
                0,
                0,
                width * ANTIALIAS_SCALE - 1,
                height * ANTIALIAS_SCALE - 1,
            ),
            radius=max(0, int(radius * ANTIALIAS_SCALE)),
            fill=fill,
        )
        image = image.resize(
            (width, height),
            Image.Resampling.LANCZOS,
        )
        photo = ImageTk.PhotoImage(image)
        canvas.create_image(
            x1, y1, anchor='nw', image=photo, tags=tag,
        )
        canvas._antialiased_images.append(photo)
        return

    # During an active window resize, prioritize responsiveness. The polished
    # rounded image is restored by finish_live_resize as soon as resizing stops.
    if PIL_AVAILABLE and not getattr(canvas, '_use_antialias', True):
        canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=fill, outline='', tags=tag,
        )
        return

    canvas.create_rectangle(
        x1 + radius, y1, x2 - radius, y2,
        fill=fill, outline='', tags=tag,
    )
    canvas.create_rectangle(
        x1, y1 + radius, x2, y2 - radius,
        fill=fill, outline='', tags=tag,
    )
    canvas.create_oval(
        x1, y1, x1 + radius * 2, y1 + radius * 2,
        fill=fill, outline='', tags=tag,
    )
    canvas.create_oval(
        x2 - radius * 2, y1, x2, y1 + radius * 2,
        fill=fill, outline='', tags=tag,
    )
    canvas.create_oval(
        x1, y2 - radius * 2, x1 + radius * 2, y2,
        fill=fill, outline='', tags=tag,
    )
    canvas.create_oval(
        x2 - radius * 2, y2 - radius * 2, x2, y2,
        fill=fill, outline='', tags=tag,
    )


def draw_circle(canvas, center_x, center_y, radius, fill, tag):
    if PIL_AVAILABLE and getattr(canvas, '_use_antialias', True):
        size = max(1, int(math.ceil(radius * 2)))
        image = Image.new(
            'RGBA',
            (size * ANTIALIAS_SCALE, size * ANTIALIAS_SCALE),
            (0, 0, 0, 0),
        )
        drawer = ImageDraw.Draw(image)
        drawer.ellipse(
            (
                0,
                0,
                size * ANTIALIAS_SCALE - 1,
                size * ANTIALIAS_SCALE - 1,
            ),
            fill=fill,
        )
        image = image.resize(
            (size, size),
            Image.Resampling.LANCZOS,
        )
        photo = ImageTk.PhotoImage(image)
        canvas.create_image(
            center_x - size / 2,
            center_y - size / 2,
            anchor='nw',
            image=photo,
            tags=tag,
        )
        canvas._antialiased_images.append(photo)
        return

    canvas.create_oval(
        center_x - radius,
        center_y - radius,
        center_x + radius,
        center_y + radius,
        fill=fill,
        outline='',
        tags=tag,
    )


def begin_live_resize(widget, redraw):
    """Use cheap canvas shapes while resizing, then restore AA once settled."""
    widget._use_antialias = False
    if widget._resize_redraw_job is not None:
        widget.after_cancel(widget._resize_redraw_job)
    if widget._live_redraw_job is None:
        widget._live_redraw_job = widget.after(
            50,
            lambda: flush_live_resize(widget, redraw),
        )
    widget._resize_redraw_job = widget.after(
        120,
        lambda: finish_live_resize(widget, redraw),
    )


def flush_live_resize(widget, redraw):
    widget._live_redraw_job = None
    redraw()


def finish_live_resize(widget, redraw):
    widget._resize_redraw_job = None
    if widget._live_redraw_job is not None:
        widget.after_cancel(widget._live_redraw_job)
        widget._live_redraw_job = None
    widget._use_antialias = True
    redraw()


class RoundedCard(tk.Canvas):

    def __init__(self, parent, height, padding=20):
        super().__init__(
            parent,
            height=height,
            background=BACKGROUND,
            borderwidth=0,
            highlightthickness=0,
        )
        self.padding = padding
        self._antialiased_images = []
        self._use_antialias = True
        self._resize_redraw_job = None
        self._live_redraw_job = None
        self.inner = tk.Frame(self, background=SURFACE)
        self.inner_window = self.create_window(
            padding,
            padding,
            anchor='nw',
            window=self.inner,
        )
        self.bind('<Configure>', self.on_configure)

    def on_configure(self, _event=None):
        begin_live_resize(self, self.redraw)

    def redraw(self, _event=None):
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        self.delete('card_shape')
        self._antialiased_images = []
        draw_rounded_rectangle(
            self, 2, 5, width - 2, height - 1,
            22, '#E4DEE7', 'card_shape',
        )
        draw_rounded_rectangle(
            self, 2, 1, width - 2, height - 5,
            22, SURFACE, 'card_shape',
        )
        self.tag_lower('card_shape')
        self.itemconfigure(
            self.inner_window,
            width=max(1, width - self.padding * 2),
        )


class MaterialButton(tk.Canvas):

    def __init__(
        self,
        parent,
        text,
        command,
        width=170,
        height=46,
        tonal=False,
        enabled=True,
    ):
        try:
            background = parent.cget('background')
        except tk.TclError:
            background = BACKGROUND
        super().__init__(
            parent,
            width=width,
            height=height,
            background=background,
            borderwidth=0,
            highlightthickness=0,
            cursor='hand2',
            takefocus=True,
        )
        self.button_text = text
        self.command = command
        self.tonal = tonal
        self.enabled = enabled
        self.hovered = False
        self._antialiased_images = []
        self._use_antialias = True
        self._resize_redraw_job = None
        self._live_redraw_job = None
        self.bind('<Configure>', self.on_configure)
        self.bind('<Enter>', self.on_enter)
        self.bind('<Leave>', self.on_leave)
        self.bind('<Button-1>', self.on_click)
        self.bind('<Return>', self.on_click)
        self.bind('<space>', self.on_click)

    def on_configure(self, _event=None):
        begin_live_resize(self, self.redraw)

    def colors(self):
        if not self.enabled:
            return SURFACE_VARIANT, OUTLINE
        if self.tonal:
            return (
                '#DED0F7' if self.hovered else PRIMARY_CONTAINER,
                ON_PRIMARY_CONTAINER,
            )
        return (
            PRIMARY_HOVER if self.hovered else PRIMARY,
            ON_PRIMARY,
        )

    def redraw(self, _event=None):
        self.delete('all')
        self._antialiased_images = []
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        fill, foreground = self.colors()
        draw_rounded_rectangle(
            self, 1, 1, width - 1, height - 1,
            height / 2, fill, 'button',
        )
        self.create_text(
            width / 2,
            height / 2,
            text=self.button_text,
            fill=foreground,
            font=('Segoe UI Variable Text', 10, 'bold'),
        )

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.configure(cursor='hand2' if self.enabled else 'arrow')
        self.redraw()

    def on_enter(self, _event):
        self.hovered = True
        self.redraw()

    def on_leave(self, _event):
        self.hovered = False
        self.redraw()

    def on_click(self, _event=None):
        if self.enabled and self.command is not None:
            self.command()


class MaterialSlider(tk.Canvas):

    def __init__(self, parent, variable, command=None):
        super().__init__(
            parent,
            height=42,
            background=SURFACE,
            borderwidth=0,
            highlightthickness=0,
            cursor='arrow',
            takefocus=True,
        )
        self.variable = variable
        self.command = command
        self.minimum = 0.0
        self.maximum = 1.0
        self.enabled = False
        self._antialiased_images = []
        self.redraw_job = None
        self._use_antialias = True
        self._resize_redraw_job = None
        self._live_redraw_job = None
        self.variable.trace_add('write', self.on_variable_changed)
        self.bind('<Configure>', self.on_configure)
        self.bind('<Button-1>', self.on_pointer)
        self.bind('<B1-Motion>', self.on_pointer)
        self.bind('<Left>', lambda event: self.nudge(-1))
        self.bind('<Right>', lambda event: self.nudge(1))

    def configure_range(self, minimum, maximum):
        self.minimum = float(minimum)
        self.maximum = max(float(maximum), self.minimum + 0.001)
        self.redraw()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.configure(cursor='hand2' if self.enabled else 'arrow')
        self.redraw()

    def normalized_value(self):
        value = float(self.variable.get())
        return max(
            0.0,
            min(1.0, (value - self.minimum) / (self.maximum - self.minimum)),
        )

    def on_configure(self, _event=None):
        begin_live_resize(self, self.redraw)

    def redraw(self, _event=None):
        self.redraw_job = None
        self.delete('all')
        self._antialiased_images = []
        width = max(1, self.winfo_width())
        center_y = 21
        left = 12
        right = max(left + 1, width - 12)
        thumb_x = left + (right - left) * self.normalized_value()

        self.create_line(
            left,
            center_y,
            right,
            center_y,
            fill=SURFACE_VARIANT,
            width=8,
            capstyle=tk.ROUND,
            tags='track',
        )
        active_color = PRIMARY if self.enabled else OUTLINE
        if thumb_x > left:
            self.create_line(
                left,
                center_y,
                thumb_x,
                center_y,
                fill=active_color,
                width=8,
                capstyle=tk.ROUND,
                tags='active_track',
            )
        draw_circle(
            self,
            thumb_x,
            center_y,
            12,
            PRIMARY_CONTAINER if self.enabled else SURFACE_VARIANT,
            'thumb_halo',
        )
        draw_circle(
            self,
            thumb_x,
            center_y,
            8,
            PRIMARY if self.enabled else OUTLINE,
            'thumb',
        )

    def on_pointer(self, event):
        if not self.enabled:
            return
        self.focus_set()
        width = max(1, self.winfo_width())
        normalized = max(
            0.0,
            min(1.0, (event.x - 12) / max(1, width - 24)),
        )
        value = self.minimum + normalized * (self.maximum - self.minimum)
        self.variable.set(round(value, 3))

    def nudge(self, direction):
        if not self.enabled:
            return
        step = max(0.001, (self.maximum - self.minimum) / 1000)
        value = max(
            self.minimum,
            min(self.maximum, float(self.variable.get()) + direction * step),
        )
        self.variable.set(round(value, 3))

    def on_variable_changed(self, *_args):
        if self.redraw_job is None:
            self.redraw_job = self.after(16, self.flush_variable_change)

    def flush_variable_change(self):
        self.redraw()
        if self.command is not None:
            self.command()


class RoundedStatus(tk.Canvas):

    def __init__(self, parent, textvariable):
        super().__init__(
            parent,
            height=48,
            background=BACKGROUND,
            borderwidth=0,
            highlightthickness=0,
        )
        self.textvariable = textvariable
        self._antialiased_images = []
        self._use_antialias = True
        self._resize_redraw_job = None
        self._live_redraw_job = None
        self.textvariable.trace_add('write', self.redraw)
        self.bind('<Configure>', self.on_configure)

    def on_configure(self, _event=None):
        begin_live_resize(self, self.redraw)

    def redraw(self, *_args):
        self.delete('all')
        self._antialiased_images = []
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        draw_rounded_rectangle(
            self, 1, 1, width - 1, height - 1,
            18, PRIMARY_CONTAINER, 'status',
        )
        draw_circle(
            self,
            21,
            height / 2,
            4,
            PRIMARY,
            'status_dot',
        )
        self.create_text(
            36,
            height / 2,
            anchor='w',
            text=self.textvariable.get(),
            fill=ON_PRIMARY_CONTAINER,
            font=('Segoe UI', 9),
        )


class ReplayTrimmer:

    def __init__(self, root):
        self.root = root
        self.root.title('RocketBot Replay Trimmer')
        self.root.geometry('760x760')
        self.root.minsize(680, 720)
        self.root.configure(background=BACKGROUND)

        self.recording = None
        self.input_path = None
        self.duration = 0.0
        self.file_label = tk.StringVar(value='No replay loaded')
        self.details_label = tk.StringVar(
            value='Open an exported .json.gz replay to begin.'
        )
        self.status_label = tk.StringVar(value='Ready')
        self.start_time = tk.DoubleVar(value=0.0)
        self.end_time = tk.DoubleVar(value=0.0)
        self.start_value_label = tk.StringVar(value='0.000 s')
        self.end_value_label = tk.StringVar(value='0.000 s')
        self.selection_label = tk.StringVar(value='No range selected')

        self.configure_styles()
        self.build_ui()
        self._window_resize_active = False
        self._window_resize_job = None
        self._last_window_resize = 0.0
        self._window_resize_ready = False
        self.root.bind('<Configure>', self.on_window_configure, add='+')
        self.root.after(250, self.enable_window_resize_freeze)

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('App.TFrame', background=BACKGROUND)
        style.configure(
            'Card.TFrame',
            background=SURFACE,
            borderwidth=1,
            relief='solid',
        )
        style.configure(
            'Title.TLabel',
            background=BACKGROUND,
            foreground=ON_SURFACE,
            font=('Segoe UI Variable Display', 24, 'bold'),
        )
        style.configure(
            'Subtitle.TLabel',
            background=BACKGROUND,
            foreground=ON_SURFACE_VARIANT,
            font=('Segoe UI', 10),
        )
        style.configure(
            'CardTitle.TLabel',
            background=SURFACE,
            foreground=ON_SURFACE,
            font=('Segoe UI', 12, 'bold'),
        )
        style.configure(
            'Overline.TLabel',
            background=SURFACE,
            foreground=PRIMARY,
            font=('Segoe UI', 8, 'bold'),
        )
        style.configure(
            'Body.TLabel',
            background=SURFACE,
            foreground=ON_SURFACE,
        )
        style.configure(
            'Muted.TLabel',
            background=SURFACE,
            foreground=ON_SURFACE_VARIANT,
        )
        style.configure(
            'Value.TLabel',
            background=SURFACE,
            foreground=PRIMARY,
            font=('Segoe UI Variable Text', 11, 'bold'),
        )
        style.configure(
            'Status.TLabel',
            background=PRIMARY_CONTAINER,
            foreground=ON_PRIMARY_CONTAINER,
            padding=(14, 10),
            font=('Segoe UI', 9),
        )
        style.configure(
            'Primary.TButton',
            background=PRIMARY,
            foreground=ON_PRIMARY,
            borderwidth=0,
            padding=(22, 11),
            font=('Segoe UI Variable Text', 10, 'bold'),
        )
        style.map(
            'Primary.TButton',
            background=[
                ('active', PRIMARY_HOVER),
                ('pressed', ON_PRIMARY_CONTAINER),
                ('disabled', SURFACE_VARIANT),
            ],
            foreground=[
                ('disabled', OUTLINE),
            ],
        )
        style.configure(
            'Tonal.TButton',
            background=PRIMARY_CONTAINER,
            foreground=ON_PRIMARY_CONTAINER,
            borderwidth=0,
            padding=(18, 10),
            font=('Segoe UI Variable Text', 10, 'bold'),
        )
        style.map(
            'Tonal.TButton',
            background=[
                ('active', '#DED0F7'),
                ('pressed', '#D3C2EF'),
            ],
        )

    def build_ui(self):
        shell = ttk.Frame(self.root, padding=(30, 24), style='App.TFrame')
        shell.pack(fill='both', expand=True)
        self.shell = shell
        shell.columnconfigure(0, weight=1)

        ttk.Label(
            shell,
            text='Replay trimmer',
            style='Title.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Label(
            shell,
            text='Shape a clean RocketBot replay with frame-accurate boundaries.',
            style='Subtitle.TLabel',
        ).grid(row=1, column=0, sticky='w', pady=(2, 20))

        source_card = RoundedCard(shell, height=126, padding=22)
        source_card.grid(row=2, column=0, sticky='ew')
        source_content = source_card.inner
        source_content.columnconfigure(0, weight=1)

        ttk.Label(
            source_content,
            text='SOURCE REPLAY',
            style='Overline.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Label(
            source_content,
            textvariable=self.file_label,
            style='CardTitle.TLabel',
            wraplength=470,
        ).grid(row=1, column=0, sticky='w', pady=(4, 2))
        ttk.Label(
            source_content,
            textvariable=self.details_label,
            style='Muted.TLabel',
        ).grid(row=2, column=0, sticky='w')
        self.open_button = MaterialButton(
            source_content,
            text='Choose replay',
            command=self.open_replay,
            tonal=True,
            width=146,
            height=44,
        )
        self.open_button.grid(
            row=0, column=1, rowspan=3, sticky='e', padx=(18, 0)
        )

        trim_card = RoundedCard(shell, height=286, padding=22)
        trim_card.grid(row=3, column=0, sticky='nsew', pady=(14, 0))
        trim_content = trim_card.inner
        trim_content.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        ttk.Label(
            trim_content,
            text='TRIM RANGE',
            style='Overline.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Label(
            trim_content,
            text='Keep only the moment that matters',
            style='CardTitle.TLabel',
        ).grid(row=1, column=0, sticky='w', pady=(4, 16))

        start_header = tk.Frame(trim_content, background=SURFACE)
        start_header.grid(row=2, column=0, sticky='ew')
        start_header.columnconfigure(0, weight=1)
        ttk.Label(
            start_header,
            text='Start',
            style='Body.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Label(
            start_header,
            textvariable=self.start_value_label,
            style='Value.TLabel',
        ).grid(row=0, column=1, sticky='e')
        self.start_scale = MaterialSlider(
            trim_content,
            variable=self.start_time,
            command=self.on_range_changed,
        )
        self.start_scale.grid(row=3, column=0, sticky='ew', pady=(2, 12))

        end_header = tk.Frame(trim_content, background=SURFACE)
        end_header.grid(row=4, column=0, sticky='ew')
        end_header.columnconfigure(0, weight=1)
        ttk.Label(
            end_header,
            text='End',
            style='Body.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Label(
            end_header,
            textvariable=self.end_value_label,
            style='Value.TLabel',
        ).grid(row=0, column=1, sticky='e')
        self.end_scale = MaterialSlider(
            trim_content,
            variable=self.end_time,
            command=self.on_range_changed,
        )
        self.end_scale.grid(row=5, column=0, sticky='ew', pady=(2, 10))

        ttk.Label(
            trim_content,
            textvariable=self.selection_label,
            style='Muted.TLabel',
        ).grid(row=6, column=0, sticky='w', pady=(2, 0))

        action_row = ttk.Frame(shell, style='App.TFrame')
        action_row.grid(row=4, column=0, sticky='ew', pady=(16, 0))
        action_row.columnconfigure(0, weight=1)
        self.save_button = MaterialButton(
            action_row,
            text='Save trimmed copy',
            command=self.save_trimmed_copy,
            width=190,
            height=48,
            enabled=False,
        )
        self.save_button.grid(row=0, column=1, sticky='e')

        self.status_banner = RoundedStatus(
            shell,
            self.status_label,
        )
        self.status_banner.grid(
            row=5, column=0, sticky='ew', pady=(14, 0)
        )

    def enable_window_resize_freeze(self):
        self._window_resize_ready = True

    def on_window_configure(self, event):
        if event.widget is not self.root or not self._window_resize_ready:
            return

        if not self._window_resize_active:
            frozen_width = max(1, self.shell.winfo_width())
            frozen_height = max(1, self.shell.winfo_height())
            self.shell.pack_forget()
            self.shell.place(
                x=0,
                y=0,
                width=frozen_width,
                height=frozen_height,
            )
            self._window_resize_active = True

        self._last_window_resize = time.monotonic()
        if self._window_resize_job is None:
            self._window_resize_job = self.root.after(
                140,
                self.check_window_resize_finished,
            )

    def check_window_resize_finished(self):
        remaining = 0.14 - (time.monotonic() - self._last_window_resize)
        if remaining > 0:
            self._window_resize_job = self.root.after(
                max(1, math.ceil(remaining * 1000)),
                self.check_window_resize_finished,
            )
            return
        self.finish_window_resize()

    def finish_window_resize(self):
        self._window_resize_job = None
        self.shell.place_forget()
        self.shell.pack(fill='both', expand=True)
        self._window_resize_active = False

    def on_range_changed(self, _value=None):
        start = float(self.start_time.get())
        end = float(self.end_time.get())
        self.start_value_label.set(f'{start:.3f} s')
        self.end_value_label.set(f'{end:.3f} s')

        if self.recording is None:
            self.selection_label.set('No range selected')
            return
        if end <= start:
            self.selection_label.set('End must be after start')
            self.save_button.set_enabled(False)
            return

        self.selection_label.set(
            f'Selected duration  {end - start:.3f} seconds'
        )
        self.save_button.set_enabled(True)

    def open_replay(self):
        selected = filedialog.askopenfilename(
            parent=self.root,
            title='Open RocketBot recording',
            initialdir=str(Path(__file__).resolve().parent),
            filetypes=[
                ('RocketBot recordings', '*.json.gz'),
                ('All files', '*.*'),
            ],
        )
        if not selected:
            return
        self.load_replay(Path(selected))

    def load_replay(self, path):
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as replay_file:
                recording = json.load(replay_file)
            validate_recording(recording)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            messagebox.showerror('Could not open replay', str(error))
            self.status_label.set(f'Open failed: {error}')
            return

        self.recording = recording
        self.input_path = path
        self.duration = float(recording['duration'])
        self.file_label.set(path.name)
        self.details_label.set(
            f'Duration: {self.duration:.3f}s   '
            f'Inputs: {len(recording["inputs"])}   '
            f'Physics frames: {len(recording["checkpoints"])}'
        )

        self.start_scale.configure_range(0, self.duration)
        self.end_scale.configure_range(0, self.duration)
        self.start_scale.set_enabled(True)
        self.end_scale.set_enabled(True)
        self.start_time.set(0.0)
        self.end_time.set(self.duration)
        self.on_range_changed()
        self.status_label.set('Replay loaded. Choose start and end times.')

    def save_trimmed_copy(self):
        if self.recording is None or self.input_path is None:
            return

        try:
            trimmed = trim_recording(
                self.recording,
                float(self.start_time.get()),
                float(self.end_time.get()),
                self.input_path.name,
            )
        except (ValueError, KeyError, TypeError) as error:
            messagebox.showerror('Invalid trim range', str(error))
            self.status_label.set(f'Trim failed: {error}')
            return

        name = self.input_path.name
        base_name = name[:-8] if name.lower().endswith('.json.gz') else name
        output_path = filedialog.asksaveasfilename(
            parent=self.root,
            title='Save trimmed RocketBot recording',
            initialdir=str(self.input_path.parent),
            initialfile=f'{base_name}-trimmed.json.gz',
            defaultextension='.json.gz',
            filetypes=[
                ('RocketBot recordings', '*.json.gz'),
                ('All files', '*.*'),
            ],
        )
        if not output_path:
            return

        try:
            with gzip.open(
                output_path, 'wt', encoding='utf-8'
            ) as replay_file:
                json.dump(trimmed, replay_file, separators=(',', ':'))
        except OSError as error:
            messagebox.showerror('Could not save replay', str(error))
            self.status_label.set(f'Save failed: {error}')
            return

        self.status_label.set(
            f'Saved {Path(output_path).name} '
            f'({trimmed["duration"]:.3f}s)'
        )
        messagebox.showinfo(
            'Replay saved',
            f'Trimmed replay saved to:\n{output_path}',
        )


def main():
    root = tk.Tk()
    app = ReplayTrimmer(root)
    if len(sys.argv) > 1:
        app.load_replay(Path(sys.argv[1]))
    root.mainloop()


if __name__ == '__main__':
    main()
