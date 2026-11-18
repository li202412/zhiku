export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

export interface UserInfo {
  id?: string
  user_id?: string
  uuid?: string
  username: string
  email: string
  phone?: string
  gender?: string
  bio?: string
  avatar?: string
  date_joined?: string
  is_active?: boolean
}

export interface ChatSession {
  id: string
  user_id?: string
  title: string
  metadata?: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface KnowledgeDocument {
  id: string
  user_id: string
  md5: string
  filename: string
  file_size: number
  file_type: string
  status: string
  chunk_count: number
  created_at: string
}

export interface KnowledgeChunk {
  chunk_id: string
  index: number
  content: string
  page: number
  images: string[]
}

export interface KnowledgeDocumentDetail {
  id: string
  user_id: string
  md5: string
  filename: string
  chunk_count: number
  content: string
  images: string[]
  created_at: string | null
  chunks: KnowledgeChunk[]
}

export interface SSEMessage {
  type: 'thinking' | 'response' | 'done' | 'error'
  content?: string
  session_id?: string
  stage?: string
  details?: Record<string, unknown>
}

export interface KnowledgeSSEMessage {
  event_type: 'processing' | 'completed' | 'error' | 'finish'
  filename?: string
  progress?: number
  current?: number
  total?: number
  message?: string
  md5?: string
  knowledge_id?: string
  status?: string
  error_message?: string
}
