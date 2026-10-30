import { defineStore } from 'pinia';
import { uploadFile, parsePoints, reviewTender, generateDraft } from '../api';

/**
 * 新建分析任务流：upload → parse → review → generate 顺序执行。
 * steps: [{ key, label, status: 'wait'|'process'|'success'|'error' }]
 */
export const useAnalysisStore = defineStore('analysis', {
  state: () => ({
    sid: '',
    filename: '',
    running: false,
    current: 0,
    steps: [
      { key: 'upload', label: '上传招标文件', status: 'wait' },
      { key: 'parse', label: '智能体分析与大纲', status: 'wait' },
      { key: 'review', label: '审标风险扫描', status: 'wait' },
      { key: 'generate', label: '生成投标初稿骨架', status: 'wait' },
    ],
    result: null, // { points, summary, review, generate }
    error: '',
  }),
  actions: {
    _set(key, status) {
      this.steps.find((s) => s.key === key).status = status;
    },
    reset() {
      this.$reset();
    },
    async run(file) {
      this.reset();
      this.running = true;
      this.error = '';
      this.filename = file.name;
      this.result = null;
      try {
        // 1) 上传
        this._set('upload', 'process');
        const up = await uploadFile(file);
        this.sid = up.session_id;
        this._set('upload', 'success');
        this.current = 1;

        // 2) 解析要点
        this._set('parse', 'process');
        const points = await parsePoints(this.sid);
        this._set('parse', 'success');
        this.current = 2;

        // 3) 审标扫描
        this._set('review', 'process');
        const review = await reviewTender(this.sid);
        this._set('review', 'success');
        this.current = 3;

        // 4) 生成初稿
        this._set('generate', 'process');
        const gen = await generateDraft(this.sid);
        this._set('generate', 'success');
        this.current = 4;

        this.result = { points, review, generate: gen };
        return { sid: this.sid, points, review, generate: gen };
      } catch (e) {
        // 将当前 process 步骤标记失败
        const cur = this.steps.find((s) => s.status === 'process');
        if (cur) cur.status = 'error';
        this.error = e.response?.data?.error || e.message || '分析失败';
        return null;
      } finally {
        this.running = false;
      }
    },
  },
});
