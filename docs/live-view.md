# Live view

Open **Live view** in the dashboard navigation for a continuous feed targeting
10 FPS, at up to 960 pixels on the longest edge, without audio. Frames are decoded before
replacing the displayed image so updates do not blank the view.

Only an open, visible Live view page requests capture. Pause, closing the page,
or hiding its tab disconnects the feed. After three seconds without demand,
the viewer stops the encoder. Cleanup can take another second. Several browsers
share one encoder. No recording is retained.

The capture path supports the x86 Wayland viewer. Pi 5 and generic ARM64
Wayland images install the same dependency, but have not been verified on
physical hardware. EGLFS and framebuffer viewers are unsupported. The feed
shows the player output and cannot confirm the physical TV panel is working.

## Update an existing x86 installation

Run as your existing Anthias install user:

```bash
cd ~/anthias
git switch feat/live-view
git pull --ff-only

BUILD_TARGET=x86 ENVIRONMENT=production \
  ./bin/generate_dev_mode_dockerfiles.sh

MODE=build DOCKER_TAG=diniptools-live-view \
  ./bin/upgrade_containers.sh
```

Both the server and viewer images need rebuilding. The viewer now needs
`wf-recorder`; updating only the web files is insufficient. The installation
reuses the existing configuration and media mounts. If the upgrade script
exits because `/var/run/reboot-required` exists, reboot and rerun its build command.

On first checkout, fetch the `diniptools` remote and run
`git switch --track diniptools/feat/live-view` instead of switching an existing
local branch. The remote should be `https://github.com/DinipTools/Anthias.git`.
The standard installer downloads official images without this feature.

## Capture and delivery

`anthias_viewer/live_view.py` starts a single `wf-recorder` process when demand
arrives over the existing viewer command bus. It captures the composed Wayland
display and encodes JPEG frames at 10 FPS. A short lease renewed by open streams
controls its lifetime. The worker reads with a timeout so it can stop even when
the recorder is silent. Redis operations have bounded retries.

`anthias_server/app/live_view.py` forwards frames from Redis over one asynchronous
HTTP response at `/live-view/stream/`. It uses the dashboard's authentication
rules, disables proxy buffering, drops stale frames, and reconnects every
30 seconds to check authorization again. Closing the HTTP response releases
the subscription and stops renewing capture demand.

The page decodes each image off-screen, then replaces the previous image.
A capture error hides the last image so it cannot appear live. A disconnected
stream reconnects automatically while the page remains open and visible.

## Verification

```bash
bun run build
uv run --group test pytest tests/test_live_view.py tests/test_live_view_browser.py
```

The Redis integration test uses `redis:6379` by default, or
`ANTHIAS_LIVE_TEST_REDIS_URL` when set. Tests verify shared demand, process cleanup,
authentication, stream cleanup, hidden tabs, pause/resume, and continuous image
visibility while the next frame is decoding.

To exercise actual capture without a physical TV, install `cage`, `wf-recorder`, `wlr-randr`,
`ffmpeg` and Python's Pillow package, then run:

```bash
python tools/verify_live_view_capture.py
```

The encoder follows the rotation actually applied to the TV, including changes
made while a live feed is open. It corrects the raw scanout orientation before
scaling and JPEG encoding.

To verify portrait capture, run `ANTHIAS_TEST_ROTATION=270 python
tools/verify_live_view_capture.py`. Values 0, 90, 180 and 270 are supported;
the check uses colored corners to detect the wrong rotation direction.

This plays a generated video under a private headless Cage instance, checks
every JPEG, verifies that frames change, and measures the delivered capture
rate. It does not benchmark the deployment machine or its remote network.
