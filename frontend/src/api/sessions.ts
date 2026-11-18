import client from './client'
import { endpoints } from './endpoints'
import type { ApiResponse, ChatSession } from '../types/api'

interface SessionsData {
  sessions: ChatSession[]
}

interface SessionDetailData {
  session_id: string
  history: [string, string][]
}

export const sessionsApi = {
  /** 当前登录用户的会话列表（后端按 token 识别用户，无需传 userId） */
  list: async () => {
    const res = await client.get<ApiResponse<SessionsData>>(endpoints.getMySessions)
    return res.data
  },

  get: async (id: string) => {
    const res = await client.get<ApiResponse<SessionDetailData>>(endpoints.getSession(id))
    return res.data
  },

  delete: async (id: string) => {
    const res = await client.delete<ApiResponse<null>>(endpoints.deleteSession(id))
    return res.data
  },
}
