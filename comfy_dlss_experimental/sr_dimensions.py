"""Orientation-neutral transport budgets, not measured hardware maxima.

Canonical values also generate the native header. Output pixels derive from the
existing CXR1 byte envelope; no standard 4K rectangle is imposed. The input
budget remains unchanged in this output-only expansion, not a hardware maximum.
"""
from fractions import Fraction
import math
from ._sr_limits import LIMITS

SIZE_MODES = ('manual', '1.5x', '2x', '3x')


def dimensions(width, height, *, output=False):
    kind = 'output' if output else 'input'
    side, pixels = LIMITS[kind+'_max_side'], LIMITS[kind+'_max_pixels']
    if (type(width) is not int or type(height) is not int or
            not LIMITS['min_side'] <= min(width,height) <= max(width,height) <= side or width*height > pixels):
        requested = f'{width}x{height}'
        detail = (f'; CXR1 result = {width*height*8+8} bytes / {LIMITS["cxr_max_payload"]} bytes'
                  if output and type(width) is int and type(height) is int else '')
        raise ValueError(f'SR {kind} {requested} exceeds size budget: min side {LIMITS["min_side"]}, max side {side}, max pixels {pixels}{detail}; not a measured GPU limit')


def output_budget_report(width, height):
    dimensions(width, height, output=True)
    return dict(policy='cxr1_result_bytes', pixels=width*height,
                result_bytes=width*height*LIMITS['output_bytes_per_pixel']+LIMITS['output_header_bytes'],
                max_result_bytes=LIMITS['cxr_max_payload'], max_side=LIMITS['output_max_side'],
                gpu_memory_estimate=False, runtime_support='checked_on_create')


def output_size(width, height, size_mode, manual_width, manual_height, *, dlaa=False):
    dimensions(width,height)
    if dlaa:
        return width,height
    if size_mode not in SIZE_MODES:
        raise ValueError('Choose a known SR output size mode')
    if size_mode == 'manual':
        result = (manual_width,manual_height)
    else:
        scale=Fraction(size_mode[:-1])
        # Closest legal even size preserving the EXACT source aspect ratio.
        common=math.gcd(width,height);a,b=width//common,height//common
        unit=math.lcm(2//math.gcd(a,2),2//math.gcd(b,2))
        factor=max((common//unit+1)*unit, round(Fraction(common)*scale/unit)*unit)
        result=(a*factor,b*factor)
    dimensions(*result,output=True)
    if result[0]%2 or result[1]%2 or result[0]*height != result[1]*width or result[0] <= width:
        raise ValueError('SR output must be larger, even and preserve source aspect; use a multiplier for automatic calculation')
    return result
