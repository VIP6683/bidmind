import { createRouter, createWebHistory } from 'vue-router';
import { useAuthStore } from '../store/auth';

const routes = [
	{
		path: '/login',
		name: 'login',
		component: () => import('../views/LoginView.vue'),
		meta: { public: true, title: '登录' }
	},
	{
		path: '/',
		component: () => import('../layouts/MainLayout.vue'),
		redirect: '/workspace',
		children: [
			{
				path: 'workspace',
				name: 'workspace',
				component: () => import('../views/WorkspaceView.vue'),
				meta: { title: '智能分析工作台' }
			},
			{
				path: 'history',
				name: 'history',
				component: () => import('../views/HistoryView.vue'),
				meta: { title: '分析记录' }
			},
			{
				path: 'new',
				name: 'new',
				component: () => import('../views/NewAnalysisView.vue'),
				meta: { title: '新建分析' }
			},
			{
				path: 'analysis/:sid',
				name: 'analysis',
				component: () => import('../views/AnalysisDetailView.vue'),
				meta: { title: '分析详情' }
			},
			{
				path: 'company',
				name: 'company',
				component: () => import('../views/CompanyProfileView.vue'),
				meta: { title: '我的投标知识库' }
			},
			{
				path: 'model-config',
				name: 'model-config',
				component: () => import('../views/ModelConfigView.vue'),
				meta: { title: 'AI模型配置' }
			}
		]
	},
	{ path: '/:pathMatch(.*)*', redirect: '/workspace' }
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach((to) => {
	const auth = useAuthStore();
	if (!to.meta.public && !auth.isLogged)
		return { name: 'login', query: { redirect: to.fullPath } };
	if (to.name === 'login' && auth.isLogged) return { name: 'workspace' };
	return true;
});

router.afterEach((to) => {
	document.title = to.meta.title ? `${to.meta.title} - 标析智策` : '标析智策';
});

export default router;
