<script setup>
defineProps({
	stats: { type: Object, default: null }
});
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
	</div>
</template>

<style scoped>
.hero {
	position: relative;
	border-radius: 14px;
	border: 1px solid #eef0f4;
	background:
		radial-gradient(900px 420px at 88% -20%, #e8f0fd 0%, transparent 55%),
		radial-gradient(700px 380px at -10% 120%, #eef0fb 0%, transparent 50%),
		linear-gradient(150deg, #fafbfd 0%, #f2f5fa 100%);
	color: #1f2329;
	padding: 34px 40px;
	overflow: hidden;
}
.hero::before {
	content: '';
	position: absolute;
	inset: 0;
	background-image: radial-gradient(rgba(31, 56, 88, 0.05) 1px, transparent 1px);
	background-size: 22px 22px;
	pointer-events: none;
}

.hero-left {
	position: relative;
	z-index: 1;
}
.hero-tag {
	font-size: 12px;
	color: #8a9099;
	letter-spacing: 2px;
	margin-bottom: 14px;
}
.hero-title {
	font-size: 30px;
	font-weight: 700;
	line-height: 1.35;
	margin-bottom: 14px;
	color: #1f2329;
}
.hero-desc {
	font-size: 13px;
	color: #8a9099;
	max-width: 520px;
	line-height: 1.8;
	margin-bottom: 22px;
}
.hero-btn {
	box-shadow: 0 8px 20px rgba(51, 112, 255, 0.28);
}
</style>
