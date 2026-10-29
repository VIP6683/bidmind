import { defineStore } from 'pinia';
import { getHistory, getStats, deleteHistory } from '../api';

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    history: [],
    stats: null,
    loading: false,
    loaded: false,
  }),
  actions: {
    async fetchAll() {
      this.loading = true;
      try {
        const [h, s] = await Promise.all([getHistory(), getStats()]);
        this.history = h.items;
        this.stats = s;
        this.loaded = true;
      } finally {
        this.loading = false;
      }
    },
    async remove(sid) {
      await deleteHistory(sid);
      this.history = this.history.filter((h) => h.sid !== sid);
      await this.fetchStatsOnly();
    },
    async fetchStatsOnly() {
      this.stats = await getStats();
    },
  },
});
