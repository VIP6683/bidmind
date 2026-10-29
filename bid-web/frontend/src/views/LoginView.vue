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
		<!-- 左侧：浅色插画区（vben 风格） -->
		<div class="brand-pane">
			<div class="brand-logo">
				<div class="logo-icon">
					<el-icon :size="18"><Document /></el-icon>
				</div>
				<div>
					<div class="brand-name">标析智策</div>
					<div class="brand-sub">Tender Intelligence</div>
				</div>
			</div>

			<div class="brand-center">
				<!-- 纯 CSS 插画：文档分析场景 -->
				<div class="illus">
					<div class="illus-card main">
						<div class="ic-line w60"></div>
						<div class="ic-line w80"></div>
						<div class="ic-line w45"></div>
						<div class="ic-chart">
							<span style="height: 18px"></span>
							<span style="height: 34px"></span>
							<span style="height: 26px"></span>
							<span style="height: 42px"></span>
						</div>
					</div>
					<div class="illus-card back left">
						<div class="ic-line w70"></div>
						<div class="ic-line w50"></div>
					</div>
					<div class="illus-badge pass">✓ 废标自查通过</div>
					<div class="illus-badge doc">📄 招标要点</div>
					<div class="illus-dots"></div>
				</div>

				<h1 class="brand-slogan">开箱即用的招标文件智能分析系统</h1>
				<p class="brand-desc">工程化 · 高性能 · 可维护投标工作台</p>
			</div>

			<div class="brand-feats">
				<span>✓ 本地化文件处理</span>
				<span>✓ 全流程留痕可追溯</span>
				<span>✓ 企业级数据隔离</span>
			</div>
		</div>

		<!-- 右侧：白色登录面板 -->
		<div class="form-pane">
			<div class="form-inner">
				<h2 class="form-title">欢迎回来 👋</h2>
				<div class="form-sub">请输入您的账户信息以开始管理您的项目</div>

				<el-form
					ref="formRef"
					:model="form"
					:rules="rules"
					size="large"
					@keyup.enter="submit"
				>
					<el-form-item prop="username">
						<el-input
							v-model="form.username"
							placeholder="账号"
							:prefix-icon="'User'"
						/>
					</el-form-item>
					<el-form-item prop="password">
						<el-input
							v-model="form.password"
							type="password"
							show-password
							placeholder="密码"
							:prefix-icon="'Lock'"
						/>
					</el-form-item>
					<div class="form-row">
						<el-checkbox v-model="form.remember">记住账号</el-checkbox>
						<el-link type="primary" :underline="false" @click="forgot"
							>忘记密码?</el-link
						>
					</div>
					<el-button type="primary" class="submit" :loading="loading" @click="submit">
						登 录
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

