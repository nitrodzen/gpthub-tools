import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { cancelJob, createJob, fetchResult, getJob, type Job, type JobCapability, type Operation } from './api'
import { getCopy, type Language } from './i18n'

type Tab = 'upscale' | 'remove' | 'convert'
type ConvertMode = 'images' | 'documents' | 'pdf'
type PdfAction = 'pdf-merge' | 'pdf-split' | 'images-to-pdf' | 'pdf-to-images'
type Theme = 'light' | 'dark'
type FormError = { message: string; code?: string }
type TrackedJob = {
  capability: JobCapability
  tab: Tab
  operation: Operation
  sourceFiles: File[]
  fileCount: number
  scale: number
  submittedAt: number
  inputPixels: number
  job: Job | null
  error: FormError | null
  resultUrl: string | null
  previewLoading: boolean
  seen: boolean
}
type TrackedJobs = Partial<Record<Tab, TrackedJob>>

const MAX_UPSCALE_OUTPUT_PIXELS = 420_000_000
const TRACKED_JOBS_STORAGE_KEY = 'gpthub-tracked-jobs-v1'
const READY_FAVICON = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="18" fill="#18a86b"/><path fill="none" stroke="#fff" stroke-linecap="round" stroke-linejoin="round" stroke-width="7" d="m17 33 10 10 21-23"/></svg>')}`

function setFavicon(href: string) {
  let icon = document.querySelector<HTMLLinkElement>('link[rel~="icon"]')
  if (!icon) {
    icon = document.createElement('link')
    icon.rel = 'icon'
    icon.type = 'image/svg+xml'
    document.head.append(icon)
  }
  icon.href = href
}

function isRunning(job: Job | null) {
  return !job || job.status === 'queued' || job.status === 'running'
}

function readTrackedJobs(): TrackedJobs {
  try {
    const stored = JSON.parse(localStorage.getItem(TRACKED_JOBS_STORAGE_KEY) || '{}') as Record<string, Partial<TrackedJob>>
    const restored: TrackedJobs = {}
    for (const tab of ['upscale', 'remove', 'convert'] as Tab[]) {
      const entry = stored[tab]
      if (!entry?.capability?.jobId || !entry.capability.token || !entry.operation || Date.parse(entry.capability.expiresAt || '') <= Date.now()) continue
      restored[tab] = {
        capability: entry.capability,
        tab,
        operation: entry.operation,
        sourceFiles: [],
        fileCount: entry.fileCount || 1,
        scale: entry.scale || 2,
        submittedAt: entry.submittedAt || Date.now(),
        inputPixels: entry.inputPixels || 0,
        job: entry.job || null,
        error: entry.error || null,
        resultUrl: null,
        previewLoading: false,
        seen: Boolean(entry.seen),
      }
    }
    return restored
  } catch {
    return {}
  }
}

async function imagePixelCount(file: File) {
  const bitmap = await createImageBitmap(file)
  const pixels = bitmap.width * bitmap.height
  bitmap.close()
  return pixels
}

export function estimateDuration(operation: Operation, scale: number, fileCount: number, inputPixels = 0) {
  const count = Math.max(1, fileCount)
  const megapixels = inputPixels / 1_000_000
  if (operation === 'upscale') return Math.round((scale === 4 ? 90 * count + megapixels * 30 : 60 * count + megapixels * 12))
  if (operation === 'remove-background') return 60 * count
  if (operation === 'document-convert') return 45 * count
  if (operation === 'pdf-to-images') return 75
  return Math.max(8, 8 * count)
}

export function formatDuration(seconds: number, language: Language) {
  const rounded = Math.max(0, Math.ceil(seconds))
  if (rounded < 60) return `${rounded} ${language === 'ru' ? 'сек' : 'sec'}`
  const minutes = Math.floor(rounded / 60)
  const remainder = rounded % 60
  if (!remainder) return `${minutes} ${language === 'ru' ? 'мин' : 'min'}`
  return `${minutes} ${language === 'ru' ? 'мин' : 'min'} ${remainder} ${language === 'ru' ? 'сек' : 'sec'}`
}

const routeForTab: Record<Tab, string> = {
  upscale: '/upscale',
  remove: '/remove-background',
  convert: '/convert/images',
}

function routeState() {
  const path = window.location.pathname
  const tab: Tab = path.startsWith('/remove-background') ? 'remove' : path.startsWith('/convert') ? 'convert' : 'upscale'
  const mode: ConvertMode = path.startsWith('/convert/documents')
    ? 'documents'
    : path.startsWith('/convert/pdf')
      ? 'pdf'
      : 'images'
  return { tab, mode }
}

function SunIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.64 5.64l1.42 1.42m9.88 9.88 1.42 1.42M18.36 5.64l-1.42 1.42M7.06 16.94l-1.42 1.42"/><circle cx="12" cy="12" r="4"/></svg>
}

function UploadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"/></svg>
}

function SparkIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3ZM18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2Z"/></svg>
}

