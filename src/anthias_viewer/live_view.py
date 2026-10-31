"""On-demand captures of the composed Wayland display."""

import base64
import os
import subprocess
from time import monotonic, time

type FrameReply = dict[str, str | float]

# The viewer command subscriber serializes callers, including cache access.
_cached: tuple[float, FrameReply] | None = None


def capture_frame() -> FrameReply:
    global _cached
    if _cached is not None and monotonic() - _cached[0] < 1:
        return _cached[1]

    reply: FrameReply
    if not os.environ.get('WAYLAND_DISPLAY'):
        reply = {
            'error': 'Live view requires a Wayland display (x86, Pi 5 or ARM64).'
        }
    else:
        try:
            result = subprocess.run(
                ['grim', '-t', 'ppm', '-s', '0.5', '-'],
                check=True,
                capture_output=True,
                timeout=1,
            )
            # Debian's grim disables JPEG; FFmpeg is already in the viewer.
            result = subprocess.run(
                [
                    'ffmpeg',
                    '-v',
                    'error',
                    '-f',
                    'image2pipe',
                    '-vcodec',
                    'ppm',
                    '-i',
                    'pipe:0',
                    '-frames:v',
                    '1',
                    '-f',
                    'image2pipe',
                    '-vcodec',
                    'mjpeg',
                    '-q:v',
                    '7',
                    '-threads',
                    '1',
                    'pipe:1',
                ],
                input=result.stdout,
                check=True,
                capture_output=True,
                timeout=1,
            )
            jpeg = result.stdout
            if not jpeg.startswith(b'\xff\xd8') or len(jpeg) > 1_000_000:
                reply = {'error': 'The display did not return a usable frame.'}
            else:
                reply = {
                    'jpeg': base64.b64encode(jpeg).decode('ascii'),
                    'captured_at': time(),
                }
        except FileNotFoundError:
            reply = {
                'error': 'Screen capture is not installed. Rebuild the viewer image.'
            }
        except (OSError, subprocess.SubprocessError):
            reply = {
                'error': 'Cannot capture the display. Check that the TV and viewer are on.'
            }

    # Cache failures too, so multiple browsers cannot repeatedly spawn grim.
    _cached = monotonic(), reply
    return reply
