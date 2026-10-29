<script setup>
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useAuthStore } from '../store/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const formRef = ref();
const loading = ref(false);
const form = reactive({
	username: auth.remember ? auth.username : '',
	password: '',
	remember: auth.remember
});

const rules = {
	username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
	password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
};

function forgot() {
	ElMessage.info('内测版暂不支持找回密码，请联系管理员');
}

async function submit() {
	await formRef.value.validate().catch(() => Promise.reject());
	loading.value = true;
	setTimeout(() => {
		auth.login(form.username.trim(), form.remember);
		ElMessage.success('登录成功，欢迎回来');
		router.push(route.query.redirect || '/workspace');
		loading.value = false;
	}, 400);
}
</script>

<template>
	<div class="login-page">
		<!-- 左侧：品牌蓝 -->
		<div class="brand-pane">
			<div class="brand-top">
				<div class="brand-logo">
					<div class="logo-icon">
						<el-icon :size="18"><Document /></el-icon>
					</div>
					<div>
						<div class="brand-name">标析智策</div>
						<div class="brand-sub">Tender Intelligence</div>
					</div>
				</div>
			</div>

			<div class="brand-mid">
				<div class="brand-kicker">智能识别 · 合规判断 · 报告生成</div>
				<h1 class="brand-slogan">让每一份招标文件<br />都经得起推敲。</h1>
				<p class="brand-desc">
					从关键条款提取到废标风险预警，帮助投标团队更快、更准确地完成文件审阅。
				</p>
			</div>

			<div class="brand-feats">
				<span>✓ 本地化文件处理</span>
				<span>✓ 全流程留痕可追溯</span>
				<span>✓ 企业级数据隔离</span>
			</div>

			<div class="ring-deco big"></div>
			<div class="ring-deco small"></div>
		</div>

		<!-- 右侧：登录表单 -->
		<div class="form-pane">
			<div class="form-inner">
				<div class="form-welcome">欢迎回来</div>
				<h2 class="form-title">登录智能分析工作台</h2>
				<div class="form-sub">按模板规范辅助与逐项核对。</div>

				<el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
					<el-form-item label="用户名" prop="username">
						<el-input v-model="form.username" placeholder="请输入用户名" :prefix-icon="'User'" />
					</el-form-item>
					<el-form-item label="密码" prop="password">
						<el-input
							v-model="form.password"
							type="password"
							show-password
							placeholder="请输入密码"
							:prefix-icon="'Lock'"
						/>
					</el-form-item>
					<div class="form-row">
						<el-checkbox v-model="form.remember">记住用户名</el-checkbox>
						<el-link type="primary" :underline="false" @click="forgot">忘记密码</el-link>
					</div>
					<el-button
						type="primary"
						class="submit"
						:loading="loading"
						@click="submit"
					>
						登录系统 →
					</el-button>
				</el-form>

				<div class="test-account">
					<div class="ta-row">
						<span class="ta-label">测试账号</span>
						<span class="ta-value">chenming</span>
					</div>
					<div class="ta-row">
						<span class="ta-label">初始密码</span>
						<span class="ta-value">Tecklee@2025</span>
					</div>
				</div>
			</div>

			<div class="form-foot">标析智策 · 招标文件智能分析平台</div>
		</div>
	</div>
</template>

<style scoped>
.login-page {
	display: flex;
	height: 100vh;
	overflow: hidden;
}

/* ===== 左侧品牌区 ===== */
.brand-pane {
	flex: 1.15;
	min-width: 0;
	position: relative;
	overflow: hidden;
	color: #fff;
	padding: 40px 48px;
	display: flex;
	flex-direction: column;
	justify-content: space-between;
	background: linear-gradient(150deg, #0a4fbc 0%, #0b57d0 50%, #1d6feb 100%);
}
.ring-deco {
	position: absolute;
	border: 1px solid rgba(255, 255, 255, 0.12);
	border-radius: 50%;
	pointer-events: none;
}
.ring-deco.big {
	width: 420px;
	height: 420px;
	right: -120px;
	bottom: -160px;
}
.ring-deco.small {
	width: 260px;
	height: 260px;
	right: 40px;
	bottom: -60px;
	border-color: rgba(255, 255, 255, 0.09);
}

.brand-logo {
	display: flex;
	align-items: center;
	gap: 10px;
}
.logo-icon {
	width: 36px;
	height: 36px;
	border-radius: 9px;
	background: #fff;
	color: #0b57d0;
	display: flex;
	align-items: center;
	justify-content: center;
	flex-shrink: 0;
}
.brand-name {
	font-size: 16px;
	font-weight: 700;
	line-height: 1.2;
}
.brand-sub {
	font-size: 10px;
	color: rgba(255, 255, 255, 0.6);
	letter-spacing: 0.14em;
	margin-top: 2px;
}

.brand-kicker {
	font-size: 12px;
	letter-spacing: 3px;
	color: rgba(255, 255, 255, 0.72);
	margin-bottom: 16px;
}
.brand-slogan {
	font-size: 34px;
	font-weight: 700;
	line-height: 1.35;
	margin-bottom: 14px;
}
.brand-desc {
	font-size: 13px;
	line-height: 1.8;
	color: rgba(255, 255, 255, 0.78);
	max-width: 420px;
}

.brand-feats {
	display: flex;
	gap: 22px;
	font-size: 12px;
	color: rgba(255, 255, 255, 0.8);
	position: relative;
	z-index: 1;
}

/* ===== 右侧表单区 ===== */
.form-pane {
	width: 480px;
	flex-shrink: 0;
	background: #fff;
	position: relative;
	display: flex;
	align-items: center;
	justify-content: center;
}
.form-inner {
	width: 320px;
}
.form-welcome {
	font-size: 12px;
	color: var(--muted);
	letter-spacing: 2px;
	margin-bottom: 8px;
}
.form-title {
	font-size: 22px;
	font-weight: 700;
	color: var(--ink);
	margin-bottom: 6px;
}
.form-sub {
	font-size: 12px;
	color: var(--muted);
	margin-bottom: 24px;
}
.form-row {
	display: flex;
	align-items: center;
	justify-content: space-between;
	margin: 2px 0 18px;
}
.submit {
	width: 100%;
	height: 44px;
	font-size: 15px;
	letter-spacing: 0.12em;
}

.test-account {
	margin-top: 20px;
	border: 1px solid var(--border);
	border-radius: 8px;
	padding: 10px 14px;
	background: #fafbfd;
}
.ta-row {
	display: flex;
	align-items: center;
	gap: 12px;
	line-height: 1.9;
}
.ta-label {
	font-size: 11px;
	color: var(--muted);
	width: 48px;
	flex-shrink: 0;
}
.ta-value {
	font-size: 12px;
	color: var(--ink);
	font-family: Consolas, Monaco, monospace;
}

.form-foot {
	position: absolute;
	bottom: 20px;
	left: 0;
	right: 0;
	text-align: center;
	font-size: 11px;
	color: var(--muted);
}

@media (max-width: 860px) {
	.brand-pane {
		display: none;
	}
	.form-pane {
		width: 100%;
	}
}
</style>