function Hint({ text, below = false }: { text: string; below?: boolean }) {
  return <span className={`hint ${below ? 'hint-below' : ''}`} data-tip={text} aria-label={text} tabIndex={0}>i</span>
}

const ZOOM_STEP = 0.25
const MAX_ZOOM = 4

export function ZoomPane({ label, src, checkerboard, copy }: { label: string; src: string; checkerboard: boolean; copy: ReturnType<typeof getCopy> }) {
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ startX: number; startY: number; originX: number; originY: number; moved: boolean } | null>(null)
  const suppressClick = useRef(false)
  const [{ zoom, position }, setView] = useState({ zoom: 1, position: { x: 0, y: 0 } })

  useEffect(() => {
    setView({ zoom: 1, position: { x: 0, y: 0 } })
  }, [src])

  const clampPosition = useCallback((next: { x: number; y: number }, level: number) => {
    const bounds = viewport.current?.getBoundingClientRect()
    if (!bounds) return next
    const maxX = (bounds.width * (level - 1)) / 2
    const maxY = (bounds.height * (level - 1)) / 2
    return { x: Math.max(-maxX, Math.min(maxX, next.x)), y: Math.max(-maxY, Math.min(maxY, next.y)) }
  }, [])

  const setZoomLevel = useCallback((next: number | ((current: number) => number)) => {
    setView((current) => {
      const requested = typeof next === 'function' ? next(current.zoom) : next
      const bounded = Math.max(1, Math.min(MAX_ZOOM, Math.round(requested * 100) / 100))
      return { zoom: bounded, position: bounded === 1 ? { x: 0, y: 0 } : clampPosition(current.position, bounded) }
    })
  }, [clampPosition])

  const panBy = useCallback((x: number, y: number) => {
    setView((current) => ({ ...current, position: clampPosition({ x: current.position.x + x, y: current.position.y + y }, current.zoom) }))
  }, [clampPosition])

  const toggleZoom = () => setZoomLevel(zoom === 1 ? 2 : 1)

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      setZoomLevel((current) => current + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP))
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [setZoomLevel])

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (zoom === 1) return
    drag.current = { startX: event.clientX, startY: event.clientY, originX: position.x, originY: position.y, moved: false }
    if (event.currentTarget.setPointerCapture) event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const activeDrag = drag.current
    if (!activeDrag) return
    const distanceX = event.clientX - activeDrag.startX
    const distanceY = event.clientY - activeDrag.startY
    if (Math.abs(distanceX) > 3 || Math.abs(distanceY) > 3) activeDrag.moved = true
    setView((current) => ({
      ...current,
      position: clampPosition({
        x: activeDrag.originX + distanceX,
        y: activeDrag.originY + distanceY,
      }, current.zoom),
    }))
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    suppressClick.current = Boolean(drag.current?.moved)
    drag.current = null
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture?.(event.pointerId)
  }

  const onClick = () => {
    if (suppressClick.current) {
      suppressClick.current = false
      return
    }
    toggleZoom()
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const distance = event.shiftKey ? 64 : 24
    const directions: Record<string, [number, number]> = {
      ArrowLeft: [-distance, 0], ArrowRight: [distance, 0], ArrowUp: [0, -distance], ArrowDown: [0, distance],
    }
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleZoom(); return }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); setZoomLevel(zoom + ZOOM_STEP); return }
    if (event.key === '-') { event.preventDefault(); setZoomLevel(zoom - ZOOM_STEP); return }
    if (event.key === '0') { event.preventDefault(); setZoomLevel(1); return }
    if (directions[event.key] && zoom > 1) {
      event.preventDefault()
      panBy(...directions[event.key])
    }
  }

  return (
    <section className={`compare-pane ${checkerboard ? 'checkerboard' : ''}`}>
      <header className="compare-pane-head">
        <strong>{label}</strong>
        <div className="compare-pane-controls" aria-label={`${copy.zoomLevel}: ${label}`}>
          <button type="button" onClick={() => setZoomLevel(zoom - ZOOM_STEP)} disabled={zoom === 1} aria-label={`${copy.zoomOut}: ${label}`} title={copy.zoomOut}>−</button>
          <output aria-label={`${label}: ${copy.zoomLevel}`}>{Math.round(zoom * 100)}%</output>
          <button type="button" onClick={() => setZoomLevel(zoom + ZOOM_STEP)} disabled={zoom === MAX_ZOOM} aria-label={`${copy.zoomIn}: ${label}`} title={copy.zoomIn}>+</button>
          <button type="button" onClick={() => setZoomLevel(1)} disabled={zoom === 1} aria-label={`${copy.resetZoom}: ${label}`} title={copy.resetZoom}>↺</button>
        </div>
      </header>
      <div
        ref={viewport}
        className={`zoom-viewport ${zoom > 1 ? 'is-zoomed' : ''}`}
        role="button"
        tabIndex={0}
        aria-label={`${label}: ${copy.zoomLevel} ${Math.round(zoom * 100)}%`}
        aria-pressed={zoom > 1}
        style={{ touchAction: zoom > 1 ? 'none' : 'pan-y' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onClick={onClick}
        onKeyDown={onKeyDown}
      >
        <div className="zoom-media" style={{ transform: `translate3d(${position.x}px, ${position.y}px, 0) scale(${zoom})` }}>
          <img src={src} alt={label} draggable={false} />
        </div>
      </div>
    </section>
  )
}

function FileDrop({ files, accept, onAdd, copy }: { files: File[]; accept: string; onAdd: (files: File[]) => void; copy: ReturnType<typeof getCopy> }) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const choose = () => input.current?.click()
  return (
    <div
      className={`dropzone ${dragging ? 'is-dragging' : ''}`}
      role="button"
      tabIndex={0}
      onClick={choose}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') choose() }}
      onDragOver={(event) => { event.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        onAdd(Array.from(event.dataTransfer.files))
      }}
      aria-label={copy.drop}
    >
      <input
        ref={input}
        type="file"
        hidden
        multiple
        accept={accept}
        onChange={(event) => {
          onAdd(Array.from(event.target.files || []))
          event.target.value = ''
        }}
      />
      <span className="drop-icon"><UploadIcon /></span>
      <strong>{copy.drop}</strong>
      <span>{copy.browse}</span>
      <small>{copy.limits}</small>
      {files.length > 0 && <span className="file-count">{files.length}</span>}
    </div>
  )
}

