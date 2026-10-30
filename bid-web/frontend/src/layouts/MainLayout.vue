<script setup>
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { storeToRefs } from 'pinia';
import { useAuthStore } from '../store/auth';
import { useWorkspaceStore } from '../store/workspace';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const ws = useWorkspaceStore();
const { stats } = storeToRefs(ws);

const collapsed = ref(false);
const active = computed(() => '/' + route.path.split('/')[1]);
const pageTitle = computed(() => route.meta.title || '');

function logout() {
	auth.logout();
	ElMessage.success('已退出登录');
	router.push('/login');
}
</script>

<template>
	<el-container class="layout">
		<!-- 侧边栏（可折叠） -->
		<aside class="sidebar" :class="{ collapsed }">
			<!-- 左上角：Logo + 登录信息 -->
			<div class="side-header">
				<div class="logo-icon">
					<el-icon :size="18"><Document /></el-icon>
				</div>
				<div v-if="!collapsed" class="head-text">
					<div class="logo-name">标析智策</div>
					<div class="logo-sub">{{ auth.username || '访客用户' }} · 本地内测版</div>
				</div>
			</div>

			<el-menu
				:default-active="active"
				router
				:collapse="collapsed"
				:collapse-transition="false"
				class="menu"
			>
				<el-menu-item index="/workspace">
					<el-icon><Monitor /></el-icon><template #title>工作台</template>
				</el-menu-item>
				<el-menu-item index="/history">
					<el-icon><Files /></el-icon><template #title>分析记录</template>
				</el-menu-item>
				<el-menu-item index="/new">
					<el-icon><Plus /></el-icon><template #title>新建分析</template>
				</el-menu-item>

				<div v-if="!collapsed" class="menu-group">工作区</div>
				<el-menu-item index="/collab" disabled>
					<el-icon><ChatDotSquare /></el-icon><template #title>协作标注</template>
				</el-menu-item>
				<el-menu-item index="/company">
					<el-icon><Collection /></el-icon><template #title>我的投标知识库</template>
				</el-menu-item>

				<div v-if="!collapsed" class="menu-group">系统设置</div>
				<el-menu-item index="/model-config">
					<el-icon><Setting /></el-icon><template #title>AI模型配置</template>
				</el-menu-item>
			</el-menu>
		</aside>

		<!-- 主区 -->
		<el-container class="main-wrap">
			<header class="topbar">
				<el-breadcrumb separator="/">
					<el-breadcrumb-item>我的文件</el-breadcrumb-item>
					<el-breadcrumb-item>{{ pageTitle }}</el-breadcrumb-item>
				</el-breadcrumb>
				<div class="topbar-right">
					<el-badge
						:value="stats?.risk_total ?? 0"
						:hidden="!stats?.risk_total"
						:max="99"
						class="bell"
					>
						<el-icon :size="16"><Bell /></el-icon>
					</el-badge>
					<el-avatar :size="28" class="avatar-sm" src="/avatar.jpg"></el-avatar>
					<el-tooltip content="退出登录" placement="bottom">
						<el-icon class="logout" @click="logout"><SwitchButton /></el-icon>
					</el-tooltip>
				</div>
			</header>
			<main class="content">
				<router-view />
			</main>
		</el-container>
	</el-container>
</template>

<style scoped>
.layout {
	height: 100vh;
}

.sidebar {
	width: 208px;
	flex-shrink: 0;
	background: #fff;
	border-right: 1px solid var(--border);
	display: flex;
	flex-direction: column;
	transition: width 0.2s ease;
	overflow: hidden;
}
.sidebar.collapsed {
	width: 64px;
}

/* 左上角：Logo + 登录信息 + 折叠按钮 */
.side-header {
	display: flex;
	align-items: center;
	gap: 10px;
	padding: 14px 12px 14px 16px;
	border-bottom: 1px solid var(--bg-deep);
	flex-shrink: 0;
}
.sidebar.collapsed .side-header {
	flex-direction: column;
	padding: 12px 8px;
	gap: 8px;
}
.logo-icon {
	width: 34px;
	height: 34px;
	border-radius: 8px;
	background: linear-gradient(135deg, var(--primary), var(--primary-deep));
	color: #fff;
	display: flex;
	align-items: center;
	justify-content: center;
	flex-shrink: 0;
}
.head-text {
	flex: 1;
	min-width: 0;
}
.logo-name {
	font-size: 15px;
	font-weight: 700;
	line-height: 1.2;
	white-space: nowrap;
}
.logo-sub {
	font-size: 11px;
	color: var(--muted);
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}
.collapse-btn {
	flex-shrink: 0;
	color: var(--muted);
	cursor: pointer;
	padding: 4px;
	border-radius: 6px;
}
.collapse-btn:hover {
	color: var(--primary);
	background: var(--primary-light);
}

.menu {
	border-right: none;
	flex: 1;
}
.menu:not(.el-menu--collapse) {
	width: 100%;
}
.menu :deep(.el-menu-item) {
	height: 42px;
	margin: 2px 8px;
	border-radius: 8px;
}
.menu :deep(.el-menu-item.is-active) {
	background: var(--primary-light);
	font-weight: 600;
}

.menu-group {
	font-size: 11px;
	color: var(--muted);
	padding: 14px 20px 6px;
}

.main-wrap {
	flex: 1;
	min-width: 0;
	flex-direction: column;
}

.topbar {
	height: 52px;
	background: #fff;
	border-bottom: 1px solid var(--border);
	display: flex;
	align-items: center;
	justify-content: space-between;
	padding: 0 24px;
	flex-shrink: 0;
}
.topbar-right {
	display: flex;
	align-items: center;
	gap: 16px;
}
.bell {
	cursor: pointer;
	color: var(--muted);
}
.avatar-sm {
	background: var(--primary);
}
.logout {
	color: var(--muted);
	cursor: pointer;
}
.logout:hover {
	color: var(--danger);
}

.content {
	flex: 1;
	overflow-y: auto;
	padding: 20px 24px;
}
</style>
