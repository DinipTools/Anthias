"""Operator-only live display frames, carried over the existing viewer bus."""

import base64
import binascii
import json
import math
import uuid
from time import time

import redis
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from redis.backoff import NoBackoff
from redis.retry import Retry

from anthias_server.app.helpers import template
from anthias_server.lib.auth import authorized
from anthias_server.settings import REPLY_KEY_PREFIX, VIEWER_CHANNEL


@never_cache
@authorized
@require_GET
def page(request: HttpRequest) -> HttpResponse:
    return template(request, 'live_view.html', {'active_nav': 'live_view'})


@never_cache
@authorized
@require_GET
def frame(request: HttpRequest) -> HttpResponse:
    correlation_id = uuid.uuid4().hex
    # Bound Redis retries too: a failed live view must not hold an HTTP
    # worker for the default retry budget while the operator needs controls.
    try:
        with redis.Redis(
            host='redis',
            port=6379,
            db=0,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=4,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            client.publish(
                VIEWER_CHANNEL, f'viewer live_frame&{correlation_id}'
            )
            response = client.blpop(
                f'{REPLY_KEY_PREFIX}{correlation_id}', timeout=3
            )
        if response is None:
            return JsonResponse(
                {'error': 'The viewer is not responding. Retrying…'},
                status=503,
            )
        reply = json.loads(response[1])
        if not isinstance(reply, dict):
            raise TypeError('invalid frame reply')
        if isinstance(reply.get('error'), str):
            return JsonResponse({'error': reply['error']}, status=503)
        captured_at = reply.get('captured_at')
        encoded = reply.get('jpeg')
        if (
            not isinstance(captured_at, (int, float))
            or not math.isfinite(captured_at)
            or not 0 <= time() - captured_at <= 5
            or not isinstance(encoded, str)
            or len(encoded) > 1_333_336
        ):
            raise ValueError('stale or invalid frame')
        jpeg = base64.b64decode(encoded, validate=True)
        if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
            raise ValueError('invalid JPEG')
    except (redis.RedisError, OSError, ValueError, TypeError, binascii.Error):
        return JsonResponse(
            {'error': 'Live view is unavailable. Retrying…'}, status=503
        )

    result = HttpResponse(jpeg, content_type='image/jpeg')
    result['X-Captured-At'] = str(captured_at)
    return result
