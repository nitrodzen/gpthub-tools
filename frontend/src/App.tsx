import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { cancelJob, createJob, fetchResult, getJob, type Job, type JobCapability, type Operation } from './api'
import { getCopy, type Language } from './i18n'

type Tab = 'upscale' | 'remove' | 'convert'
type ConvertMode = 'images' | 'documents' | 'pdf'
type PdfAction = 'pdf-merge' | 'pdf-split' | 'images-to-pdf' | 'pdf-to-images'
type Theme = 'light' | 'dark'

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
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return
    const distanceX = event.clientX - drag.current.startX
    const distanceY = event.clientY - drag.current.startY
    if (Math.abs(distanceX) > 3 || Math.abs(distanceY) > 3) drag.current.moved = true
    setView((current) => ({
      ...current,
      position: clampPosition({
        x: drag.current!.originX + distanceX,
        y: drag.current!.originY + distanceY,
      }, current.zoom),
    }))
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    suppressClick.current = Boolean(drag.current?.moved)
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
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
        <img src={src} alt={label} draggable={false} style={{ transform: `translate3d(${position.x}px, ${position.y}px, 0) scale(${zoom})` }} />
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
  const [capability, setCapability] = useState<JobCapability | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<{ message: string; code?: string } | null>(null)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const copy = getCopy(language)

  const originalUrl = useMemo(() => {
    if (files.length !== 1 || !files[0].type.startsWith('image/')) return null
    return URL.createObjectURL(files[0])
  }, [files])

  useEffect(() => () => { if (originalUrl) URL.revokeObjectURL(originalUrl) }, [originalUrl])
  useEffect(() => () => { if (resultUrl) URL.revokeObjectURL(resultUrl) }, [resultUrl])
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('gpthub-theme', theme) }, [theme])
  useEffect(() => { document.documentElement.lang = language; localStorage.setItem('gpthub-language', language) }, [language])

  useEffect(() => {
    if (!capability) return
    let stopped = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const current = await getJob(capability)
        if (stopped) return
        setJob(current)
        if (current.status === 'succeeded') {
          if (current.resultType?.startsWith('image/') && files.length === 1) {
            const blob = await fetchResult(capability)
            if (!stopped) setResultUrl(URL.createObjectURL(blob))
          }
          return
        }
        if (current.status === 'failed' || current.status === 'cancelled') return
        timer = window.setTimeout(poll, 1200)
      } catch (caught) {
        const problem = caught as Error & { code?: string }
        setError({ message: problem.message, code: problem.code })
      }
    }
    void poll()
    return () => { stopped = true; if (timer) window.clearTimeout(timer) }
  }, [capability, files.length])

  const navigateTab = (next: Tab) => {
    setTab(next)
    setMode('images')
    window.history.pushState({}, '', routeForTab[next])
    resetJob()
  }

  const navigateMode = (next: ConvertMode) => {
    setMode(next)
    window.history.pushState({}, '', `/convert/${next}`)
    resetJob()
  }

  const resetJob = () => {
    setFiles([])
    setCapability(null)
    setJob(null)
    setError(null)
    if (resultUrl) URL.revokeObjectURL(resultUrl)
    setResultUrl(null)
  }

  const addFiles = (incoming: File[]) => {
    setFiles((current) => [...current, ...incoming].slice(0, 20))
    setError(null)
    setJob(null)
    setCapability(null)
    if (resultUrl) URL.revokeObjectURL(resultUrl)
    setResultUrl(null)
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
    if (!files.length) return setError({ message: copy.noFiles })
    if (operation === 'pdf-merge' && files.length < 2) return setError({ message: copy.needTwoPdfs })
    setError(null)
    setJob(null)
    const options: Record<string, unknown> = {}
    if (operation === 'upscale') Object.assign(options, { scale, format, quality })
    if (operation === 'remove-background') Object.assign(options, { format, quality })
    if (operation === 'image-convert') Object.assign(options, { format, quality, maxWidth: Number(maxWidth) || 0, maxHeight: Number(maxHeight) || 0 })
    if (operation === 'pdf-split') Object.assign(options, { mode: splitMode, ranges })
    if (operation === 'images-to-pdf') Object.assign(options, { pageSize, orientation, margin })
    if (operation === 'pdf-to-images') Object.assign(options, { format, quality, dpi })
    try {
      setCapability(await createJob(operation, files, options))
    } catch (caught) {
      const problem = caught as Error & { code?: string }
      setError({ message: problem.message, code: problem.code })
    }
  }

  const download = async () => {
    if (!capability || !job) return
    const blob = await fetchResult(capability)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = job.resultName || 'result'
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const cancel = async () => {
    if (capability) await cancelJob(capability)
    resetJob()
  }

  const title = tab === 'upscale' ? copy.upscaleTitle : tab === 'remove' ? copy.removeTitle : copy.convertTitle
  const lead = tab === 'upscale' ? copy.upscaleLead : tab === 'remove' ? copy.removeLead : copy.convertLead
  const localizedError = error?.code ? copy.errors[error.code] || error.message : error?.message
  const busy = Boolean(capability && (!job || job.status === 'queued' || job.status === 'running'))

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
            <button
              key={item}
              className={tab === item ? 'active' : ''}
              aria-label={item === 'upscale' ? copy.upscale : item === 'remove' ? copy.remove : copy.convert}
              onClick={() => navigateTab(item)}
            >
              <span>{item === 'upscale' ? '↗' : item === 'remove' ? '◐' : '⇄'}</span>
              {item === 'upscale' ? copy.upscale : item === 'remove' ? copy.remove : copy.convert}
            </button>
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
              <button key={value} className={pdfAction === value ? 'active' : ''} onClick={() => { setPdfAction(value); resetJob() }}>{label}</button>
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
              <button className="primary-button" onClick={() => void submit()} disabled={busy || files.length === 0}><SparkIcon />{busy ? copy.processing : copy.process}</button>
            </div>
          </div>

          {(busy || job || localizedError) && <div className={`job-panel ${job?.status === 'succeeded' ? 'success' : localizedError ? 'failure' : ''}`}>
            {localizedError ? <><span className="status-icon">!</span><div><strong>{copy.failed}</strong><p>{localizedError}</p></div></> : <><span className="status-icon">{job?.status === 'succeeded' ? '✓' : '···'}</span><div className="job-copy"><strong>{job?.status === 'succeeded' ? copy.ready : job?.status === 'running' ? copy.running : copy.queued}</strong><div className="progress"><span style={{ width: job?.status === 'succeeded' ? '100%' : job?.status === 'running' ? '68%' : '22%' }} /></div>{job?.resultName && <small>{job.resultName}</small>}</div><div className="job-actions">{job?.status === 'succeeded' ? <button className="download-button" onClick={() => void download()}>{copy.download} ↓</button> : <button className="text-button" onClick={() => void cancel()}>{copy.cancel}</button>}</div></>}
          </div>}

          {originalUrl && resultUrl && <div className="comparison">
            <div className="comparison-head"><strong>{copy.compare}</strong><span>{copy.zoomHint}</span></div>
            <div className="compare-grid">
              <ZoomPane label={copy.before} src={originalUrl} checkerboard={false} copy={copy} />
              <ZoomPane label={copy.after} src={resultUrl} checkerboard={tab === 'remove'} copy={copy} />
            </div>
          </div>}
        </section>

        <div className="privacy-note"><span>⌁</span><p>{copy.privacy}</p></div>
      </main>

      <footer><span>© {new Date().getFullYear()} GPTHub Tools</span><a href="https://github.com/nitrodzen/gpthub-tools" target="_blank" rel="noreferrer">{copy.github} ↗</a></footer>
    </div>
  )
}
