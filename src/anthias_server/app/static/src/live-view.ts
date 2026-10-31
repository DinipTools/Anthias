function startLiveView(root: HTMLElement): () => void {
  const image = root.querySelector<HTMLImageElement>('[data-live-image]')
  const status = root.querySelector<HTMLElement>('[data-live-status]')
  const button = root.querySelector<HTMLButtonElement>('[data-live-pause]')
  const endpoint = root.dataset.streamUrl
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

  async function showFrame(value: unknown, job: AbortController): Promise<void> {
    if (typeof value !== 'object' || value === null) throw new Error('Invalid live stream.')
    if ('error' in value && typeof value.error === 'string') {
      clearImage()
      ui.status.textContent = value.error
      return
    }
    if (!('jpeg' in value) || typeof value.jpeg !== 'string' ||
        value.jpeg.length > 1400000 || !('captured_at' in value) ||
        typeof value.captured_at !== 'number' || !Number.isFinite(value.captured_at)) {
      throw new Error('Invalid live frame.')
    }
    const bytes = Uint8Array.from(atob(value.jpeg), (c) => c.charCodeAt(0))
    const nextUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' }))
    try {
      const next = new Image()
      next.alt = ui.image.alt
      next.dataset.liveImage = ''
      next.src = nextUrl
      await next.decode()
      if (job.signal.aborted) return
      // Keep the displayed frame until its replacement is fully decoded.
      const previousUrl = imageUrl
      ui.image.replaceWith(next)
      ui.image = next
      imageUrl = nextUrl
      if (previousUrl) URL.revokeObjectURL(previousUrl)
      ui.status.textContent = 'Live · 10 FPS'
    } finally {
      if (imageUrl !== nextUrl) URL.revokeObjectURL(nextUrl)
    }
  }

  async function connect(): Promise<void> {
    if (stopped || paused || document.hidden || pending) return
    const job = new AbortController()
    pending = job
    let watchdog = setTimeout(() => job.abort(), 8000)
    try {
      const response = await fetch(ui.endpoint, {
        signal: job.signal, cache: 'no-store',
        credentials: 'same-origin', redirect: 'error',
      })
      if (!response.ok || !response.body ||
          !response.headers.get('Content-Type')?.startsWith('application/x-ndjson')) {
        throw new Error('Live view is unavailable. Reconnecting…')
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      try {
        while (!job.signal.aborted) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          if (buffer.length > 2000000) throw new Error('Invalid live stream.')
          let newline: number
          while ((newline = buffer.indexOf('\n')) >= 0) {
            const line = buffer.slice(0, newline)
            buffer = buffer.slice(newline + 1)
            if (!line) continue
            const frame: unknown = JSON.parse(line)
            await showFrame(frame, job)
            clearTimeout(watchdog)
            watchdog = setTimeout(() => job.abort(), 5000)
          }
        }
      } finally {
        await reader.cancel().catch(() => {})
      }
    } catch (error: unknown) {
      if (!paused && !document.hidden && !stopped) {
        clearImage()
        ui.status.textContent = error instanceof Error && error.name !== 'AbortError'
          ? error.message : 'Connection interrupted. Reconnecting…'
      }
    } finally {
      clearTimeout(watchdog)
      pending = null
      if (!stopped && !paused && !document.hidden) timer = setTimeout(connect, 1000)
    }
  }

  function visibility(): void {
    clearTimeout(timer)
    if (document.hidden) {
      pending?.abort()
      ui.status.textContent = 'Paused while this tab is hidden.'
    } else if (!paused) {
      ui.status.textContent = 'Connecting to the display…'
      void connect()
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
      ui.status.textContent = 'Connecting to the display…'
      void connect()
    }
  }

  document.addEventListener('visibilitychange', visibility)
  ui.button.addEventListener('click', toggle)
  void connect()
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
