import asyncio
import base64
import json
import os
import subprocess
import sys
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, time
from unittest import mock

import pytest
import redis
from django.test import Client
from django.urls import reverse

from anthias_common.live_view import FRAME_CHANNEL
from anthias_server.app import live_view as views
from anthias_viewer import live_view


def _run(scenario: Callable[[], Coroutine[object, object, None]]) -> None:
    # Playwright's sync API can already own the main thread's event loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(scenario())).result()


JPEG = b'\xff\xd8pixels\xff\xd9'


@pytest.fixture(autouse=True)
def reset_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_view, '_worker', None)
    monkeypatch.setattr(live_view, '_wanted_until', 0.0)
    monkeypatch.setattr(live_view, '_rotation', 0)
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')


def test_viewers_share_one_encoder_and_renew_its_lease() -> None:
    with mock.patch.object(live_view.threading, 'Thread') as thread:
        live_view.request_stream()
        first = live_view._wanted_until
        live_view.request_stream()
    assert live_view._wanted_until >= first
    assert thread.call_count == 1
    thread.return_value.start.assert_called_once()


def test_no_viewer_means_no_encoder() -> None:
    assert not live_view._wanted()
    assert live_view._worker is None


def test_rotation_change_retires_the_old_encoder() -> None:
    with mock.patch.object(live_view.threading, 'Thread') as thread:
        live_view.request_stream(rotation=0)
        assert live_view._wanted(0)
        live_view.request_stream(rotation=270)
        assert not live_view._wanted(0)
        assert live_view._wanted(270)
    assert thread.call_count == 1


def test_live_stream_uses_the_applied_display_rotation() -> None:
    import anthias_viewer

    with (
        mock.patch.object(anthias_viewer, '_last_applied_rotation', 270),
        mock.patch.object(anthias_viewer, 'request_stream') as request,
    ):
        anthias_viewer.commands['live_stream'](None)
    request.assert_called_once_with(rotation=270)


def test_expired_lease_stops_a_real_encoder_process() -> None:
    bus = mock.MagicMock()
    children: list[subprocess.Popen[bytes]] = []
    original = subprocess.Popen

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = original(*args, **kwargs)
        children.append(process)
        return process

    command = [
        sys.executable,
        '-c',
        "import sys,time\nwhile True:\n sys.stdout.buffer.write(b'\\xff\\xd8pixels\\xff\\xd9');sys.stdout.buffer.flush();time.sleep(.02)",
    ]
    live_view._wanted_until = monotonic() + 0.25
    with (
        mock.patch.object(live_view.redis, 'Redis', return_value=bus),
        mock.patch.object(live_view, 'recorder_command', return_value=command),
        mock.patch.object(live_view.subprocess, 'Popen', side_effect=spawn),
    ):
        live_view._record()
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdout.closed
    pipeline = bus.__enter__.return_value
    assert pipeline.publish.call_count >= 2
    payload = json.loads(pipeline.publish.call_args.args[1])
    assert base64.b64decode(payload['jpeg']) == JPEG
    assert live_view._worker is None


def test_live_stream_command_only_renews_capture_demand() -> None:
    from anthias_viewer import commands

    with mock.patch.object(live_view.threading, 'Thread') as thread:
        commands['live_stream'](None)
    thread.return_value.start.assert_called_once()


def _bus(
    messages: list[dict[str, object]],
) -> tuple[mock.MagicMock, mock.MagicMock]:
    bus = mock.MagicMock()
    client = bus.__aenter__.return_value
    client.pubsub = mock.MagicMock()
    subscription = client.pubsub.return_value.__aenter__.return_value
    subscription.subscribe = mock.AsyncMock()
    subscription.get_message = mock.AsyncMock(side_effect=messages)
    client.publish = mock.AsyncMock()
    return bus, subscription


