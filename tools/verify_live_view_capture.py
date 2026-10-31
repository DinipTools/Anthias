"""Exercise the actual capture code against moving video under headless Cage.

Run with Python 3.13+, Cage, wf-recorder, wlr-randr, FFmpeg and Pillow.
Set ANTHIAS_TEST_ROTATION to 0, 90, 180 or 270. No TV required.
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
    rotation = int(os.environ.get('ANTHIAS_TEST_ROTATION', '0'))
    transform = {0: 'normal', 90: '270', 180: '180', 270: '90'}[rotation]
    subprocess.run(
        ['wlr-randr', '--output', 'HEADLESS-1', '--transform', transform],
        check=True,
        timeout=5,
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
                    capture['recorder_command'](rotation),
                    stdout=subprocess.PIPE,
                    stderr=recorder_log,
                )
                try:
                    capture['jpeg_frames'].__globals__['_wanted_until'] = (
                        monotonic() + 6
                    )
                    capture['jpeg_frames'].__globals__['_rotation'] = rotation
                    frames: set[bytes] = set()
                    count = 0
                    size = None
                    first = None
                    last = monotonic()
                    for frame in capture['jpeg_frames'](recorder, rotation):
                        with Image.open(io.BytesIO(frame)) as image:
                            image.load()
                            size = image.size
                            if rotation in (90, 270):
                                assert image.width < image.height, (
                                    f'Portrait display captured as {image.size}'
                                )
                            else:
                                assert image.width > image.height, (
                                    f'Landscape display captured as {image.size}'
                                )
                            if count >= 15:
                                rgb = image.convert('RGB')
                                corners = [
                                    ((1, 1), (255, 0, 0)),
                                    ((5, 1), (0, 255, 0)),
                                    ((1, 5), (0, 0, 255)),
                                    ((5, 5), (255, 255, 0)),
                                ]
                                for (x, y), expected in corners:
                                    actual = rgb.getpixel(
                                        (
                                            image.width * x // 6,
                                            image.height * y // 6,
                                        )
                                    )
                                    assert all(
                                        abs(a - b) < 60
                                        for a, b in zip(actual, expected)
                                    ), (rotation, (x, y), actual, expected)
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
                        f'PASS: rotation {rotation}, {size}, changing video, {count} frames, {(count - 1) / (last - first):.1f} FPS'
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
        rotation = int(os.environ.get('ANTHIAS_TEST_ROTATION', '0'))
        size = '360x640' if rotation in (90, 270) else '640x360'
        pattern = (
            f'testsrc2=size={size}:rate=10,'
            'drawbox=x=0:y=0:w=iw/3:h=ih/3:color=red:t=fill,'
            'drawbox=x=iw*2/3:y=0:w=iw/3:h=ih/3:color=lime:t=fill,'
            'drawbox=x=0:y=ih*2/3:w=iw/3:h=ih/3:color=blue:t=fill,'
            'drawbox=x=iw*2/3:y=ih*2/3:w=iw/3:h=ih/3:color=yellow:t=fill'
        )
        subprocess.run(
            [
                'ffmpeg',
                '-v',
                'error',
                '-f',
                'lavfi',
                '-i',
                pattern,
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
