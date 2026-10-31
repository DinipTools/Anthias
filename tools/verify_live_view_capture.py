"""Exercise the actual capture code against moving video under headless Cage.

Run with Python 3.13+, cage, grim and ffmpeg installed. No TV required.
"""

import base64
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from time import sleep


def capture_video(directory: Path) -> None:
    capture = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / 'src/anthias_viewer/live_view.py'
        )
    )['capture_frame']
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
            frames: set[bytes] = set()
            for _ in range(6):
                sleep(1.1)
                reply = capture()
                if 'jpeg' not in reply:
                    raise AssertionError(reply)
                frames.add(base64.b64decode(reply['jpeg']))
            if len(frames) < 3:
                raise AssertionError(
                    f'Capture did not show motion: {Path(log.name).read_text()}'
                )
            print('PASS: composed Wayland video produces changing JPEG frames')
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
