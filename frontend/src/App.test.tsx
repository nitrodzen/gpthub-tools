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
    expect(screen.getByRole('slider', { name: 'Качество файла: 100%' })).toHaveValue('100')
    expect(screen.getByLabelText(/90% уменьшает файл примерно на 15–30%/)).toBeInTheDocument()
  })

  it('offers all models and constrains the native photo model to 2x', () => {
    render(<App />)
    const model = screen.getByRole('combobox', { name: 'Режим обработки' })
    expect(model.querySelectorAll('option')).toHaveLength(5)
    fireEvent.click(screen.getByRole('button', { name: '4×' }))
    fireEvent.change(model, { target: { value: 'photo' } })
    expect(screen.getByRole('button', { name: '2×' })).toHaveClass('active')
    expect(screen.getByRole('button', { name: '4×' })).toBeDisabled()
  })

  it('submits enhancement without enlargement as an additive operation', async () => {
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Без увеличения' }))
    const file = new File(['image'], 'photo.png', { type: 'image/png' })
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('image-enhance', [file], expect.objectContaining({ scale: 1 })))
  })

  it('accepts scanned PDFs for OCR and submits the selected output format', async () => {
    window.history.replaceState({}, '', '/convert/documents/ocr')
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]')!
    expect(input.getAttribute('accept')).toContain('.pdf')
    expect(input.getAttribute('accept')).toContain('.png')
    const file = new File(['scan'], 'scan.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Формат результата' }), { target: { value: 'pdf' } })
    fireEvent.change(input, { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('ocr', [file], { format: 'pdf', language: 'rus+eng' }))
  })

  it('submits the image preparation chain without changing the other tool contracts', async () => {
    window.history.replaceState({}, '', '/prepare-image')
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    const file = new File(['image'], 'product.png', { type: 'image/png' })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Убрать шум и следы сжатия' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Фон результата' }), { target: { value: 'color' } })
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('image-pipeline', [file], expect.objectContaining({ removeBackground: true, enhance: true, background: '#ffffff', scale: 2 })))
  })

  it('normalizes the legacy documents route and exposes action-specific routes and formats', () => {
    window.history.replaceState({}, '', '/convert/documents')
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement

    expect(window.location.pathname).toBe('/convert/documents/word-to-pdf')
    expect(screen.getByRole('button', { name: 'Word → PDF' })).toHaveAttribute('aria-pressed', 'true')
    expect(input).toHaveAttribute('accept', '.doc,.docx,.odt,.rtf')

    fireEvent.click(screen.getByRole('button', { name: 'PDF → Word' }))
    expect(window.location.pathname).toBe('/convert/documents/pdf-to-word')
    expect(input).toHaveAttribute('accept', '.pdf')

    fireEvent.click(screen.getByRole('button', { name: 'Word → Excel' }))
    expect(window.location.pathname).toBe('/convert/documents/word-to-excel')
    expect(input).toHaveAttribute('accept', '.doc,.docx,.odt,.rtf')

    fireEvent.click(screen.getByRole('button', { name: 'Excel → Word' }))
    expect(window.location.pathname).toBe('/convert/documents/excel-to-word')
    expect(input).toHaveAttribute('accept', '.xls,.xlsx,.ods,.csv')
  })

  it('follows document routes after browser history navigation and clears staged files', () => {
    window.history.replaceState({}, '', '/convert/documents/excel-to-word')
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['csv'], 'staged.csv')] } })
    expect(screen.getByText('staged.csv')).toBeInTheDocument()

    window.history.replaceState({}, '', '/convert/documents/pdf-to-word')
    fireEvent.popState(window)

    expect(screen.getByRole('button', { name: 'PDF → Word' })).toHaveAttribute('aria-pressed', 'true')
    expect(input).toHaveAttribute('accept', '.pdf')
    expect(screen.queryByText('staged.csv')).not.toBeInTheDocument()
  })

  it('maps Word to Excel to its operation and sends the UI locale', async () => {
    window.history.replaceState({}, '', '/convert/documents/word-to-excel')
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    const document = new File(['word'], 'tables.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [document] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('word-to-excel', [document], { locale: 'ru' }))
    expect(estimateDuration('word-to-excel', 2, 3)).toBe(135)
  })

  it('sends exact CSV controls for Excel to Word', async () => {
    window.history.replaceState({}, '', '/convert/documents/excel-to-word')
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    const csv = new File(['name;value'], 'table.csv', { type: 'text/csv' })

    fireEvent.change(screen.getByRole('combobox', { name: 'Разделитель CSV' }), { target: { value: 'semicolon' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Кодировка CSV' }), { target: { value: 'windows-1251' } })
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [csv] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('excel-to-word', [csv], {
      locale: 'ru', csvDelimiter: 'semicolon', csvEncoding: 'windows-1251',
    }))
    expect(estimateDuration('excel-to-word', 2, 2)).toBe(90)
  })

  it('keeps PDF to Word on the compatible document conversion operation', async () => {
    window.history.replaceState({}, '', '/convert/documents/pdf-to-word')
    apiMocks.createJob.mockReturnValue(new Promise(() => {}))
    const { container } = render(<App />)
    const pdf = new File(['pdf'], 'source.pdf', { type: 'application/pdf' })
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [pdf] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    await waitFor(() => expect(apiMocks.createJob).toHaveBeenCalledWith('document-convert', [pdf], {}))
  })

  it('keeps one converter job while document actions change', async () => {
    window.history.replaceState({}, '', '/convert/documents/word-to-excel')
    apiMocks.createJob.mockResolvedValue({
      jobId: 'office-running', token: 'office-running-token', expiresAt: '2099-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'office-running', operation: 'word-to-excel', status: 'running', progress: 0, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2099-07-11T03:00:00Z', warnings: [],
    })
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['word'], 'tables.docx')] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    expect(await screen.findByText('Обработка на сервере')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Excel → Word' }))
    fireEvent.change(input, { target: { files: [new File(['csv'], 'table.csv')] } })
    expect(screen.getByRole('button', { name: 'Обрабатываем файлы' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Обрабатываем файлы' }))
    expect(apiMocks.createJob).toHaveBeenCalledTimes(1)
  })

  it('localizes Office conversion job errors', async () => {
    window.history.replaceState({}, '', '/convert/documents/word-to-excel')
    apiMocks.createJob.mockResolvedValue({
      jobId: 'office-failed', token: 'office-failed-token', expiresAt: '2099-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'office-failed', operation: 'word-to-excel', status: 'failed', progress: 0, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2099-07-11T03:00:00Z',
      error: { code: 'OFFICE_TOO_COMPLEX', message: 'Backend fallback' }, warnings: [],
    })
    const { container } = render(<App />)
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [new File(['word'], 'complex.docx')] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))

    expect(await screen.findByText('Документ слишком сложный для надёжной конвертации. Упростите его и повторите попытку.')).toBeInTheDocument()
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

  it('disables cancellation while the request is in flight', async () => {
    apiMocks.createJob.mockResolvedValue({
      jobId: 'job-cancel', token: 'capability-token-123456789', expiresAt: '2026-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'job-cancel', operation: 'upscale', status: 'running', progress: 0, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2026-07-11T03:00:00Z',
    })
    let completeCancellation: () => void = () => undefined
    apiMocks.cancelJob.mockReturnValue(new Promise<void>((resolve) => { completeCancellation = resolve }))
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    expect(await screen.findByRole('button', { name: 'Отменить' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))
    expect(screen.getByRole('button', { name: 'Отменяем задание…' })).toBeDisabled()
    expect(apiMocks.cancelJob).toHaveBeenCalledTimes(1)

    completeCancellation()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Отменяем задание…' })).not.toBeInTheDocument())
  })

  it('keeps the task available when cancellation fails', async () => {
    apiMocks.createJob.mockResolvedValue({
      jobId: 'job-cancel-error', token: 'capability-token-123456789', expiresAt: '2026-07-11T03:00:00Z',
    })
    apiMocks.getJob.mockResolvedValue({
      jobId: 'job-cancel-error', operation: 'upscale', status: 'running', progress: 0, total: 1,
      createdAt: new Date().toISOString(), expiresAt: '2026-07-11T03:00:00Z',
    })
    apiMocks.cancelJob.mockRejectedValue(new Error('Cancellation temporarily failed'))
    const { container } = render(<App />)
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['image'], 'photo.png', { type: 'image/png' })] } })
    fireEvent.click(screen.getByRole('button', { name: 'Начать обработку' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Отменить' }))

    expect(await screen.findByText('Cancellation temporarily failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeInTheDocument()
  })

  it('compares original and upscaled image and highlights a completed job', async () => {
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

    expect(await screen.findByAltText('После')).toBeInTheDocument()
    expect(screen.getByAltText('До')).toBeInTheDocument()
    expect(container.querySelectorAll('.image-comparison')).toHaveLength(1)
    fireEvent.change(screen.getByRole('slider', { name: 'Граница до и после' }), { target: { value: '75' } })
    expect(container.querySelector('.comparison-overlay')).toHaveStyle({ clipPath: 'inset(0 25% 0 0)' })
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

  it('restores and localizes document conversion warnings', async () => {
    localStorage.setItem('gpthub-tracked-jobs-v1', JSON.stringify({
      convert: {
        capability: { jobId: 'stored-office', token: 'stored-office-token', expiresAt: '2099-07-11T03:00:00Z' },
        tab: 'convert', operation: 'excel-to-word', fileCount: 1, scale: 2, submittedAt: Date.now(), inputPixels: 0,
        job: {
          jobId: 'stored-office', operation: 'excel-to-word', status: 'succeeded', progress: 1, total: 1,
          createdAt: new Date().toISOString(), expiresAt: '2099-07-11T03:00:00Z', resultName: 'table.docx',
          resultType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          warnings: [
            { code: 'FORMULAS_AS_VALUES', message: 'Backend fallback' },
            { code: 'CSV_DETECTION_GUESSED', message: 'Backend fallback' },
          ],
        },
        error: null, seen: false,
      },
    }))
    window.history.replaceState({}, '', '/convert/documents/excel-to-word')
    render(<App />)

    expect(screen.getByLabelText('Предупреждения')).toHaveTextContent('Формулы перенесены как сохранённые значения, а при их отсутствии — как текст формулы.')
    expect(screen.getByLabelText('Предупреждения')).toHaveTextContent('Кодировка или разделитель CSV определены автоматически; проверьте результат.')
    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem('gpthub-tracked-jobs-v1') || '{}')
      expect(stored.convert.job.warnings).toHaveLength(2)
    })

    fireEvent.click(screen.getByRole('button', { name: 'English' }))
    expect(screen.getByLabelText('Warnings')).toHaveTextContent('Formulas were transferred as saved values, or as formula text when no saved value existed.')
  })

  it('includes the support email in the footer', () => {
    render(<App />)
    expect(screen.getByRole('link', { name: 'HELP / У меня есть вопрос или проблема' })).toHaveAttribute(
      'href', 'mailto:support@gpthub.ru?subject=GPTHub%20Tools',
    )
  })
})
