import base64
import json
import subprocess
from time import time
from unittest import mock

import pytest
import redis
from django.test import Client
from django.urls import reverse

from anthias_server.app import live_view as views
from anthias_viewer import live_view

JPEG = b'\xff\xd8screen pixels\xff\xd9'


@pytest.fixture(autouse=True)
def reset_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_view, '_cached', None)
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')


def test_capture_returns_pixels_and_shares_work_between_browsers() -> None:
    with mock.patch.object(live_view.subprocess, 'run') as run:
        run.return_value.stdout = JPEG
        first = live_view.capture_frame()
        second = live_view.capture_frame()
    assert base64.b64decode(first['jpeg']) == JPEG
    assert first == second
    assert run.call_count == 2


def test_capture_failure_replaces_old_pixels_and_recovers() -> None:
    with (
        mock.patch.object(live_view, 'monotonic', side_effect=[0, 2, 2, 4, 4]),
        mock.patch.object(live_view.subprocess, 'run') as run,
    ):
        run.return_value.stdout = JPEG
        assert 'jpeg' in live_view.capture_frame()
        run.side_effect = subprocess.TimeoutExpired('grim', 2)
        assert 'error' in live_view.capture_frame()
        run.side_effect = None
        assert 'jpeg' in live_view.capture_frame()


@pytest.mark.parametrize(
    'failure',
    [FileNotFoundError(), OSError(), subprocess.TimeoutExpired('grim', 2)],
)
def test_capture_errors_are_explicit_and_cached(failure: Exception) -> None:
    with mock.patch.object(
        live_view.subprocess, 'run', side_effect=failure
    ) as run:
        assert 'error' in live_view.capture_frame()
        assert 'error' in live_view.capture_frame()
    assert run.call_count == 1


def test_unsupported_display_does_not_attempt_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('WAYLAND_DISPLAY')
    with mock.patch.object(live_view.subprocess, 'run') as run:
        assert 'Wayland' in live_view.capture_frame()['error']
    run.assert_not_called()


def test_live_viewer_command_replies_with_capture() -> None:
    from anthias_viewer import commands, reply_sender

    with (
        mock.patch(
            'anthias_viewer.capture_frame',
            return_value={'error': 'No display'},
        ),
        mock.patch.object(reply_sender, 'send') as send,
    ):
        commands['live_frame']('request-id')
        commands['live_frame'](None)
    send.assert_called_once_with('request-id', {'error': 'No display'})


def _reply() -> dict[str, str | float]:
    return {'jpeg': base64.b64encode(JPEG).decode(), 'captured_at': time()}


def test_frame_delivers_uncached_jpeg_using_correlated_request(
    client: Client,
) -> None:
    with mock.patch.object(views.redis, 'Redis') as connect:
        bus = connect.return_value.__enter__.return_value
        bus.blpop.return_value = ('reply-key', json.dumps(_reply()))
        response = client.get(reverse('anthias_app:live_frame'))
    assert response.status_code == 200
    assert response.content == JPEG
    assert response['Content-Type'] == 'image/jpeg'
    assert 'no-store' in response['Cache-Control']
    assert 'X-Captured-At' in response
    command = bus.publish.call_args.args[1]
    correlation = command.split('&')[1]
    assert command.startswith('viewer live_frame&')
    assert bus.blpop.call_args.args[0].endswith(correlation)


@pytest.mark.parametrize(
    'reply',
    [
        None,
        'null',
        'not-json',
        json.dumps({'error': 'TV is off'}),
        json.dumps({'jpeg': 'bad', 'captured_at': 0}),
        json.dumps({'jpeg': 'bad', 'captured_at': float('nan')}),
        json.dumps({'jpeg': 'bad', 'captured_at': time()}),
        json.dumps(
            {
                'jpeg': base64.b64encode(JPEG).decode(),
                'captured_at': time() - 60,
            }
        ),
    ],
)
def test_unavailable_or_invalid_frames_never_serve_old_pixels(
    client: Client, reply: str | None
) -> None:
    with mock.patch.object(views.redis, 'Redis') as connect:
        connect.return_value.__enter__.return_value.blpop.return_value = (
            ('key', reply) if reply is not None else None
        )
        response = client.get(reverse('anthias_app:live_frame'))
    assert response.status_code == 503
    assert 'error' in response.json()
    assert 'no-store' in response['Cache-Control']


def test_redis_failure_does_not_crash_live_view(client: Client) -> None:
    with mock.patch.object(
        views.redis, 'Redis', side_effect=redis.ConnectionError
    ):
        assert client.get(reverse('anthias_app:live_frame')).status_code == 503


@pytest.mark.django_db
@pytest.mark.parametrize('route', ['live_view', 'live_frame'])
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
    assert '/login/' in response['Location']
    connect.assert_not_called()


@pytest.mark.django_db
def test_live_page_contains_feed_and_controls(client: Client) -> None:
    response = client.get(reverse('anthias_app:live_view'))
    assert response.status_code == 200
    assert b'data-live-image' in response.content
    assert b'data-live-pause' in response.content
    assert b'live-view.js' in response.content