function FileList({ files, setFiles, copy }: { files: File[]; setFiles: (files: File[]) => void; copy: ReturnType<typeof getCopy> }) {
  if (!files.length) return null
  const move = (index: number, delta: number) => {
    const next = [...files]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    setFiles(next)
  }
  return (
    <div className="file-list">
      <div className="file-list-head"><span>{files.length} files</span><button className="text-button" onClick={() => setFiles([])}>{copy.clear}</button></div>
      {files.map((file, index) => (
        <div className="file-row" key={`${file.name}-${file.size}-${file.lastModified}-${index}`}>
          <span className="file-index">{index + 1}</span>
          <span className="file-info"><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span>
          <span className="file-actions">
            <button disabled={index === 0} onClick={() => move(index, -1)} title={copy.moveUp} aria-label={copy.moveUp}>↑</button>
            <button disabled={index === files.length - 1} onClick={() => move(index, 1)} title={copy.moveDown} aria-label={copy.moveDown}>↓</button>
            <button className="remove-file" onClick={() => setFiles(files.filter((_, fileIndex) => fileIndex !== index))} title={copy.removeFile} aria-label={copy.removeFile}>×</button>
          </span>
        </div>
      ))}
    </div>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function App() {
  const initial = routeState()
  const [tab, setTab] = useState<Tab>(initial.tab)
  const [mode, setMode] = useState<ConvertMode>(initial.mode)
  const [pdfAction, setPdfAction] = useState<PdfAction>('pdf-merge')
  const [files, setFiles] = useState<File[]>([])
  const [language, setLanguage] = useState<Language>(() => (localStorage.getItem('gpthub-language') as Language) || 'ru')
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem('gpthub-theme') as Theme | null
    return saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  })
  const [format, setFormat] = useState('webp')
  const [quality, setQuality] = useState(100)
  const [scale, setScale] = useState(2)
  const [maxWidth, setMaxWidth] = useState('')
  const [maxHeight, setMaxHeight] = useState('')
  const [ranges, setRanges] = useState('1-3')
  const [splitMode, setSplitMode] = useState('ranges')
  const [pageSize, setPageSize] = useState('a4')
  const [orientation, setOrientation] = useState('auto')
  const [margin, setMargin] = useState(10)
  const [dpi, setDpi] = useState(150)
  const [trackedJobs, setTrackedJobs] = useState<TrackedJobs>(readTrackedJobs)
  const [error, setError] = useState<FormError | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submittingTab, setSubmittingTab] = useState<Tab | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [clock, setClock] = useState(Date.now())
  const submittingRef = useRef(false)
  const trackedJobsRef = useRef<TrackedJobs>(trackedJobs)
  const tabRef = useRef<Tab>(tab)
  const previewFetchesRef = useRef(new Set<string>())
  const initialTitleRef = useRef(document.title)
  const initialFaviconRef = useRef<string | null>(null)
  const notifiedJobIdsRef = useRef(new Set<string>())
  const copy = getCopy(language)
  const visibleTrackedJob = trackedJobs[tab]
  const visibleJob = visibleTrackedJob?.job || null
  const visibleBusy = Boolean(visibleTrackedJob && isRunning(visibleJob))
  const busy = submitting || visibleBusy
  const isSubmittingHere = submitting && submittingTab === tab
  const hasRunningJobs = submitting || Object.values(trackedJobs).some((entry) => entry && isRunning(entry.job))
  const completedJobs = Object.values(trackedJobs).filter((entry): entry is TrackedJob => Boolean(entry?.job?.status === 'succeeded'))
  const unreadReadyJobs = Object.values(trackedJobs).filter((entry): entry is TrackedJob => Boolean(entry?.job?.status === 'succeeded' && !entry.seen))
  const readyCountByTab = (target: Tab) => unreadReadyJobs.filter((entry) => entry.tab === target).length
  const pollableJobsKey = Object.values(trackedJobs)
    .filter((entry): entry is TrackedJob => Boolean(entry && (isRunning(entry.job) || (entry.tab === tab && entry.job?.status === 'succeeded' && entry.job.resultType?.startsWith('image/') && entry.fileCount === 1 && !entry.resultUrl))))
    .map((entry) => `${entry.capability.jobId}:${entry.job?.status || 'pending'}`)
    .sort()
    .join('|')

  const originalUrl = useMemo(() => {
    if (visibleTrackedJob?.sourceFiles.length !== 1 || !visibleTrackedJob.sourceFiles[0].type.startsWith('image/')) return null
    return URL.createObjectURL(visibleTrackedJob.sourceFiles[0])
  }, [visibleTrackedJob?.sourceFiles])

  useEffect(() => () => { if (originalUrl) URL.revokeObjectURL(originalUrl) }, [originalUrl])
  useEffect(() => { trackedJobsRef.current = trackedJobs }, [trackedJobs])
  useEffect(() => { tabRef.current = tab }, [tab])
  useEffect(() => {
    const persisted = Object.fromEntries(Object.entries(trackedJobs).map(([jobTab, entry]) => [jobTab, {
      ...entry,
      sourceFiles: [],
      resultUrl: null,
      previewLoading: false,
    }]))
    localStorage.setItem(TRACKED_JOBS_STORAGE_KEY, JSON.stringify(persisted))
  }, [trackedJobs])
  useEffect(() => () => {
    Object.values(trackedJobsRef.current).forEach((entry) => {
      if (entry?.resultUrl) URL.revokeObjectURL(entry.resultUrl)
    })
  }, [])
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('gpthub-theme', theme) }, [theme])
  useEffect(() => { document.documentElement.lang = language; localStorage.setItem('gpthub-language', language) }, [language])
  useEffect(() => {
    initialFaviconRef.current = document.querySelector<HTMLLinkElement>('link[rel~="icon"]')?.getAttribute('href') || '/favicon.svg'
    return () => {
      document.title = initialTitleRef.current
      setFavicon(initialFaviconRef.current || '/favicon.svg')
    }
  }, [])
  useEffect(() => {
    const isReady = completedJobs.length > 0
    document.title = isReady ? `${copy.tabReady} — GPTHub Tools` : initialTitleRef.current
    setFavicon(isReady ? READY_FAVICON : initialFaviconRef.current || '/favicon.svg')

    if (
      isReady
      && document.visibilityState === 'hidden'
      && typeof Notification !== 'undefined'
      && Notification.permission === 'granted'
    ) {
      const newReadyJobs = unreadReadyJobs.filter((entry) => !notifiedJobIdsRef.current.has(entry.capability.jobId))
      if (newReadyJobs.length) {
        newReadyJobs.forEach((entry) => notifiedJobIdsRef.current.add(entry.capability.jobId))
        new Notification('GPTHub Tools', { body: copy.notificationReady })
      }
    }
  }, [completedJobs, copy.notificationReady, copy.tabReady, unreadReadyJobs])
  useEffect(() => {
    if (!hasRunningJobs) return
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [hasRunningJobs])

  useEffect(() => {
    if (!pollableJobsKey) return
    let stopped = false
    const poll = async () => {
      const entries = Object.entries(trackedJobsRef.current) as [Tab, TrackedJob][]
      await Promise.all(entries.map(async ([jobTab, entry]) => {
        const shouldPollStatus = isRunning(entry.job)
        const shouldLoadPreview = entry.job?.status === 'succeeded'
          && jobTab === tabRef.current
          && entry.job.resultType?.startsWith('image/')
          && entry.fileCount === 1
          && !entry.resultUrl
          && !previewFetchesRef.current.has(entry.capability.jobId)
        if (!shouldPollStatus && !shouldLoadPreview) return
        try {
          const current = shouldPollStatus ? await getJob(entry.capability) : entry.job!
          if (stopped) return
          const isCurrentTab = jobTab === tabRef.current
          if (current.status === 'succeeded' && shouldLoadPreview) {
            previewFetchesRef.current.add(entry.capability.jobId)
            setTrackedJobs((jobs) => {
              const latest = jobs[jobTab]
              if (!latest || latest.capability.jobId !== entry.capability.jobId) return jobs
              return { ...jobs, [jobTab]: { ...latest, job: current, error: null, previewLoading: true, seen: latest.seen || isCurrentTab } }
            })
            try {
              const blob = await fetchResult(entry.capability)
              if (stopped) return
              const resultUrl = URL.createObjectURL(blob)
              setTrackedJobs((jobs) => {
                const latest = jobs[jobTab]
                if (!latest || latest.capability.jobId !== entry.capability.jobId) {
                  URL.revokeObjectURL(resultUrl)
                  return jobs
                }
                return { ...jobs, [jobTab]: { ...latest, job: current, resultUrl, previewLoading: false, seen: latest.seen || isCurrentTab } }
              })
            } catch {
              setTrackedJobs((jobs) => {
                const latest = jobs[jobTab]
                return !latest || latest.capability.jobId !== entry.capability.jobId ? jobs : { ...jobs, [jobTab]: { ...latest, previewLoading: false } }
              })
            } finally {
              previewFetchesRef.current.delete(entry.capability.jobId)
            }
            return
          }
          setTrackedJobs((jobs) => {
            const latest = jobs[jobTab]
            if (!latest || latest.capability.jobId !== entry.capability.jobId) return jobs
            return { ...jobs, [jobTab]: { ...latest, job: current, error: null, seen: latest.seen || (current.status === 'succeeded' && isCurrentTab) } }
          })
        } catch (caught) {
          const problem = caught as Error & { code?: string }
          setTrackedJobs((jobs) => {
            const latest = jobs[jobTab]
            return !latest || latest.capability.jobId !== entry.capability.jobId ? jobs : { ...jobs, [jobTab]: { ...latest, error: { message: problem.message, code: problem.code } } }
          })
        }
      }))
    }
    void poll()
    const timer = window.setInterval(() => { void poll() }, 1200)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [pollableJobsKey])

  useEffect(() => {
    setTrackedJobs((jobs) => {
      const entry = jobs[tab]
      if (!entry || entry.job?.status !== 'succeeded' || entry.seen) return jobs
      return { ...jobs, [tab]: { ...entry, seen: true } }
    })
  }, [tab])

  const navigateTab = (next: Tab) => {
    setTab(next)
    setMode('images')
    window.history.pushState({}, '', routeForTab[next])
    resetForm()
  }

  const navigateMode = (next: ConvertMode) => {
    setMode(next)
    window.history.pushState({}, '', `/convert/${next}`)
    resetForm()
  }

  const resetForm = () => {
    setFiles([])
    setError(null)
  }

  const addFiles = (incoming: File[]) => {
    setFiles((current) => [...current, ...incoming].slice(0, 20))
    setError(null)
  }

  let operation: Operation = 'upscale'
  if (tab === 'remove') operation = 'remove-background'
  if (tab === 'convert' && mode === 'images') operation = 'image-convert'
  if (tab === 'convert' && mode === 'documents') operation = 'document-convert'
  if (tab === 'convert' && mode === 'pdf') operation = pdfAction
  const showsImageFormat = ['upscale', 'remove-background', 'image-convert', 'pdf-to-images'].includes(operation)
  const showsQuality = showsImageFormat && (format === 'jpeg' || format === 'webp')
  const qualityLoss = 100 - quality
  const estimatedSavingMin = Math.min(70, Math.round((qualityLoss * 1.25) / 5) * 5)
  const estimatedSavingMax = Math.min(85, estimatedSavingMin + 15 + Math.round((qualityLoss * .25) / 5) * 5)
  const qualityHint = quality === 100
    ? copy.qualityHintMax
    : copy.qualityHintEstimate.replace('{min}', String(estimatedSavingMin)).replace('{max}', String(estimatedSavingMax))
  const formatHint = format === 'png' ? copy.formatPngHint : format === 'webp' ? copy.formatWebpHint : copy.formatJpegHint
  const dpiHint = dpi === 150 ? copy.dpi150Hint : copy.dpi300Hint

  const accept = useMemo(() => {
    if (tab !== 'convert' || mode === 'images' || (mode === 'pdf' && pdfAction === 'images-to-pdf')) return '.png,.jpg,.jpeg,.webp,.heic,.heif,.tif,.tiff,.bmp'
    if (mode === 'documents') return '.doc,.docx,.odt,.rtf,.pdf'
    return '.pdf'
  }, [tab, mode, pdfAction])

  const submit = async () => {
    if (submittingRef.current || busy) return
    if (!files.length) return setError({ message: copy.noFiles })
    if (operation === 'pdf-merge' && files.length < 2) return setError({ message: copy.needTwoPdfs })
    const targetTab = tab
    const targetOperation = operation
    const sourceFiles = [...files]
    const targetScale = scale
    submittingRef.current = true
    setSubmitting(true)
    setSubmittingTab(targetTab)
    setError(null)
    const options: Record<string, unknown> = {}
    if (targetOperation === 'upscale') Object.assign(options, { scale: targetScale, format, quality })
    if (targetOperation === 'remove-background') Object.assign(options, { format, quality })
    if (targetOperation === 'image-convert') Object.assign(options, { format, quality, maxWidth: Number(maxWidth) || 0, maxHeight: Number(maxHeight) || 0 })
    if (targetOperation === 'pdf-split') Object.assign(options, { mode: splitMode, ranges })
    if (targetOperation === 'images-to-pdf') Object.assign(options, { pageSize, orientation, margin })
    if (targetOperation === 'pdf-to-images') Object.assign(options, { format, quality, dpi })
    try {
      let pixels = 0
      if (targetOperation === 'upscale') {
        for (const file of sourceFiles) {
          let filePixels = 0
          try {
            filePixels = await imagePixelCount(file)
          } catch {
            continue
          }
          pixels += filePixels
          if (filePixels * targetScale * targetScale > MAX_UPSCALE_OUTPUT_PIXELS) {
            const problem = new Error('The image is too large for the selected upscale factor') as Error & { code?: string }
            problem.code = 'IMAGE_TOO_LARGE'
            throw problem
          }
        }
      }
      const started = Date.now()
      setClock(started)
      const created = await createJob(targetOperation, sourceFiles, options)
      setTrackedJobs((jobs) => {
        const replaced = jobs[targetTab]
        if (replaced?.resultUrl) URL.revokeObjectURL(replaced.resultUrl)
        return {
          ...jobs,
          [targetTab]: {
            capability: created,
            tab: targetTab,
            operation: targetOperation,
            sourceFiles,
            fileCount: sourceFiles.length,
            scale: targetScale,
            submittedAt: started,
            inputPixels: pixels,
            job: null,
            error: null,
            resultUrl: null,
            previewLoading: false,
            seen: false,
          },
        }
      })
    } catch (caught) {
      const problem = caught as Error & { code?: string }
      setError({ message: problem.message, code: problem.code })
    } finally {
      submittingRef.current = false
      setSubmitting(false)
      setSubmittingTab(null)
    }
  }

  const download = async () => {
    if (!visibleTrackedJob || !visibleJob) return
    const blob = await fetchResult(visibleTrackedJob.capability)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = visibleJob.resultName || 'result'
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const cancel = async () => {
    if (!visibleTrackedJob || cancelling) return
    const targetTab = tab
    const targetJobId = visibleTrackedJob.capability.jobId
    setCancelling(true)
    setError(null)
    try {
      await cancelJob(visibleTrackedJob.capability)
      setTrackedJobs((jobs) => {
        const removed = jobs[targetTab]
        if (!removed || removed.capability.jobId !== targetJobId) return jobs
        if (removed.resultUrl) URL.revokeObjectURL(removed.resultUrl)
        const { [targetTab]: _, ...remaining } = jobs
        return remaining
      })
      resetForm()
    } catch (caught) {
      const problem = caught as Error & { code?: string }
      setError({ message: problem.message || copy.cancelFailed, code: problem.code })
    } finally {
      setCancelling(false)
    }
  }

  const title = tab === 'upscale' ? copy.upscaleTitle : tab === 'remove' ? copy.removeTitle : copy.convertTitle
  const lead = tab === 'upscale' ? copy.upscaleLead : tab === 'remove' ? copy.removeLead : copy.convertLead
  const visibleError = visibleTrackedJob?.error || error
  const localizedError = visibleError?.code ? copy.errors[visibleError.code] || visibleError.message : visibleError?.message
  const startedAt = visibleJob?.createdAt ? Date.parse(visibleJob.createdAt) : visibleTrackedJob?.submittedAt
  const elapsedSeconds = startedAt ? Math.max(0, Math.floor((clock - startedAt) / 1000)) : 0
  const estimatedSeconds = estimateDuration(visibleTrackedJob?.operation || operation, visibleTrackedJob?.scale || scale, visibleTrackedJob?.fileCount || files.length, visibleTrackedJob?.inputPixels || 0)
  const progressPercent = visibleJob?.status === 'succeeded'
    ? 100
    : isSubmittingHere
      ? 4
      : visibleJob?.status === 'queued' || !visibleJob
        ? 8
        : Math.min(94, Math.round(12 + (elapsedSeconds / estimatedSeconds) * 82))
  const remainingSeconds = Math.max(0, estimatedSeconds - elapsedSeconds)
  const progressMeta = visibleJob?.status === 'running'
    ? remainingSeconds > 0
      ? `~${progressPercent}% · ${copy.remaining} ${formatDuration(remainingSeconds, language)} · ${copy.elapsed} ${formatDuration(elapsedSeconds, language)}`
      : `~${progressPercent}% · ${copy.almostDone} · ${copy.elapsed} ${formatDuration(elapsedSeconds, language)}`
    : visibleJob?.status === 'queued'
      ? `${copy.waitingToStart} · ${copy.elapsed} ${formatDuration(elapsedSeconds, language)}`
      : isSubmittingHere
        ? copy.uploading
        : ''

  return (
    <div className="app-shell">
      <div className="ambient ambient-one" /><div className="ambient ambient-two" />
      <header className="site-header">
        <a className="brand" href="/upscale" onClick={(event) => { event.preventDefault(); navigateTab('upscale') }}>
          <span className="brand-mark"><SparkIcon /></span>
          <span><strong>GPTHub Tools</strong><small>{copy.brandTag}</small></span>
        </a>
        <div className="header-actions">
          <button className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} title={copy.theme} aria-label={copy.theme}><SunIcon /></button>
          <button className="language-button" onClick={() => setLanguage(language === 'ru' ? 'en' : 'ru')}>{copy.language}</button>
        </div>
      </header>

      <main>
        <nav className="tabs" aria-label="Tools">
          {(['upscale', 'remove', 'convert'] as Tab[]).map((item) => (
            (() => {
              const label = item === 'upscale' ? copy.upscale : item === 'remove' ? copy.remove : copy.convert
              const readyCount = readyCountByTab(item)
              return <button
                key={item}
                className={tab === item ? 'active' : ''}
                aria-label={readyCount ? `${label}: ${copy.tabReadyBadge.replace('{count}', String(readyCount))}` : label}
                onClick={() => navigateTab(item)}
              >
                <span className="tab-icon">{item === 'upscale' ? '↗' : item === 'remove' ? '◐' : '⇄'}</span>
                <span className="tab-label">{label}</span>
                {readyCount > 0 && <span className="tab-ready-badge" aria-hidden="true">✓ {readyCount}</span>}
              </button>
            })()
          ))}
        </nav>

        <section className="hero-copy">
          <span className="eyebrow">GPTHUB / FILE LAB</span>
          <h1>{title}</h1>
          <p>{lead}</p>
        </section>

        {tab === 'convert' && (
          <div className="mode-switcher">
            {(['images', 'documents', 'pdf'] as ConvertMode[]).map((item) => (
              <button key={item} className={mode === item ? 'active' : ''} onClick={() => navigateMode(item)}>
                <strong>{item === 'images' ? copy.images : item === 'documents' ? copy.documents : copy.pdf}</strong>
                <small>{item === 'images' ? copy.imageHelp : item === 'documents' ? copy.documentHelp : copy.pdfHelp}</small>
              </button>
            ))}
          </div>
        )}

        {tab === 'convert' && mode === 'pdf' && (
          <div className="pdf-actions">
            {([
              ['pdf-merge', copy.merge], ['pdf-split', copy.split], ['images-to-pdf', copy.imagesToPdf], ['pdf-to-images', copy.pdfToImages],
            ] as [PdfAction, string][]).map(([value, label]) => (
              <button key={value} className={pdfAction === value ? 'active' : ''} onClick={() => { setPdfAction(value); resetForm() }}>{label}</button>
            ))}
          </div>
        )}

        <section className="workspace-card">
          <div className="workspace-grid">
            <div className="upload-column">
              <FileDrop files={files} accept={accept} onAdd={addFiles} copy={copy} />
              <FileList files={files} setFiles={setFiles} copy={copy} />
            </div>
            <div className="settings-column">
              <div className="settings-head"><span>02</span><strong>{language === 'ru' ? 'Настройки' : 'Settings'}</strong></div>
              {tab === 'upscale' && <div className="field"><label><span className="label-with-hint">{copy.scale}<Hint text={copy.scaleHint} below /></span></label><div className="segmented"><button className={scale === 2 ? 'active' : ''} onClick={() => setScale(2)}>2×</button><button className={scale === 4 ? 'active' : ''} onClick={() => setScale(4)}>4×</button></div></div>}
              {showsImageFormat && (
                <div className="field"><label><span className="label-with-hint">{copy.format}<Hint text={formatHint} below /></span></label><select value={format} onChange={(event) => setFormat(event.target.value)}>
                  {tab !== 'remove' && <option value="jpeg">JPG</option>}<option value="png">PNG</option><option value="webp">WebP</option>
                </select></div>
              )}
              {showsQuality && (
                <div className="field"><label><span className="label-with-hint">{copy.quality}<Hint text={qualityHint} /></span><output>{quality}%</output></label><input type="range" min="40" max="100" value={quality} aria-label={`${copy.quality}: ${quality}%`} title={qualityHint} onChange={(event) => setQuality(Number(event.target.value))} /></div>
              )}
              {tab === 'convert' && mode === 'images' && <div className="dimension-grid"><div className="field"><label><span className="label-with-hint">{copy.maxWidth}<Hint text={copy.dimensionsHint} /></span></label><input inputMode="numeric" value={maxWidth} onChange={(event) => setMaxWidth(event.target.value.replace(/\D/g, ''))} placeholder={copy.optional} /></div><div className="field"><label>{copy.maxHeight}</label><input inputMode="numeric" value={maxHeight} onChange={(event) => setMaxHeight(event.target.value.replace(/\D/g, ''))} placeholder={copy.optional} /></div></div>}
              {operation === 'pdf-split' && <><div className="field"><label>{copy.splitMode}</label><select value={splitMode} onChange={(event) => setSplitMode(event.target.value)}><option value="ranges">{copy.byRanges}</option><option value="each">{copy.eachPage}</option></select></div>{splitMode === 'ranges' && <div className="field"><label>{copy.ranges}</label><input value={ranges} onChange={(event) => setRanges(event.target.value)} placeholder={copy.rangesHint} /></div>}</>}
              {operation === 'images-to-pdf' && <><div className="field"><label>{copy.pageSize}</label><select value={pageSize} onChange={(event) => setPageSize(event.target.value)}><option value="a4">{copy.a4}</option><option value="original">{copy.original}</option></select></div><div className="field"><label>{copy.orientation}</label><select value={orientation} onChange={(event) => setOrientation(event.target.value)}><option value="auto">{copy.auto}</option><option value="portrait">{copy.portrait}</option><option value="landscape">{copy.landscape}</option></select></div><div className="field"><label>{copy.margin}<output>{margin}</output></label><input type="range" min="0" max="30" value={margin} onChange={(event) => setMargin(Number(event.target.value))} /></div></>}
              {operation === 'pdf-to-images' && <div className="field"><label><span className="label-with-hint">{copy.dpi}<Hint text={dpiHint} /></span></label><div className="segmented"><button className={dpi === 150 ? 'active' : ''} onClick={() => setDpi(150)}>150 DPI</button><button className={dpi === 300 ? 'active' : ''} onClick={() => setDpi(300)}>300 DPI</button></div></div>}
              {mode === 'documents' && <div className="notice"><span>i</span><p>{copy.documentHelp}<br /><small>{language === 'ru' ? 'Сканированные PDF без текстового слоя не конвертируются.' : 'Scanned PDFs without a text layer cannot be converted.'}</small></p></div>}
              <button className="primary-button" onClick={() => void submit()} disabled={busy || files.length === 0} aria-busy={busy}><SparkIcon />{busy ? copy.processing : copy.process}</button>
            </div>
          </div>

          {(isSubmittingHere || visibleBusy || visibleJob || localizedError) && <div className={`job-panel ${visibleJob?.status === 'succeeded' ? 'success' : localizedError ? 'failure' : ''}`}>
            {localizedError ? <><span className="status-icon">!</span><div><strong>{copy.failed}</strong><p>{localizedError}</p></div><div className="job-actions">{visibleTrackedJob && isRunning(visibleJob) ? <button className="cancel-button" onClick={() => void cancel()} disabled={cancelling}>{cancelling ? copy.cancelling : copy.cancel}</button> : null}</div></> : <><span className="status-icon">{visibleJob?.status === 'succeeded' ? '✓' : '···'}</span><div className="job-copy"><strong>{visibleJob?.status === 'succeeded' ? copy.ready : cancelling ? copy.cancelling : isSubmittingHere ? copy.uploading : visibleJob?.status === 'running' ? copy.running : copy.queued}</strong><div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progressPercent}><span style={{ width: `${progressPercent}%` }} /></div>{progressMeta && <small className="progress-meta">{progressMeta}</small>}{visibleJob?.resultName && <small>{visibleJob.resultName}</small>}</div><div className="job-actions">{visibleJob?.status === 'succeeded' ? <button className="download-button is-ready" onClick={() => void download()}>{copy.download} ↓</button> : visibleTrackedJob ? <button className="cancel-button" onClick={() => void cancel()} disabled={cancelling}>{cancelling ? copy.cancelling : copy.cancel}</button> : null}</div></>}
          </div>}

          {tab === 'upscale' && visibleTrackedJob?.resultUrl && <div className="result-preview">
            <div className="comparison-head"><strong>{copy.resultPreview}</strong><span>{copy.zoomHint}</span></div>
            <ZoomPane label={copy.resultPreview} src={visibleTrackedJob.resultUrl} checkerboard={false} copy={copy} />
          </div>}

          {tab !== 'upscale' && originalUrl && visibleTrackedJob?.resultUrl && <div className="comparison">
            <div className="comparison-head"><strong>{copy.compare}</strong><span>{copy.zoomHint}</span></div>
            <div className="compare-grid">
              <ZoomPane label={copy.before} src={originalUrl} checkerboard={false} copy={copy} />
              <ZoomPane label={copy.after} src={visibleTrackedJob.resultUrl} checkerboard={tab === 'remove'} copy={copy} />
            </div>
          </div>}

          {tab !== 'upscale' && !originalUrl && visibleTrackedJob?.resultUrl && <div className="result-preview">
            <div className="comparison-head"><strong>{copy.resultPreview}</strong><span>{copy.zoomHint}</span></div>
            <ZoomPane label={copy.resultPreview} src={visibleTrackedJob.resultUrl} checkerboard={tab === 'remove'} copy={copy} />
          </div>}
        </section>

        <div className="privacy-note"><span>⌁</span><p>{copy.privacy}</p></div>
      </main>

      <footer><span>© {new Date().getFullYear()} GPTHub Tools</span><div><a href="mailto:support@gpthub.ru?subject=GPTHub%20Tools">{copy.support}</a><a href="https://github.com/nitrodzen/gpthub-tools" target="_blank" rel="noreferrer">{copy.github} ↗</a></div></footer>
    </div>
  )
}
