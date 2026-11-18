export const endpoints = {
  // Auth
  login: '/user/login/',
  logout: '/user/logout/',
  register: '/user/register/',
  profile: '/user/detail/',
  userUpdate: '/user/update/',
  changePassword: '/user/reset-password/',

  // File upload
  uploadFile: '/file/upload/',

  // AI Chat
  agentQueryStream: '/chat/agent/query/stream',

  // Sessions
  getSession: (id: string) => `/chat/session/${id}`,
  deleteSession: (id: string) => `/chat/session/${id}`,
  getMySessions: '/chat/sessions',
  getUserSessions: (userId: string) => `/chat/sessions/${userId}`,

  // Knowledge Base
  uploadSingleFile: '/knowledge/add/single',
  uploadMultipleFiles: '/knowledge/add/multiple',
  uploadMultipleStream: '/knowledge/add/multiple/stream',
  cleanVectors: '/knowledge/clean',
  knowledgeList: '/knowledge/list',
  knowledgeDetail: '/knowledge/detail',
  knowledgeChunks: '/knowledge/chunks',
  knowledgeImage: (md5: string, filename: string) => `/knowledge/image/${md5}/${filename}`,
  knowledgeMd5List: '/knowledge/md5/list',
  knowledgeMd5Delete: (md5: string) => `/knowledge/md5/delete/${md5}`,
  knowledgeDeleteFilename: '/knowledge/delete/filename',

  // Documents reorder
  reorderDocuments: '/chat/reorder',
} as const
