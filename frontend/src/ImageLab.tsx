import { useEffect, useRef, useState } from 'react'
import { cancelJob, createJob, fetchResult, getJob, type JobCapability } from './api'
import type { Language } from './i18n'

export const imageModels = [
  { id: 'standard', ru: 'Универсальный', en: 'General', ruHint: 'Текущий Real-ESRGAN · быстро', enHint: 'Original Real-ESRGAN · fast' },
  { id: 'photo', ru: 'Фото 2×', en: 'Photo 2×', ruHint: 'RealPLKSR · фото, шум и следы JPEG', enHint: 'RealPLKSR · photos, noise and JPEG artifacts' },
  { id: 'web-photo', ru: 'Фото из интернета', en: 'Web photos', ruHint: 'ATD · сжатые фото · медленнее', enHint: 'ATD · compressed photos · slower' },
  { id: 'detail', ru: 'Детали', en: 'Detail', ruHint: 'Real-HAT-GAN · выраженная резкость · медленнее', enHint: 'Real-HAT-GAN · pronounced sharpness · slower' },
  { id: 'illustration', ru: 'Иллюстрации и аниме', en: 'Illustrations and anime', ruHint: 'Real-ESRGAN Anime · быстро', enHint: 'Real-ESRGAN Anime · fast' },
]

export function ModelPicker({ model, onChange, language }: { model: string; onChange: (model: string) => void; language: Language }) {
  const selected = imageModels.find(item => item.id === model) || imageModels[0]
  return <div className="field"><label htmlFor="image-model">{language === 'ru' ? 'Режим обработки' : 'Processing mode'}</label>
    <select id="image-model" value={model} onChange={event => onChange(event.target.value)}>{imageModels.map(item => <option key={item.id} value={item.id}>{item[language]}</option>)}</select>
    <small className="field-help">{language === 'ru' ? selected.ruHint : selected.enHint}</small>
  </div>
}

export function ImageComparison({ before, after, language }: { before: string; after: string; language: Language }) {
  const [split, setSplit] = useState(50)
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)
  useEffect(() => { setZoom(1); setOffset({ x: 0, y: 0 }) }, [before, after])
  const transform = `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`
  return <section className="image-comparison" aria-label={language === 'ru' ? 'Сравнение до и после' : 'Before and after comparison'}>
    <div className="comparison-head"><strong>{language === 'ru' ? 'До / после' : 'Before / after'}</strong><label>{language === 'ru' ? 'Увеличение' : 'Zoom'} <select aria-label={language === 'ru' ? 'Увеличение сравнения' : 'Comparison zoom'} value={zoom} onChange={event => { setZoom(Number(event.target.value)); setOffset({ x: 0, y: 0 }) }}><option value={1}>1×</option><option value={2}>2×</option><option value={4}>4×</option></select></label></div>
    <div className="comparison-stage checkerboard" style={{ touchAction: zoom > 1 ? 'none' : 'pan-y' }}
      onPointerDown={event => { if (zoom <= 1) return; drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }; event.currentTarget.setPointerCapture?.(event.pointerId) }}
      onPointerMove={event => { const d = drag.current; if (!d) return; const r = event.currentTarget.getBoundingClientRect(); const mx = r.width * (zoom - 1) / 2; const my = r.height * (zoom - 1) / 2; setOffset({ x: Math.max(-mx, Math.min(mx, d.ox + event.clientX - d.x)), y: Math.max(-my, Math.min(my, d.oy + event.clientY - d.y)) }) }}
      onPointerUp={() => { drag.current = null }} onPointerCancel={() => { drag.current = null }}>
      <img src={before} alt={language === 'ru' ? 'До' : 'Before'} style={{ transform }} draggable={false} />
      <div className="comparison-overlay" style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}><img src={after} alt={language === 'ru' ? 'После' : 'After'} style={{ transform }} draggable={false} /></div>
      <span className="comparison-divider" style={{ left: `${split}%` }} />
      <span className="comparison-caption">{language === 'ru' ? 'После' : 'After'}</span><span className="comparison-caption right">{language === 'ru' ? 'До' : 'Before'}</span>
    </div>
    <input type="range" min={0} max={100} value={split} onChange={event => setSplit(Number(event.target.value))} aria-label={language === 'ru' ? 'Граница до и после' : 'Before and after boundary'} />
    <small className="field-help">{language === 'ru' ? 'Двигайте границу. При увеличении можно перетаскивать изображение.' : 'Move the boundary. Drag the image when zoomed in.'}</small>
  </section>
}

