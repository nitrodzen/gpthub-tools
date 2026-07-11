export type Operation =
  | 'upscale'
  | 'remove-background'
  | 'image-convert'
  | 'document-convert'
  | 'pdf-merge'
  | 'pdf-split'
  | 'images-to-pdf'
  | 'pdf-to-images'

export type Job = {
  jobId: string
  operation: Operation
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  total: number
  createdAt: string
  expiresAt: string
  resultName?: string
  resultType?: string
  error?: { code: string; message: string }
}

export type JobCapability = { jobId: string; token: string; expiresAt: string }

async function parseError(response: Response): Promise<Error & { code?: string }> {
  const payload = await response.json().catch(() => null)
  const error = new Error(payload?.error?.message || `HTTP ${response.status}`) as Error & { code?: string }
  error.code = payload?.error?.code
  return error
}

export async function createJob(operation: Operation, files: File[], options: Record<string, unknown>) {
  const body = new FormData()
  files.forEach((file) => body.append('files', file, file.name))
  body.append('options', JSON.stringify(options))
  const response = await fetch(`/api/jobs/${operation}`, { method: 'POST', body })
  if (!response.ok) throw await parseError(response)
  return (await response.json()) as JobCapability
}

export async function getJob(capability: JobCapability) {
  const response = await fetch(`/api/jobs/${capability.jobId}`, {
    headers: { 'X-Capability-Token': capability.token },
  })
  if (!response.ok) throw await parseError(response)
  return (await response.json()) as Job
}

export async function fetchResult(capability: JobCapability) {
  const response = await fetch(`/api/jobs/${capability.jobId}/download`, {
    headers: { 'X-Capability-Token': capability.token },
  })
  if (!response.ok) throw await parseError(response)
  return response.blob()
}

export async function cancelJob(capability: JobCapability) {
  const response = await fetch(`/api/jobs/${capability.jobId}`, {
    method: 'DELETE',
    headers: { 'X-Capability-Token': capability.token },
  })
  if (!response.ok) throw await parseError(response)
}
