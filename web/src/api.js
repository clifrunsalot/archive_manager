const apiBase = import.meta.env.VITE_API_BASE || '/api'

async function request(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { Accept: 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const payload = await response.json()
      if (Array.isArray(payload.detail)) {
        detail = payload.detail.map((item) => item.msg || 'Invalid request').join('; ')
      } else if (typeof payload.detail === 'string') {
        detail = payload.detail
      } else if (payload.detail && typeof payload.detail === 'object') {
        detail = payload.detail.message || payload.detail.status || JSON.stringify(payload.detail)
      }
    } catch {
      // Keep the status error when the server did not return JSON.
    }
    throw new Error(detail)
  }
  return response.json()
}

export const apiEnabled = import.meta.env.VITE_API_MODE === 'live'

export function getSession() {
  return request('/v1/session')
}

export function getReadiness() {
  return request('/ready')
}

export function getSecurityStatus() {
  return request('/v1/security/status')
}

export function getActivity() {
  return request('/v1/activity')
}

export function previewReset() {
  return request('/v1/admin/reset-preview', { method: 'POST' })
}

export function executeReset(confirmationToken) {
  return request('/v1/admin/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation_token: confirmationToken, confirmation: 'RESET ARCHIVE' }),
  })
}

export function getEvents(search = '') {
  const query = search ? `?search=${encodeURIComponent(search)}` : ''
  return request(`/v1/events${query}`)
}

export function getArtifacts() {
  return request('/v1/artifacts')
}

export function submitQuery(question, topK = 10) {
  return request('/v1/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, top_k: topK }),
  })
}

export function previewIntake({ eventId, eventType, subjectRef, notes, files }) {
  const formData = new FormData()
  formData.append('event_id', eventId)
  formData.append('event_type', eventType)
  if (subjectRef) formData.append('subject_ref', subjectRef)
  if (notes) formData.append('notes', notes)
  files.forEach((file) => formData.append('files', file))
  return request('/v1/intake/preview', { method: 'POST', body: formData })
}

export function submitIntake({ eventId, eventType, subjectRef, notes, files }) {
  const formData = new FormData()
  formData.append('event_id', eventId)
  formData.append('event_type', eventType)
  if (subjectRef) formData.append('subject_ref', subjectRef)
  if (notes) formData.append('notes', notes)
  files.forEach((file) => formData.append('files', file))
  return request('/v1/intake', { method: 'POST', body: formData })
}

export function getIntakeJob(jobId) {
  return request(`/v1/intake/jobs/${encodeURIComponent(jobId)}`)
}

export function previewDelete(eventId) {
  return request(`/v1/events/${encodeURIComponent(eventId)}/delete-preview`, { method: 'POST' })
}

export function previewExpiredPurge() {
  return request('/v1/events/expired-preview')
}

export function executeMutation(confirmationToken, confirmation) {
  return request('/v1/events/mutation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation_token: confirmationToken, confirmation }),
  })
}

export function getMutationJob(jobId) {
  return request(`/v1/events/mutation-jobs/${encodeURIComponent(jobId)}`)
}
