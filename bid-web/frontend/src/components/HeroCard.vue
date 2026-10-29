<script setup>
defineProps({
	stats: { type: Object, default: null }
});

function riskLevel(ratio) {
	if (!ratio && ratio !== 0) return '—';
	if (ratio >= 30) return '高';
	if (ratio >= 10) return '中';
	return '低';
}
</script>

<template>
	<div class="hero">
		<div class="hero-left">
			<div class="hero-tag">智能识别 · 合规判断 · 报告生成</div>
			<h1 class="hero-title">让每一份招标文件<br />都经得起推敲。</h1>
			<p class="hero-desc">
				上传 Word、PDF 或
				Markdown，系统将自动提取关键条款、识别废标风险，并生成智能分析报告。
			</p>
			<router-link to="/new">
				<el-button type="primary" round size="large" class="hero-btn"
					>+ 开始分析文件 →</el-button
				>
			</router-link>
		</div>

		<div class="hero-right">
			<div class="ring top">
				<el-progress
					type="dashboard"
					:percentage="stats?.pass_ratio ?? 0"
					:width="92"
					:stroke-width="8"
					color="#8ab6f9"
				>
					<template #default>
						<div class="ring-num">{{ stats?.pass_ratio ?? 0 }}%</div>
						<div class="ring-label">关键条款</div>
					</template>
				</el-progress>
			</div>
			<div class="ring-row">
				<div class="ring-item">
					<el-progress
						type="dashboard"
						:percentage="stats?.critical_ratio ?? 0"
						:width="76"
						:stroke-width="8"
						color="#f56c6c"
					>
						<template #default>
							<div class="ring-num sm">{{ riskLevel(stats?.critical_ratio) }}</div>
							<div class="ring-label">风险评级</div>
						</template>
					</el-progress>
				</div>
				<div class="hero-center">
					<div class="pulse">
						<el-icon :size="26" color="#fff"><DataAnalysis /></el-icon>
					</div>
				</div>
				<div class="ring-item">
					<el-progress
						type="dashboard"
						:percentage="stats?.critical_ratio ?? 0"
						:width="76"
						:stroke-width="8"
						color="#f89898"
					>
						<template #default>
							<div class="ring-num sm">{{ stats?.critical_ratio ?? 0 }}%</div>
							<div class="ring-label">废标占比</div>
						</template>
					</el-progress>
				</div>
			</div>
			<div class="hero-tip text-light">* 支持本地化、可定制、数据全程本地处理</div>
		</div>
	</div>
</template>

<style scoped>
.hero {
	border-radius: 14px;
	background:
		radial-gradient(900px 420px at 85% 120%, rgba(255, 255, 255, 0.1), transparent 60%),
		linear-gradient(150deg, #0a4fbc 0%, #0b57d0 50%, #1d6feb 100%);
	color: #fff;
	padding: 32px 40px;
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 24px;
	overflow: hidden;
}

.hero-tag {
	font-size: 12px;
	opacity: 0.8;
	letter-spacing: 2px;
	margin-bottom: 14px;
}
.hero-title {
	font-size: 34px;
	font-weight: 700;
	line-height: 1.35;
	margin-bottom: 14px;
}
.hero-desc {
	font-size: 13px;
	opacity: 0.85;
	max-width: 460px;
	line-height: 1.8;
	margin-bottom: 22px;
}
.hero-btn {
	background: rgba(255, 255, 255, 0.14);
	border: 1px solid rgba(255, 255, 255, 0.5);
}
.hero-btn:hover {
	background: rgba(255, 255, 255, 0.24);
}

.hero-right {
	flex-shrink: 0;
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 4px;
}
.ring-row {
	display: flex;
	align-items: center;
	gap: 8px;
}
.ring-num {
	font-size: 18px;
	font-weight: 700;
	color: #fff;
}
.ring-num.sm {
	font-size: 15px;
}
.ring-label {
	font-size: 11px;
	color: rgba(255, 255, 255, 0.75);
}

.hero-center {
	width: 56px;
	height: 56px;
	border-radius: 50%;
	background: radial-gradient(circle at 35% 35%, #6aa5f3, #0b57d0);
	box-shadow:
		0 0 0 8px rgba(255, 255, 255, 0.08),
		0 0 24px rgba(138, 182, 249, 0.7);
	display: flex;
	align-items: center;
	justify-content: center;
}

.hero-tip {
	font-size: 11px;
	color: rgba(255, 255, 255, 0.6);
	margin-top: 6px;
}

@media (max-width: 900px) {
	.hero-right {
		display: none;
	}
}
</style>
