import http from './http';

// 核心分析流程
export const uploadFile = (file) => {
  const fd = new FormData();
  fd.append('file', file);
  return http.post('/upload', fd);
};
export const parsePoints = (sid) => http.post(`/parse/${sid}`);
export const reviewTender = (sid) => http.post(`/review/${sid}`);
// useAi 默认 false：仅本地模板骨架，不调用 AI；显式传 true 且已配置 Key 才走 AI 生成
export const generateDraft = (sid, useAi = false) =>
  http.post(`/generate/${sid}`, null, { params: { use_ai: useAi } });

// 历史记录
export const getHistory = () => http.get('/history');
export const getHistoryDetail = (sid) => http.get(`/history/${sid}`);
export const deleteHistory = (sid) => http.delete(`/history/${sid}`);
export const saveDraft = (sid, markdown) => http.put(`/history/${sid}/draft`, { markdown });

// 统计
export const getStats = () => http.get('/stats');

// AI 模型配置
export const getLlmConfig = () => http.get('/config/llm');
export const saveLlmConfig = (data) => http.put('/config/llm', data);

// 企业素材库
export const getCompanyDoc = (name) => http.get(`/company/${name}`);
export const saveCompanyDoc = (name, content) => http.put(`/company/${name}`, { content });