/* ===== 左侧浅色插画区 ===== */
.brand-pane {
	flex: 1.4;
	min-width: 0;
	position: relative;
	overflow: hidden;
	padding: 36px 48px;
	display: flex;
	flex-direction: column;
	background:
		radial-gradient(900px 500px at 20% 10%, #eaf1fb 0%, transparent 60%),
		radial-gradient(700px 460px at 85% 90%, #eef0fb 0%, transparent 55%),
		linear-gradient(160deg, #f8fafd 0%, #f1f4f9 100%);
}
.brand-pane::before {
	content: '';
	position: absolute;
	inset: 0;
	background-image: radial-gradient(rgba(31, 56, 88, 0.055) 1px, transparent 1px);
	background-size: 22px 22px;
	pointer-events: none;
}

.brand-logo {
	display: flex;
	align-items: center;
	gap: 10px;
	position: relative;
	z-index: 1;
}
.logo-icon {
	width: 36px;
	height: 36px;
	border-radius: 9px;
	background: #3370ff;
	color: #fff;
	display: flex;
	align-items: center;
	justify-content: center;
	flex-shrink: 0;
}
.brand-name {
	font-size: 16px;
	font-weight: 700;
	line-height: 1.2;
	color: #1f2329;
}
.brand-sub {
	font-size: 10px;
	color: #9aa3af;
	letter-spacing: 0.14em;
	margin-top: 2px;
}

.brand-center {
	flex: 1;
	display: flex;
	flex-direction: column;
	align-items: center;
	justify-content: center;
	text-align: center;
	position: relative;
	z-index: 1;
}

/* --- 纯 CSS 插画 --- */
.illus {
	position: relative;
	width: 320px;
	height: 230px;
	margin-bottom: 34px;
}
.illus-card {
	background: #fff;
	border-radius: 14px;
	box-shadow: 0 18px 40px rgba(31, 56, 88, 0.1);
}
.illus-card.main {
	position: absolute;
	left: 50%;
	top: 50%;
	transform: translate(-50%, -50%);
	width: 190px;
	height: 150px;
	padding: 18px 16px;
	z-index: 2;
}
.illus-card.back {
	position: absolute;
	width: 130px;
	height: 96px;
	padding: 14px;
	opacity: 0.65;
	z-index: 1;
}
.illus-card.back.left {
	left: 0;
	bottom: 8px;
	transform: rotate(-5deg);
}
.ic-line {
	height: 7px;
	border-radius: 4px;
	background: #e8ecf3;
	margin-bottom: 9px;
}
.ic-line.w80 {
	width: 80%;
}
.ic-line.w60 {
	width: 60%;
}
.ic-line.w45 {
	width: 45%;
}
.ic-line.w50 {
	width: 50%;
}
.ic-line.w70 {
	width: 70%;
}
.ic-chart {
	position: absolute;
	right: 16px;
	bottom: 14px;
	display: flex;
	align-items: flex-end;
	gap: 6px;
	height: 46px;
}
.ic-chart span {
	width: 12px;
	border-radius: 4px 4px 2px 2px;
	background: linear-gradient(180deg, #7fa6ff 0%, #4c82f7 100%);
}
.ic-chart span:nth-child(2n) {
	background: linear-gradient(180deg, #b7cffd 0%, #8fb2fa 100%);
}
.illus-badge {
	position: absolute;
	background: #fff;
	border-radius: 10px;
	box-shadow: 0 10px 26px rgba(31, 56, 88, 0.12);
	font-size: 12px;
	color: #3d4757;
	padding: 8px 12px;
	white-space: nowrap;
}
.illus-badge.pass {
	right: 6px;
	top: 18px;
	color: #0f9d6c;
}
.illus-badge.doc {
	left: 2px;
	top: 4px;
}
.illus-dots {
	position: absolute;
	right: 22px;
	bottom: 4px;
	width: 54px;
	height: 12px;
	background-image: radial-gradient(#c8d4e6 2.5px, transparent 2.5px);
	background-size: 12px 12px;
}

.brand-slogan {
	font-size: 26px;
	font-weight: 700;
	line-height: 1.4;
	color: #1f2329;
	margin-bottom: 10px;
}
.brand-desc {
	font-size: 13px;
	color: #8a9099;
}

.brand-feats {
	display: flex;
	justify-content: center;
	gap: 26px;
	font-size: 12px;
	color: #9aa3af;
	position: relative;
	z-index: 1;
}

/* ===== 右侧白色登录面板 ===== */
.form-pane {
	width: 460px;
	flex-shrink: 0;
	background: #fff;
	position: relative;
	display: flex;
	align-items: center;
	justify-content: center;
	box-shadow: -1px 0 0 #eef0f4;
}
.form-inner {
	width: 316px;
}
.form-title {
	font-size: 24px;
	font-weight: 700;
	color: #1f2329;
	margin-bottom: 8px;
}
.form-sub {
	font-size: 13px;
	color: #8a9099;
	margin-bottom: 28px;
}
.form-row {
	display: flex;
	align-items: center;
	justify-content: space-between;
	margin: 2px 0 20px;
}
.submit {
	width: 100%;
	height: 44px;
	font-size: 15px;
	border-radius: 8px;
}

.test-account {
	margin-top: 22px;
	border: 1px solid #eef0f4;
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
	color: #8a9099;
	width: 48px;
	flex-shrink: 0;
}
.ta-value {
	font-size: 12px;
	color: #1f2329;
	font-family: Consolas, Monaco, monospace;
}

.form-foot {
	position: absolute;
	bottom: 20px;
	left: 0;
	right: 0;
	text-align: center;
	font-size: 11px;
	color: #b3bac4;
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
