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

  it('zooms the before and after panes independently', () => {
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
})
