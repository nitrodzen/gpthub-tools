import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import App from './App'

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
})