def test_http_stream_delivers_frames_and_releases_subscription() -> None:
    payload = json.dumps(
        {'jpeg': base64.b64encode(JPEG).decode(), 'captured_at': time()}
    )
    bus, subscription = _bus([{'data': payload}])

    async def scenario() -> None:
        with mock.patch.object(views.redis, 'Redis', return_value=bus):
            stream = views.frames()
            assert await anext(stream) == (payload + '\n').encode()
            await stream.aclose()

    _run(scenario)
    subscription.subscribe.assert_awaited_once_with(FRAME_CHANNEL)
    bus.__aenter__.return_value.pubsub.return_value.__aexit__.assert_awaited_once()
    bus.__aenter__.return_value.publish.assert_awaited_once_with(
        'anthias.viewer', 'viewer live_stream'
    )


def test_stream_skips_stale_frames() -> None:
    old = json.dumps({'jpeg': 'old', 'captured_at': time() - 20})
    current = json.dumps({'jpeg': 'current', 'captured_at': time()})
    bus, _ = _bus([{'data': old}, {'data': current}])

    async def scenario() -> None:
        with mock.patch.object(views.redis, 'Redis', return_value=bus):
            stream = views.frames()
            assert json.loads(await anext(stream))['jpeg'] == 'current'
            await stream.aclose()

    _run(scenario)


def test_stream_redis_error_is_reported_without_crashing() -> None:
    async def scenario() -> None:
        with mock.patch.object(
            views.redis, 'Redis', side_effect=redis.ConnectionError
        ):
            stream = views.frames()
            assert 'error' in json.loads(await anext(stream))
            await stream.aclose()

    _run(scenario)


@pytest.mark.django_db
@pytest.mark.parametrize('route', ['live_view', 'live_stream'])
def test_authentication_precedes_capture(client: Client, route: str) -> None:
    with (
        mock.patch.dict(
            'anthias_server.settings.settings.data',
            {'auth_backend': 'auth_basic'},
        ),
        mock.patch.object(views.redis, 'Redis') as connect,
    ):
        response = client.get(reverse(f'anthias_app:{route}'))
    assert response.status_code == 302
    connect.assert_not_called()


def test_stream_is_uncached_and_not_proxy_buffered(client: Client) -> None:
    response = client.get(reverse('anthias_app:live_stream'))
    assert response.status_code == 200
    assert response.is_async
    assert 'no-store' in response['Cache-Control']
    assert response['X-Accel-Buffering'] == 'no'
    assert response['Content-Type'] == 'application/x-ndjson'


@pytest.mark.integration
def test_real_redis_transport_and_disconnect_cleanup() -> None:
    redis_class = views.redis.Redis
    url = os.environ.get('ANTHIAS_LIVE_TEST_REDIS_URL', 'redis://redis:6379/0')

    def connect(**kwargs: object) -> views.redis.Redis:
        kwargs.pop('host', None)
        return redis_class.from_url(url, **kwargs)

    async def scenario() -> None:
        async with redis_class.from_url(
            url, decode_responses=True, socket_connect_timeout=1
        ) as client:
            try:
                await client.ping()
            except redis.RedisError:
                pytest.skip('A real Redis server is required')
            async with client.pubsub() as viewer:
                await viewer.subscribe('anthias.viewer')
                with mock.patch.object(views.redis, 'Redis', connect):
                    stream = views.frames()
                    receiving = asyncio.create_task(anext(stream))
                    try:
                        for _ in range(30):
                            message = await viewer.get_message(
                                ignore_subscribe_messages=True, timeout=0.2
                            )
                            if (
                                message
                                and message['data'] == 'viewer live_stream'
                            ):
                                break
                        else:
                            raise AssertionError('No capture demand arrived')
                        payload = json.dumps(
                            {
                                'jpeg': base64.b64encode(JPEG).decode(),
                                'captured_at': time(),
                            }
                        )
                        await client.publish(FRAME_CHANNEL, payload)
                        assert (
                            await asyncio.wait_for(receiving, 3)
                            == (payload + '\n').encode()
                        )
                    finally:
                        receiving.cancel()
                        await asyncio.gather(receiving, return_exceptions=True)
                        await stream.aclose()
                    for _ in range(20):
                        if (await client.pubsub_numsub(FRAME_CHANNEL))[0][
                            1
                        ] == 0:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError(
                            'Disconnected stream retained its subscription'
                        )

    _run(scenario)
