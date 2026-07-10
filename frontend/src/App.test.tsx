import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import App, { ZoomPane } from './App'
import { getCopy } from './i18n'

describe('App', () => {
  beforeEach(() => {
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
  })
})
