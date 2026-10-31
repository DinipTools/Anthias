"""One continuous encoder, leased only by open live-view streams."""

import base64
import json
import logging
import os
import selectors
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from time import monotonic, time

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from anthias_common.live_view import (
    FPS,
    FRAME_CHANNEL,
    LEASE_SECONDS,
)

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_wanted_until = 0.0
_worker: threading.Thread | None = None


def request_stream(_: str | None = None) -> None:
    global _wanted_until, _worker
    with _lock:
        _wanted_until = monotonic() + LEASE_SECONDS
        if _worker is None:
            _worker = threading.Thread(
                target=_record, name='live-view', daemon=True
            )
            _worker.start()


def _wanted() -> bool:
    with _lock:
        return monotonic() < _wanted_until


def jpeg_frames(process: subprocess.Popen[bytes]) -> Iterator[bytes]:
    """Read JPEGs without blocking lease expiry on a silent recorder."""
    if process.stdout is None:
        raise RuntimeError('Recorder has no output pipe')
    buffer = bytearray()
    last_frame = monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while _wanted():
            if monotonic() - last_frame > 5:
                raise RuntimeError('Screen capture stopped producing frames')
            if not selector.select(timeout=0.2):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Screen capture exited')
            buffer.extend(chunk)
            if len(buffer) > 2_000_000:
                raise RuntimeError('Screen capture frame is too large')
            while True:
                start = buffer.find(b'\xff\xd8')
                end = buffer.find(b'\xff\xd9', max(start, 0))
                if start < 0 or end < 0:
                    break
                frame = bytes(buffer[start : end + 2])
                del buffer[: end + 2]
                last_frame = monotonic()
                yield frame


def recorder_command() -> list[str]:
    return [
        'wf-recorder',
        '-y',
        '--no-dmabuf',
        '-D',
        '-r',
        str(FPS),
        '-c',
        'mjpeg',
        '-m',
        'mjpeg',
        '-x',
        'yuvj420p',
        '-F',
        "scale=w='min(960,iw)':h=-2",
        '-p',
        'threads=1',
        '-f',
        '/dev/stdout',
    ]


def _publish(client: redis.Redis, payload: dict[str, str | float]) -> None:
    encoded = json.dumps(payload)
    client.publish(FRAME_CHANNEL, encoded)


def _record() -> None:
    global _worker
    try:
        with redis.Redis(
            host='redis',
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            try:
                if not os.environ.get('WAYLAND_DISPLAY'):
                    raise RuntimeError('Live view requires a Wayland display')
                with tempfile.TemporaryFile() as errors:
                    process = subprocess.Popen(
                        recorder_command(),
                        stdout=subprocess.PIPE,
                        stderr=errors,
                        bufsize=0,
                    )
                    try:
                        for frame in jpeg_frames(process):
                            _publish(
                                client,
                                {
                                    'jpeg': base64.b64encode(frame).decode(
                                        'ascii'
                                    ),
                                    'captured_at': time(),
                                },
                            )
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        if process.stdout is not None:
                            process.stdout.close()
                        errors.seek(0)
                        detail = errors.read(4096).decode(errors='replace')
                        if detail:
                            logger.debug('Live recorder: %s', detail)
            except (OSError, RuntimeError, redis.RedisError) as exc:
                logger.debug('Live view unavailable: %s', exc)
                _publish(
                    client,
                    {
                        'error': 'Cannot capture the display. Check the TV and viewer.'
                    },
                )
    except (OSError, redis.RedisError):
        logger.debug('Live view Redis unavailable', exc_info=True)
    finally:
        with _lock:
            _worker = None
