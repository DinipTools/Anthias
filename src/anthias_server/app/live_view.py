"""Authenticated continuous display feed over the existing HTTP connection."""

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic, time

import redis.asyncio as redis
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from anthias_common.live_view import FRAME_CHANNEL
from anthias_server.app.helpers import template
from anthias_server.lib.auth import authorized
from anthias_server.settings import VIEWER_CHANNEL


@never_cache
@authorized
@require_GET
def page(request: HttpRequest) -> HttpResponse:
    return template(request, 'live_view.html', {'active_nav': 'live_view'})


async def frames() -> AsyncIterator[bytes]:
    try:
        async with (
            redis.Redis(
                host='redis',
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                retry=Retry(NoBackoff(), 0),
            ) as client,
            client.pubsub() as subscription,
        ):
            await subscription.subscribe(FRAME_CHANNEL)
            # Reconnect periodically so authorization is checked again.
            deadline = monotonic() + 30
            next_renewal = 0.0
            while monotonic() < deadline:
                if monotonic() >= next_renewal:
                    await client.publish(VIEWER_CHANNEL, 'viewer live_stream')
                    next_renewal = monotonic() + 1
                message = await subscription.get_message(
                    ignore_subscribe_messages=True,
                    timeout=0.2,
                )
                if message is None:
                    await asyncio.sleep(0.01)
                    continue
                payload = message['data']
                frame = json.loads(payload)
                if (
                    'jpeg' in frame
                    and not 0 <= time() - frame['captured_at'] <= 3
                ):
                    continue
                yield (payload + '\n').encode()
    except (redis.RedisError, OSError, ValueError, KeyError, TypeError):
        yield b'{"error":"Live view is unavailable. Reconnecting..."}\n'


@never_cache
@authorized
@require_GET
def stream(request: HttpRequest) -> StreamingHttpResponse:
    response = StreamingHttpResponse(
        frames(), content_type='application/x-ndjson'
    )
    response['X-Accel-Buffering'] = 'no'
    return response
