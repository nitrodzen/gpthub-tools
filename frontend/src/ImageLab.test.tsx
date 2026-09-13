import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ModelExplorer } from './ImageLab'

const api = vi.hoisted(() => ({ createJob: vi.fn(), getJob: vi.fn(), fetchResult: vi.fn(), cancelJob: vi.fn() }))
vi.mock('./api', () => api)
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('uploads only a crop and reuses its completed preview when switching back', async () => {
  const drawImage = vi.fn()
  vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 1000, height: 800, close: vi.fn() }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D)
  vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation(callback => callback(new Blob(['crop'], { type: 'image/png' })))
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:preview')
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  api.createJob.mockResolvedValue({ jobId: 'preview', token: 'secret', expiresAt: '2099-01-01' })
  api.getJob.mockResolvedValue({ status: 'succeeded' })
  api.fetchResult.mockResolvedValue(new Blob(['result']))
  api.cancelJob.mockResolvedValue(undefined)
  const file = new File(['full-image'], 'photo.png')
  const props = { file, model: 'standard', scale: 2, strength: 100, language: 'ru' as const }
  const { rerender } = render(<ModelExplorer {...props} />)
  fireEvent.click(screen.getByRole('button', { name: 'Проба выбранного режима' }))
  await waitFor(() => expect(screen.getByRole('button', { name: /Готово за/ })).toBeDisabled())
  expect(api.createJob).toHaveBeenCalledOnce()
  expect(api.createJob.mock.calls[0][1][0].size).toBe(4)
  expect(drawImage.mock.calls[0].slice(1)).toEqual([420, 320, 160, 160, 0, 0, 160, 160])
  rerender(<ModelExplorer {...props} model="detail" />)
  expect(screen.getByRole('button', { name: 'Проба выбранного режима' })).toBeEnabled()
  rerender(<ModelExplorer {...props} />)
  expect(screen.getByRole('button', { name: /Готово за/ })).toBeDisabled()
  expect(api.createJob).toHaveBeenCalledOnce()
  await waitFor(() => expect(api.cancelJob).toHaveBeenCalled())
})
