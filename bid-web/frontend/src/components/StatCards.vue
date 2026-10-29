<script setup>
defineProps({
	stats: { type: Object, default: null }
});

function fmtDuration(sec) {
	if (!sec) return '0秒';
	if (sec < 60) return `${sec}秒`;
	const m = Math.floor(sec / 60);
	const s = Math.round(sec % 60);
	return `${m}分${s}秒`;
}
</script>

<template>
	<div class="stat-grid">
		<div class="panel stat">
			<div class="stat-label">待办分析文件</div>
			<div class="stat-value blue">
				{{ stats ? Math.max(0, stats.total_files - stats.report_count) : '—' }}
			</div>
			<div class="stat-foot text-muted">数据来自本机计算任务</div>
		</div>
		<div class="panel stat">
			<div class="stat-label">识别风险条款</div>
			<div class="stat-value red">{{ stats?.risk_total ?? '—' }}</div>
			<div class="stat-foot text-muted">全部历史文件汇总</div>
		</div>
		<div class="panel stat">
			<div class="stat-label">平均处理时长</div>
			<div class="stat-value green">
				{{ fmtDuration(stats?.avg_duration_sec) }}
			</div>
			<div class="stat-foot text-muted">单文件全流程均值</div>
		</div>
		<div class="panel stat">
			<div class="stat-label">生成投标文档</div>
			<div class="stat-value">{{ stats?.report_count ?? '—' }}</div>
			<div class="stat-foot text-muted">累计生成文档数</div>
		</div>
	</div>
</template>

<style scoped>
.stat-grid {
	display: grid;
	grid-template-columns: repeat(4, 1fr);
	gap: 14px;
}

.stat {
	padding: 18px 20px;
}
.stat-label {
	font-size: 12px;
	color: var(--muted);
	margin-bottom: 10px;
}
.stat-value {
	font-size: 26px;
	font-weight: 700;
	line-height: 1.2;
	color: var(--ink);
}
.stat-value.blue {
	color: var(--primary);
}
.stat-value.red {
	color: var(--danger);
}
.stat-value.green {
	color: var(--jade);
}
.stat-foot {
	margin-top: 8px;
}

@media (max-width: 1000px) {
	.stat-grid {
		grid-template-columns: repeat(2, 1fr);
	}
}
</style>
