"""Exercise the actual capture code against moving video under headless Cage.

Run with Python 3.13+, cage, grim and ffmpeg installed. No TV required.
"""

import io
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from time import monotonic

from PIL import Image


def capture_video(directory: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    capture = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / 'src/anthias_viewer/live_view.py'
        )
    )
    with (directory / 'player.log').open('w') as log:
        player = subprocess.Popen(
            [
                'ffplay',
                '-loglevel',
                'error',
                '-an',
                '-fs',
                '-loop',
                '0',
                str(directory / 'motion.avi'),
            ],
            env={
                **os.environ,
                'SDL_VIDEODRIVER': 'wayland',
                'SDL_RENDER_DRIVER': 'software',
                'SDL_VIDEO_WAYLAND_ALLOW_LIBDECOR': '0',
            },
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with (directory / 'recorder.log').open('w') as recorder_log:
                recorder = subprocess.Popen(
                    capture['recorder_command'](),
                    stdout=subprocess.PIPE,
                    stderr=recorder_log,
                )
                try:
                    capture['jpeg_frames'].__globals__['_wanted_until'] = (
                        monotonic() + 6
                    )
                    frames: set[bytes] = set()
                    count = 0
                    first = None
                    last = monotonic()
                    for frame in capture['jpeg_frames'](recorder):
                        Image.open(io.BytesIO(frame)).verify()
                        now = monotonic()
                        if first is None:
                            first = now
                        last = now
                        count += 1
                        frames.add(frame)
                    if first is None or count < 35 or len(frames) < 3:
                        raise AssertionError(
                            f'{count} frames, {len(frames)} distinct. {Path(recorder_log.name).read_text()}'
                        )
                    print(
                        f'PASS: changing video, {count} frames, {(count - 1) / (last - first):.1f} FPS'
                    )
                finally:
                    recorder.terminate()
                    recorder.wait(timeout=5)
        except Exception:
            print(Path(log.name).read_text(), flush=True)
            raise
        finally:
            player.terminate()
            player.wait(timeout=5)


def main() -> None:
    if len(sys.argv) > 1:
        capture_video(Path(sys.argv[1]))
        return
    with tempfile.TemporaryDirectory(prefix='anthias-live-view-') as temporary:
        directory = Path(temporary)
        runtime = directory / 'runtime'
        runtime.mkdir(mode=0o700)
        subprocess.run(
            [
                'ffmpeg',
                '-v',
                'error',
                '-f',
                'lavfi',
                '-i',
                'testsrc2=size=640x360:rate=10',
                '-t',
                '6',
                '-c:v',
                'mpeg4',
                str(directory / 'motion.avi'),
            ],
            check=True,
            timeout=15,
        )
        subprocess.run(
            [
                'cage',
                '--',
                sys.executable,
                str(Path(__file__).resolve()),
                str(directory),
            ],
            env={
                **os.environ,
                'XDG_RUNTIME_DIR': str(runtime),
                'WLR_BACKENDS': 'headless',
                'WLR_RENDERER': 'pixman',
                'WLR_HEADLESS_OUTPUTS': '1',
                'WLR_LIBINPUT_NO_DEVICES': '1',
            },
            check=True,
            timeout=30,
        )


if __name__ == '__main__':
    main()
