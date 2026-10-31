"""Run after `bun run build`; browser exercises the shipped live-view bundle."""

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from PIL import Image
from playwright.sync_api import Page, Route, expect

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
BUNDLE = (
    Path(__file__).resolve().parents[1]
    / 'src/anthias_server/app/static/dist/js/live-view.js'
)


def _open(
    page: Page, client: Client, handler: Callable[[Route], None]
) -> None:
    if not BUNDLE.exists():
        pytest.skip('run bun run build first')
    html = client.get(reverse('anthias_app:live_view')).content.decode()

    def serve(route: Route) -> None:
        if route.request.url.endswith('/live-view/'):
            route.fulfill(body=html, content_type='text/html')
        elif route.request.url.endswith('/js/live-view.js'):
            route.fulfill(
                body=BUNDLE.read_text(), content_type='application/javascript'
            )
        else:
            route.abort()

    page.route('http://live.test/**', serve)
    page.route('http://live.test/live-view/frame/', handler)
    page.clock.install()
    page.goto('http://live.test/live-view/')


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new('RGB', (64, 36), 'red').save(output, format='JPEG')
    return output.getvalue()


def test_live_feed_pauses_resumes_and_stops_when_hidden(
    page: Page, client: Client
) -> None:
    requests: list[str] = []

    def frame(route: Route) -> None:
        requests.append(route.request.url)
        route.fulfill(body=_jpeg(), content_type='image/jpeg')

    _open(page, client, frame)
    image = page.locator('[data-live-image]')
    expect(image).to_be_visible()
    expect(page.locator('[data-live-status]')).to_contain_text(
        'Live · Updated'
    )
    page.get_by_role('button', name='Pause', exact=True).click()
    count = len(requests)
    page.clock.fast_forward(5000)
    assert len(requests) == count
    page.get_by_role('button', name='Resume', exact=True).click()
    expect(image).to_be_visible()
    assert len(requests) > count
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {value: true, configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    count = len(requests)
    page.clock.fast_forward(5000)
    assert len(requests) == count
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {value: false, configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    expect(image).to_be_visible()
    assert len(requests) > count


def test_failed_capture_hides_old_frame_and_recovers(
    page: Page, client: Client
) -> None:
    unavailable = False

    def frame(route: Route) -> None:
        if unavailable:
            route.fulfill(status=503, json={'error': 'The TV is unavailable.'})
        else:
            route.fulfill(body=_jpeg(), content_type='image/jpeg')

    _open(page, client, frame)
    image = page.locator('[data-live-image]')
    expect(image).to_be_visible()
    unavailable = True
    page.clock.fast_forward(1100)
    expect(image).to_be_hidden()
    expect(page.locator('[data-live-status]')).to_have_text(
        'The TV is unavailable.'
    )
    unavailable = False
    page.clock.fast_forward(1100)
    expect(image).to_be_visible()


def test_slow_capture_does_not_create_overlapping_requests(
    page: Page, client: Client
) -> None:
    requests: list[Route] = []
    _open(page, client, lambda route: requests.append(route))
    expect(page.locator('[data-live-status]')).to_contain_text('Connecting')
    page.clock.fast_forward(4000)
    assert len(requests) == 1
    page.get_by_role('button', name='Pause', exact=True).click()
    page.clock.fast_forward(5000)
    assert len(requests) == 1


def test_leaving_the_page_stops_capture_requests(
    page: Page, client: Client
) -> None:
    requests: list[str] = []

    def frame(route: Route) -> None:
        requests.append(route.request.url)
        route.fulfill(body=_jpeg(), content_type='image/jpeg')

    _open(page, client, frame)
    expect(page.locator('[data-live-image]')).to_be_visible()
    page.goto('about:blank')
    count = len(requests)
    page.clock.fast_forward(5000)
    assert len(requests) == count
