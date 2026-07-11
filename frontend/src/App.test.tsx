import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App, { estimateDuration, formatDuration, ZoomPane } from './App'
import { getCopy } from './i18n'

const apiMocks = vi.hoisted(() => ({
  createJob: vi.fn(),
  getJob: vi.fn(),
  fetchResult: vi.fn(),
  cancelJob: vi.fn(),
}))

vi.mock('./api', () => apiMocks)

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    window.history.replaceState({}, '', '/upscale')
  })

  it('renders the three primary tools', () => {
    render(<App />)
    expect(screen.getByText('Увеличение изображений')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Удалить фон' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Конвертер' })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'Качество: 100%' })).toHaveValue('100')
    expect(screen.getByLabelText(/90% уменьшает файл примерно на 15–30%/)).toBeInTheDocument()
  })

  it('zooms image panes independently', () => {
    const copy = getCopy('ru')
    render(<><ZoomPane label={copy.before} src="before.png" checkerboard={false} copy={copy} /><ZoomPane label={copy.after} src="after.png" checkerboard={false} copy={copy} /></>)

    expect(screen.getByLabelText('До: Масштаб')).toHaveTextContent('100%')
    expect(screen.getByLabelText('После: Масштаб')).toHaveTextContent('100%')
    fireEvent.click(screen.getByRole('button', { name: 'Приблизить: До' }))
    expect(screen.getByLabelText('До: Масштаб')).toHaveTextContent('125%')
    expect(screen.getByLabelText('После: Масштаб')).toHaveTextContent('100%')

    fireEvent.click(screen.getByRole('button', { name: 'После: Масштаб 100%' }))
    expect(screen.getByLabelText('После: Масштаб')).toHaveTextContent('200%')
    fireEvent.click(screen.getByRole('button', { name: 'После: Масштаб 200%' }))
    expect(screen.getByLabelText('После: Масштаб')).toHaveTextContent('100%')
    expect(screen.getByAltText('До').parentElement).toHaveClass('zoom-media')

    const beforeViewport = screen.getByRole('button', { name: 'До: Масштаб 125%' })
    fireEvent.pointerDown(beforeViewport, { pointerId: 1, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(beforeViewport, { pointerId: 1, clientX: 132, clientY: 116 })
    fireEvent.pointerUp(beforeViewport, { pointerId: 1, clientX: 132, clientY: 116 })
    expect(screen.getByLabelText('До: Масштаб')).toHaveTextContent('125%')
  })

  it('locks submission immediately and exposes readable ETA helpers', async () => {
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Конвертер' }))
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })

    const submit = screen.getByRole('button', { name: 'Начать обработку' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Обрабатываем файлы' })).toBeDisabled()
    expect(formatDuration(125, 'ru')).toBe('2 мин 5 сек')
    expect(estimateDuration('remove-background', 2, 2)).toBe(120)
  })

  it('shows an approximate percentage and remaining time for a running job', async () => {
    apiMocks.createJob.mockResolvedValue({
      jobId: 'job-1', token: 'capability-token-123456789', expiresAt: '2026-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'job-1', operation: 'image-convert', status: 'running', progress: 0, total: 1,
      createdAt: new Date(Date.now() - 2000).toISOString(), expiresAt: '2026-07-11T03:00:00Z',
    })
    const { container } = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Конвертер' }))
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    expect(await screen.findByText('Обработка на сервере')).toBeInTheDocument()
    expect(screen.getByText(/~\d+% · осталось примерно .* · прошло/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow')
  })

  it('shows one result preview for upscaling and highlights a completed job', async () => {
    apiMocks.createJob.mockResolvedValue({
      jobId: 'job-ready', token: 'capability-token-123456789', expiresAt: '2026-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'job-ready', operation: 'upscale', status: 'succeeded', progress: 1, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2026-07-11T03:00:00Z', resultName: 'upscaled.png', resultType: 'image/png',
    })
    apiMocks.fetchResult.mockResolvedValue(new Blob(['result'], { type: 'image/png' }))
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    expect(await screen.findByAltText('Готовый результат')).toBeInTheDocument()
    expect(container.querySelectorAll('.result-preview .compare-pane')).toHaveLength(1)
    expect(screen.queryByText('Сравнение до и после')).not.toBeInTheDocument()
    expect(screen.queryByText('До')).not.toBeInTheDocument()
    expect(screen.queryByText('После')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Скачать результат/ })).toHaveClass('is-ready')
    expect(document.title).toBe('✓ Результат готов — GPTHub Tools')
    expect(document.querySelector('link[rel~="icon"]')?.getAttribute('href')).toMatch(/^data:image\/svg\+xml,/)
  })

  it('keeps processing jobs while another tool is opened', async () => {
    apiMocks.createJob
      .mockResolvedValueOnce({ jobId: 'upscale-job', token: 'capability-token-upscale', expiresAt: '2026-07-11T03:00:00Z' })
      .mockResolvedValueOnce({ jobId: 'remove-job', token: 'capability-token-remove', expiresAt: '2026-07-11T03:00:00Z' })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'upscale-job', operation: 'upscale', status: 'running', progress: 0, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2026-07-11T03:00:00Z',
    })
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'upscale.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    expect(await screen.findByText('Обработка на сервере')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Удалить фон' }))
    fireEvent.change(input, { target: { files: [new File(['image'], 'remove.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledTimes(2))

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('gpthub-tracked-jobs-v1') || '{}')
      expect(stored.upscale.capability.jobId).toBe('upscale-job')
      expect(stored.remove.capability.jobId).toBe('remove-job')
    })
  })

  it('restores a completed task and marks its tool tab as ready', async () => {
    localStorage.setItem('gpthub-tracked-jobs-v1', JSON.stringify({
      upscale: {
        capability: { jobId: 'stored-upscale', token: 'stored-capability-token', expiresAt: '2099-07-11T03:00:00Z' },
        tab: 'upscale', operation: 'upscale', fileCount: 1, scale: 2, submittedAt: Date.now(), inputPixels: 0,
        job: { jobId: 'stored-upscale', operation: 'upscale', status: 'succeeded', progress: 1, total: 1, createdAt: new Date().toISOString(), expiresAt: '2099-07-11T03:00:00Z', resultName: 'result.png', resultType: 'image/png' },
        error: null, seen: false,
      },
    }))
    apiMocks.fetchResult.mockResolvedValue(new Blob(['result'], { type: 'image/png' }))
    window.history.replaceState({}, '', '/convert/images')
    render(<App />)

    expect(screen.getByRole('button', { name: 'Увеличить: готово: 1' })).toBeInTheDocument()
    expect(document.title).toBe('✓ Результат готов — GPTHub Tools')
    fireEvent.click(screen.getByRole('button', { name: 'Увеличить: готово: 1' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Увеличить' })).toBeInTheDocument())
    expect(screen.getByText('Результат готов')).toBeInTheDocument()
  })
})
