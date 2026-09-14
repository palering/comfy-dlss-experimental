"""Host-neutral Streamline reconstruction inputs, distinct from direct NGX CSR1.

Camera metadata is explicit. Timestamp transport is not a claim that Streamline
consumes NGX's frame-time parameter. RR requires real renderer G-buffers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
import struct

from .sr_contract import SRSettings, plan_sr

CAMERA_WIRE = struct.Struct('<80fI')
FRAME_WIRE = struct.Struct('<QII7fI')
SETTINGS_WIRE = struct.Struct('<10I')


def _floats(value, count, name):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f'{name} requires {count} explicit numbers')
    try:
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
            raise ValueError(f'{name} must be finite numerical data')
        result = struct.unpack(f'<{count}f', struct.pack(f'<{count}f', *value))
    except (struct.error, OverflowError) as error:
        raise ValueError(f'{name} exceeds binary32 range') from error
    if not all(math.isfinite(v) for v in result):
        raise ValueError(f'{name} exceeds finite binary32 range')
    return result


def _inverse(a, b):
    for left, right in ((a, b), (b, a)):
        for row in range(4):
            for column in range(4):
                value = sum(left[row * 4 + k] * right[k * 4 + column] for k in range(4))
                if abs(value - int(row == column)) > .01:
                    raise ValueError('Camera matrix pair must be mutually inverse')


@dataclass(frozen=True)
class CameraFrame:
    view_to_clip: tuple[float, ...]
    clip_to_view: tuple[float, ...]
    clip_to_previous: tuple[float, ...]
    previous_to_clip: tuple[float, ...]
    position: tuple[float, ...]
    up: tuple[float, ...]
    right: tuple[float, ...]
    forward: tuple[float, ...]
    near_plane: float
    far_plane: float
    vertical_fov: float
    aspect: float
    orthographic: bool = False

    def __post_init__(self):
        for name in ('view_to_clip', 'clip_to_view', 'clip_to_previous', 'previous_to_clip'):
            object.__setattr__(self, name, _floats(getattr(self, name), 16, name))
        _inverse(self.view_to_clip, self.clip_to_view)
        _inverse(self.clip_to_previous, self.previous_to_clip)
        for name in ('position', 'up', 'right', 'forward'):
            object.__setattr__(self, name, _floats(getattr(self, name), 3, name))
        for a, b, expected in ((self.up, self.up, 1), (self.right, self.right, 1), (self.forward, self.forward, 1),
                              (self.up, self.right, 0), (self.up, self.forward, 0), (self.right, self.forward, 0)):
            if abs(sum(x * y for x, y in zip(a, b)) - expected) > .001:
                raise ValueError('Camera axes must be orthonormal')
        for name in ('near_plane', 'far_plane', 'vertical_fov', 'aspect'):
            object.__setattr__(self, name, _floats([getattr(self, name)], 1, name)[0])
        if not (0 < self.near_plane < self.far_plane and 0 < self.vertical_fov < 3.141593 and self.aspect > 0):
            raise ValueError('Invalid camera projection parameters')
        if type(self.orthographic) is not bool:
            raise ValueError('orthographic must be boolean')

    def validate_extent(self, width, height):
        if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
            raise ValueError('Invalid camera frame extent')
        if abs(self.aspect - width / height) > .001:
            raise ValueError('Camera aspect differs from the input frame')

    def encode(self):
        return CAMERA_WIRE.pack(*self.view_to_clip, *self.clip_to_view, *self.clip_to_previous,
            *self.previous_to_clip, *self.position, *self.up, *self.right, *self.forward,
            self.near_plane, self.far_plane, self.vertical_fov, self.aspect, int(self.orthographic))

    @classmethod
    def from_payload(cls, value):
        if isinstance(value, cls):
            return value
        required = {field.name for field in fields(cls)}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError('Supply complete explicit camera metadata; no camera is inferred from video')
        return cls(**value)


@dataclass(frozen=True)
class SLFrameMetadata:
    jitter_x: float = 0
    jitter_y: float = 0
    motion_scale_x: float = 1
    motion_scale_y: float = 1
    pre_exposure: float = 1
    exposure_scale: float = 1
    exposure: float = 1  # RR explicit exposure texture; SR auto exposure requires 1.
    reset: bool = False

    def __post_init__(self):
        names = ('jitter_x', 'jitter_y', 'motion_scale_x', 'motion_scale_y', 'pre_exposure', 'exposure_scale', 'exposure')
        for name in names:
            object.__setattr__(self, name, _floats([getattr(self, name)], 1, name)[0])
        if any(abs(getattr(self, k)) > .5 for k in ('jitter_x', 'jitter_y')):
            raise ValueError('Actual jitter must be in [-0.5,0.5] input pixels')
        if any(not 0 < abs(getattr(self, k)) <= 16384 for k in ('motion_scale_x', 'motion_scale_y')):
            raise ValueError('Invalid motion scale')
        if any(not 0 < getattr(self, k) <= 65504 for k in ('pre_exposure', 'exposure_scale', 'exposure')):
            raise ValueError('Exposure values must be finite and positive')
        if type(self.reset) is not bool:
            raise ValueError('reset must be boolean')

    def validate_for(self, settings):
        if settings.jitter_policy == 'unjittered_video' and (self.jitter_x or self.jitter_y):
            raise ValueError('Unjittered input cannot contain invented subpixel jitter')
        if settings.feature == 'sr' and self.exposure != 1:
            raise ValueError('This SL SR path uses auto exposure, not a manual exposure texture')


@dataclass(frozen=True)
class ReconstructionSettings:
    input_width: int
    input_height: int
    output_width: int
    output_height: int
    feature: str = 'sr'
    mode: str = 'quality'
    preset: str = 'default'
    depth_inverted: bool = False
    color_transfer: str = 'srgb'
    jitter_policy: str = 'unjittered_video'

    def __post_init__(self):
        if self.feature not in ('sr', 'rr'):
            raise ValueError('Reconstruction feature must be sr or rr; DLAA is a resolution mode')
        if self.color_transfer not in ('srgb', 'linear_sdr'):
            raise ValueError('This output adapter supports declared SDR transfer only, not HDR')
        if self.feature == 'rr' and (self.preset != 'default' or self.color_transfer != 'linear_sdr'):
            raise ValueError('RR requires linear SDR renderer color and the default RR preset, not SR presets')
        plan_sr(self.sr_settings, self.input_width, self.input_height, self.output_width, self.output_height)

    @property
    def sr_settings(self):
        return SRSettings(mode=self.mode, preset=self.preset, depth_inverted=self.depth_inverted,
                          auto_exposure=self.feature == 'sr', jitter_policy=self.jitter_policy)

    def encode(self):
        from .sr_worker import OwnedSRSettings
        base = OwnedSRSettings(self.input_width, self.input_height, self.output_width, self.output_height,
                               self.sr_settings).encode()
        return base + struct.pack('<II', 1 if self.feature == 'sr' else 2,
                                  int(self.jitter_policy == 'external_render_metadata'))

    @property
    def frame_bytes(self):
        return FRAME_WIRE.size + CAMERA_WIRE.size + self.input_width * self.input_height * (16 if self.feature == 'sr' else 44) + (128 if self.feature == 'rr' else 0)

    def to_payload(self):
        return {'schema_version': 1, **asdict(self)}


@dataclass(frozen=True)
class RRGuides:
    diffuse_albedo: bytes
    specular_albedo: bytes
    normal_roughness: bytes
    specular_motion: bytes
    world_to_view: tuple[float, ...]
    view_to_world: tuple[float, ...]

    def __post_init__(self):
        object.__setattr__(self, 'world_to_view', _floats(self.world_to_view, 16, 'world_to_view'))
        object.__setattr__(self, 'view_to_world', _floats(self.view_to_world, 16, 'view_to_world'))
        _inverse(self.world_to_view, self.view_to_world)

    def encode(self, settings):
        pixels = settings.input_width * settings.input_height
        for name, stride in (('diffuse_albedo', 8), ('specular_albedo', 8), ('normal_roughness', 8), ('specular_motion', 4)):
            plane = getattr(self, name)
            if type(plane) is not bytes or len(plane) != pixels * stride:
                raise ValueError(f'RR {name} has the wrong type or byte length')
        # The native engine validates finite values, reflectance, normalized
        # normals and linear roughness before upload, independently of manifests.
        return (struct.pack('<32f', *self.world_to_view, *self.view_to_world)
                + self.diffuse_albedo + self.specular_albedo + self.normal_roughness + self.specular_motion)
