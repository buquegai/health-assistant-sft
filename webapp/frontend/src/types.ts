export interface User {
  id: string
  username: string
}

export interface Conversation {
  id: string
  title: string
  model_id: string
  created_at: string
  updated_at: string
}

export interface ModelOption {
  id: string
  name: string
}

export interface ModelList {
  items: ModelOption[]
  default_model_id: string
}

export interface ChatMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  status: 'completed' | 'generating' | 'stopped' | 'failed'
  reply_to_id: string | null
  finish_reason: string | null
  error_code: string | null
  created_at: string
  updated_at: string
  history_trimmed?: boolean
}

export interface Page<T> {
  items: T[]
  next_cursor: string | number | null
}

export interface ApiErrorBody {
  code: string
  message: string
}

export class ApiError extends Error {
  status: number
  code: string
  requestId: string | null

  constructor(status: number, body: ApiErrorBody, requestId: string | null = null) {
    super(body.message)
    this.status = status
    this.code = body.code
    this.requestId = requestId
  }
}
