<script setup>
defineProps({
	items: { type: Array, default: () => [] }
});

function levelOf(cat) {
	const c = String(cat || '');
	if (c.startsWith('🔴')) return { label: '高风险', tag: 'danger' };
	if (c.startsWith('🟡')) return { label: '中风险', tag: 'warning' };
	return { label: '提示', tag: 'info' };
}
</script>

<template>
	<div class="risk-list">
		<el-empty v-if="!items.length" description="未识别到风险条款" :image-size="80" />
		<el-collapse v-else accordion>
			<el-collapse-item v-for="(it, i) in items" :key="i" :name="i">
				<template #title>
					<div class="risk-head">
						<el-tag :type="levelOf(it.类别).tag" size="small" effect="dark">{{
							levelOf(it.类别).label
						}}</el-tag>
						<el-tag size="small" effect="plain" type="info">{{ it.类别 }}</el-tag>
						<span class="risk-word">{{ it.命中词 }}</span>
						<span class="risk-ctx text-muted">{{ it.上下文 }}</span>
						<span class="risk-line text-muted">L{{ it.行号 }}</span>
					</div>
				</template>
				<div class="risk-body">
					<div class="suggestion">
						<div class="sug-title">建议处理</div>
						<div class="sug-text">
							定位原文第 {{ it.行号 }} 行「{{
								it.命中词
							}}」相关条款，逐字核对招标文件要求：
							属于废标级（🔴）条款必须在投标文件中逐条明确响应并承诺满足；评分/格式类（🟡）条款
							按评分标准逐项对照响应，避免负偏离；确认无法满足的条款应在答疑期提出澄清。
						</div>
					</div>
					<div class="ctx-full">条款上下文：{{ it.上下文 }}</div>
				</div>
			</el-collapse-item>
		</el-collapse>
	</div>
</template>

<style scoped>
.risk-head {
	display: flex;
	align-items: center;
	gap: 8px;
	min-width: 0;
	flex: 1;
	padding-right: 12px;
}
.risk-word {
	font-size: 13px;
	font-weight: 600;
	flex-shrink: 0;
}
.risk-ctx {
	flex: 1;
	min-width: 0;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}
.risk-line {
	flex-shrink: 0;
}

.risk-body {
	padding: 0 4px;
}
.suggestion {
	background: var(--primary-soft);
	border-left: 3px solid var(--primary);
	border-radius: 6px;
	padding: 10px 14px;
	margin-bottom: 10px;
}
.sug-title {
	font-size: 12px;
	font-weight: 700;
	color: var(--primary-deep);
	margin-bottom: 4px;
}
.sug-text {
	font-size: 13px;
	line-height: 1.8;
	color: var(--text);
}
.ctx-full {
	font-size: 12px;
	color: var(--muted);
	line-height: 1.7;
}
</style>
