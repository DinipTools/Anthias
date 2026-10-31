function startLiveView(root: HTMLElement): () => void {
  const image = root.querySelector<HTMLImageElement>('[data-live-image]')
  const status = root.querySelector<HTMLElement>('[data-live-status]')
  const button = root.querySelector<HTMLButtonElement>('[data-live-pause]')
  const endpoint = root.dataset.frameUrl
  if (!image || !status || !button || !endpoint) return () => {}

  const ui = { image, status, button, endpoint }

  let paused = false
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending: AbortController | null = null
  let imageUrl: string | null = null

  function clearImage(): void {
    ui.image.hidden = true
    ui.image.removeAttribute('src')
    if (imageUrl) URL.revokeObjectURL(imageUrl)
    imageUrl = null
  }

  async function refresh(): Promise<void> {
    if (stopped || paused || document.hidden || pending) return
    const job = new AbortController()
    pending = job
    const timeout = setTimeout(() => job.abort(), 6000)
    try {
      const response = await fetch(ui.endpoint, {
        signal: job.signal,
        cache: 'no-store',
        credentials: 'same-origin',
        redirect: 'error',
      })
      if (!response.ok) {
        const body: unknown = await response.json()
        const message = typeof body === 'object' && body !== null &&
          'error' in body && typeof body.error === 'string'
          ? body.error : 'Live view is unavailable. Retrying…'
        throw new Error(message)
      }
      if (!response.headers.get('Content-Type')?.startsWith('image/jpeg')) {
        throw new Error('Please sign in again to view the display.')
      }
      const blob = await response.blob()
      if (job.signal.aborted) return
      clearImage()
      imageUrl = URL.createObjectURL(blob)
      ui.image.src = imageUrl
      await ui.image.decode()
      if (job.signal.aborted) return
      ui.image.hidden = false
      ui.status.textContent = `Live · Updated ${new Date().toLocaleTimeString()}`
    } catch (error: unknown) {
      if (!paused && !document.hidden && !stopped) {
        clearImage()
        ui.status.textContent = error instanceof Error && error.name !== 'AbortError'
          ? error.message : 'The display did not respond. Retrying…'
      }
    } finally {
      clearTimeout(timeout)
      pending = null
      if (!stopped && !paused && !document.hidden) timer = setTimeout(refresh, 1000)
    }
  }

  function visibility(): void {
    clearTimeout(timer)
    if (document.hidden) {
      pending?.abort()
      ui.status.textContent = 'Paused while this tab is hidden.'
    } else if (!paused) {
      clearImage()
      ui.status.textContent = 'Connecting to the display…'
      void refresh()
    }
  }

  function toggle(): void {
    paused = !paused
    ui.button.textContent = paused ? 'Resume' : 'Pause'
    ui.button.setAttribute('aria-pressed', String(paused))
    clearTimeout(timer)
    if (paused) {
      pending?.abort()
      ui.status.textContent = 'Paused · Showing the last captured frame.'
    } else {
      clearImage()
      ui.status.textContent = 'Connecting to the display…'
      void refresh()
    }
  }

  document.addEventListener('visibilitychange', visibility)
  ui.button.addEventListener('click', toggle)
  void refresh()
  return () => {
    stopped = true
    clearTimeout(timer)
    pending?.abort()
    clearImage()
    document.removeEventListener('visibilitychange', visibility)
    ui.button.removeEventListener('click', toggle)
  }
}

const root = document.querySelector<HTMLElement>('[data-live-view]')
if (root) {
  let stop = startLiveView(root)
  window.addEventListener('pagehide', () => stop())
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) stop = startLiveView(root)
  })
}
