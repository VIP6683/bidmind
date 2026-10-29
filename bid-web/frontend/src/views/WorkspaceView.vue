<script setup>
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useWorkspaceStore } from '../store/workspace';
import HeroCard from '../components/HeroCard.vue';
import StatCards from '../components/StatCards.vue';

const router = useRouter();
const ws = useWorkspaceStore();

onMounted(() => ws.fetchAll());

const riskWarnings = () =>
	[...ws.history]
		.filter((h) => h.critical)
		.sort((a, b) => b.critical - a.critical)
		.slice(0, 5);
</script>

<template>
	<div class="workspace" v-loading="ws.loading">
		<HeroCard :stats="ws.stats" />
		<StatCards :stats="ws.stats" style="margin-top: 16px" />

		<div class="bottom-grid">
			<!-- 最近分析 -->
			<div class="panel">
				<div class="panel-title">
					<div>
						最近分析
						<div class="text-muted" style="font-weight: 400; margin-top: 2px">
							这里是你最近处理过的文件
						</div>
					</div>
					<router-link to="/history">全部记录 ›</router-link>
				</div>

				<el-empty
					v-if="!ws.history.length"
					description="暂无分析记录，点击右上角「新建分析」开始"
					:image-size="90"
				/>
				<div v-else class="list">
					<div
						v-for="h in ws.history.slice(0, 6)"
						:key="h.sid"
						class="row"
						@click="router.push(`/analysis/${h.sid}`)"
					>
						<div class="file-icon">
							<el-icon :size="16"><Document /></el-icon>
						</div>
						<div class="row-main">
							<div class="row-name">{{ h.filename }}</div>
							<div class="text-muted">{{ h.created_at }}</div>
						</div>
						<div class="row-side">
							<div class="text-muted">{{ h.created_at?.slice(5, 16) }}</div>
							<el-tag v-if="h.critical" type="danger" size="small" effect="light"
								>{{ h.critical }} 项</el-tag
							>
							<el-tag
								v-else-if="h.status === 'done'"
								type="success"
								size="small"
								effect="light"
								>已完成</el-tag
							>
							<el-tag v-else type="info" size="small" effect="light">待处理</el-tag>
						</div>
					</div>
				</div>
			</div>

			<!-- 风险预警 -->
			<div class="panel">
				<div class="panel-title">
					<div>
						风险预警
						<div class="text-muted" style="font-weight: 400; margin-top: 2px">
							以下文件存在废标级风险，需重点关注
						</div>
					</div>
				</div>

				<el-empty
					v-if="!riskWarnings().length"
					description="暂无废标级风险预警"
					:image-size="90"
				/>
				<div v-else class="warn-list">
					<div
						v-for="h in riskWarnings()"
						:key="h.sid"
						class="warn-card"
						@click="router.push(`/analysis/${h.sid}`)"
					>
						<div class="warn-head">
							<el-tag type="danger" size="small"
								>{{ h.critical }} 项废标级风险</el-tag
							>
							<span class="text-muted">{{ h.created_at?.slice(5, 16) }}</span>
						</div>
						<div class="warn-name">{{ h.filename }}</div>
						<div class="warn-foot">
							<span class="text-muted"
								>废标占比 {{ ws.stats?.critical_ratio ?? 0 }}% ·
								点击查看风险明细</span
							>
							<el-icon class="arrow"><ArrowRight /></el-icon>
						</div>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>

<style scoped>
.bottom-grid {
	display: grid;
	grid-template-columns: 1.6fr 1fr;
	gap: 14px;
	margin-top: 16px;
}

.list {
	display: flex;
	flex-direction: column;
}
.row {
	display: flex;
	align-items: center;
	gap: 12px;
	padding: 12px 8px;
	border-bottom: 1px solid var(--bg);
	cursor: pointer;
	border-radius: 8px;
}
.row:hover {
	background: var(--bg);
}
.row:last-child {
	border-bottom: none;
}

.file-icon {
	width: 36px;
	height: 36px;
	border-radius: 8px;
	background: var(--primary-soft);
	color: var(--primary);
	display: flex;
	align-items: center;
	justify-content: center;
	flex-shrink: 0;
}
.row-main {
	flex: 1;
	min-width: 0;
}
.row-name {
	font-size: 13px;
	font-weight: 500;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
	margin-bottom: 2px;
}
.row-side {
	display: flex;
	align-items: center;
	gap: 10px;
	flex-shrink: 0;
}

.warn-list {
	display: flex;
	flex-direction: column;
	gap: 10px;
}
.warn-card {
	border: 1px solid var(--border);
	border-left: 3px solid var(--danger);
	border-radius: 8px;
	padding: 12px 14px;
	cursor: pointer;
}
.warn-card:hover {
	background: var(--seal-soft);
	border-color: #e5b8ad;
}
.warn-head {
	display: flex;
	justify-content: space-between;
	align-items: center;
	margin-bottom: 8px;
}
.warn-name {
	font-size: 13px;
	font-weight: 600;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
	margin-bottom: 8px;
}
.warn-foot {
	display: flex;
	justify-content: space-between;
	align-items: center;
}
.arrow {
	color: var(--muted);
}

@media (max-width: 1000px) {
	.bottom-grid {
		grid-template-columns: 1fr;
	}
}
</style>
