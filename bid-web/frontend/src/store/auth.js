import { defineStore } from 'pinia';

const KEY = 'bz_logged';
const REMEMBER_KEY = 'bz_remember_user';

export const useAuthStore = defineStore('auth', {
  state: () => ({
    logged: localStorage.getItem(KEY) === '1',
    username: localStorage.getItem(REMEMBER_KEY) || '',
    remember: !!localStorage.getItem(REMEMBER_KEY),
  }),
  getters: { isLogged: (s) => s.logged },
  actions: {
    login(username, remember) {
      this.logged = true;
      this.username = username;
      this.remember = remember;
      localStorage.setItem(KEY, '1');
      if (remember && username) localStorage.setItem(REMEMBER_KEY, username);
      else localStorage.removeItem(REMEMBER_KEY);
    },
    logout() {
      this.logged = false;
      localStorage.removeItem(KEY);
    },
  },
});
