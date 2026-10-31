# Live view

Open **Live view** in the dashboard navigation to see the display output.
Frames refresh about once a second at half the display resolution, without
audio. Images, webpages and video are captured from the compositor.

Capture runs only in response to frame requests. Closing the page, hiding
the tab or pressing **Pause** stops requests. An in-progress capture can
finish after you leave, but no background encoder or recording remains.
Multiple browsers share a capture for one second. If capture fails, the
page hides the old frame and retries.

This feature targets the x86 Wayland viewer. The same capture path is
installed on Pi 5 and generic ARM64 Wayland images, but has not been
verified on those devices. Direct EGLFS and framebuffer viewers are
unsupported. The feed shows the player output, rather than measuring the
physical TV panel or its brightness.

## Install on an existing x86 device

This branch starts from `v2026.10.0`. Back up your installation through
Settings, then run these commands as your existing Anthias install user:

```bash
cd ~/anthias
git fetch diniptools
git switch --track diniptools/feat/live-view

BUILD_TARGET=x86 ENVIRONMENT=production \
  ./bin/generate_dev_mode_dockerfiles.sh

MODE=build DOCKER_TAG=diniptools-live-view \
  ./bin/upgrade_containers.sh
```

The `diniptools` remote should point to
`https://github.com/DinipTools/Anthias.git`. If the local branch already
exists, use `git switch feat/live-view` and `git pull --ff-only` instead
of `git switch --track`. Preserve local edits before switching branches.

The build reuses the existing configuration and media mounts. If the
upgrade script exits because `/var/run/reboot-required` exists, reboot,
then rerun its `MODE=build` command. Open `/live-view/` on the device's
existing dashboard address after the containers start. Enable dashboard
authentication through Settings if you want login required for the feed.

Future updates use `git pull --ff-only` followed by the two build commands.
The standard installer downloads official images, which do not contain
this feature.

## Implementation and checks

`anthias_server/app/live_view.py` serves the page and a JPEG endpoint using
the dashboard's authentication rules. The endpoint requests a frame through
the existing Redis viewer command bus. `anthias_viewer/live_view.py` captures
Cage's composed output using `grim`, then encodes a JPEG with FFmpeg.
Debian's `grim` package has JPEG support disabled. Redis retries and both
capture subprocesses have bounded timeouts. Replies expire after 30 seconds,
and the endpoint rejects frames older than five seconds.

Two designs were compared: per-frame request/reply and a background capture
thread controlled by a short demand lease. The designers and reviewer,
all using the same model family, chose request/reply for its smaller lifecycle
and zero idle capture. Timestamp validation was retained from the lease
design. A capture can occupy the viewer command subscriber for up to two
seconds, including on a display failure; playback itself runs separately.

Run focused checks:

```bash
bun run build
uv run --group test pytest tests/test_live_view.py tests/test_live_view_browser.py
```

To verify moving video capture without a physical TV, install `cage`, `grim`
and `ffmpeg`, then run:

```bash
python tools/verify_live_view_capture.py
```

The script creates a private headless Cage session, plays a generated test
video and checks that the actual capture code returns changing JPEG frames.
The browser tests exercise the built page, including pause, hidden tabs,
failed captures and requests that take longer than the polling interval.