export function ModelExplorer({ file, model, scale, strength, language }: { file?: File; model: string; scale: number; strength: number; language: Language }) {
  const [sourceUrl, setSourceUrl] = useState('')
  const [crop, setCrop] = useState({ x: .5, y: .5 })
  const [cache, setCache] = useState<Record<string, { url: string; before: string; seconds: number }>>({})
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const urls = useRef<string[]>([])
  const active = useRef<JobCapability | null>(null)
  const runningRef = useRef(false)
  const key = `${model}:${scale}:${strength}`
  const selected = cache[key]
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file); setSourceUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [file])
  useEffect(() => {
    generation.current++
    setCache({}); setError(''); setRunning(false); runningRef.current = false
    urls.current.forEach(URL.revokeObjectURL); urls.current = []
    if (active.current) void cancelJob(active.current).catch(() => undefined)
    active.current = null
    return () => { generation.current++; urls.current.forEach(URL.revokeObjectURL); if (active.current) void cancelJob(active.current).catch(() => undefined) }
  }, [file, crop.x, crop.y])
  const preview = async () => {
    if (!file || runningRef.current || cache[key]) return
    const current = generation.current
    runningRef.current = true; setRunning(true); setError('')
    let capability: JobCapability | null = null
    try {
      const bitmap = await createImageBitmap(file)
      const width = Math.min(160, bitmap.width), height = Math.min(160, bitmap.height)
      const x = Math.max(0, Math.min(bitmap.width - width, Math.round(crop.x * bitmap.width - width / 2)))
      const y = Math.max(0, Math.min(bitmap.height - height, Math.round(crop.y * bitmap.height - height / 2)))
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height
      canvas.getContext('2d')!.drawImage(bitmap, x, y, width, height, 0, 0, width, height); bitmap.close()
      const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('Preview unavailable')), 'image/png'))
      const started = Date.now()
      capability = await createJob('upscale-preview', [new File([blob], 'preview.png', { type: 'image/png' })], { model, scale, strength, format: 'png' })
      if (current !== generation.current) { await cancelJob(capability); return }
      active.current = capability
      while (current === generation.current && Date.now() - started < 180000) {
        const job = await getJob(capability)
        if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error?.code || 'Preview failed')
        if (job.status === 'succeeded') {
          const result = await fetchResult(capability)
          if (current !== generation.current) return
          const url = URL.createObjectURL(result), before = URL.createObjectURL(blob)
          urls.current.push(url, before)
          setCache(previous => ({ ...previous, [key]: { url, before, seconds: Math.round((Date.now() - started) / 1000) } }))
          return
        }
        await new Promise(resolve => window.setTimeout(resolve, 900))
      }
      if (current === generation.current) throw new Error('TIMEOUT')
    } catch {
      if (current === generation.current) setError(language === 'ru' ? 'Не удалось получить пробу. Попробуйте ещё раз; для HEIC можно сначала использовать конвертер.' : 'Preview failed. Try again; HEIC may need conversion first.')
    } finally {
      if (capability) void cancelJob(capability).catch(() => undefined)
      if (current === generation.current) { active.current = null; setRunning(false); runningRef.current = false }
    }
  }
  if (!file || scale === 1) return null
  return <section className="model-explorer">
    <strong>{language === 'ru' ? 'Попробовать на фрагменте' : 'Try a small crop'}</strong>
    <p className="field-help">{language === 'ru' ? 'Нажмите на нужную область фото, выберите режим и сделайте пробу. Готовые варианты переключаются без повторной обработки.' : 'Click an area of the photo, choose a mode and preview it. Completed previews switch instantly.'}</p>
    {sourceUrl && <div className="crop-selector"><img src={sourceUrl} alt={language === 'ru' ? 'Выбрать фрагмент' : 'Choose a crop'} onClick={event => { if (running) return; const r = event.currentTarget.getBoundingClientRect(); setCrop({ x: (event.clientX - r.left) / r.width, y: (event.clientY - r.top) / r.height }) }} /><span style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%` }}>+</span></div>}
    <button className="preview-button" disabled={running || Boolean(selected)} onClick={() => void preview()}>{running ? (language === 'ru' ? 'Готовим пробу…' : 'Preparing preview…') : selected ? (language === 'ru' ? `Готово за ${selected.seconds} сек` : `Ready in ${selected.seconds} sec`) : (language === 'ru' ? 'Проба выбранного режима' : 'Preview selected mode')}</button>
    {error && <p role="alert">{error}</p>}
    {selected && <ImageComparison before={selected.before} after={selected.url} language={language} />}
  </section>
}
