<script setup>
import { onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useWorkspaceStore } from '../store/workspace';

const router = useRouter();
const ws = useWorkspaceStore();

onMounted(() => ws.fetchAll());

function statusTag(status) {
	const map = {
		uploaded: { label: '待处理', type: 'info' },
		parsed: { label: '已解析', type: 'warning' },
		reviewed: { label: '已审标', type: 'warning' },
		done: { label: '已完成', type: 'success' }
	};
	return map[status] || { label: status, type: 'info' };
}

async function onDelete(row) {
	await ElMessageBox.confirm(
		`确定删除「${row.filename}」的全部分析记录与产物文件吗？此操作不可恢复。`,
		'删除确认',
		{ type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
	);
	await ws.remove(row.sid);
	ElMessage.success('已删除');
}
</script>

<template>
	<div class="panel" v-loading="ws.loading">
		<div class="panel-title">
			分析记录
			<span class="text-muted" style="font-weight: 400">共 {{ ws.history.length }} 条</span>
		</div>

		<el-table
			:data="ws.history"
			style="width: 100%"
			empty-text="暂无记录，前往「新建分析」上传招标文件"
		>
			<el-table-column label="文件名" min-width="280">
				<template #default="{ row }">
					<div class="file-cell">
						<div class="file-icon">
							<el-icon :size="15"><Document /></el-icon>
						</div>
						<div>
							<div class="fname">{{ row.filename }}</div>
							<div class="text-muted">{{ row.created_at }}</div>
						</div>
					</div>
				</template>
			</el-table-column>
			<el-table-column label="状态" width="100">
				<template #default="{ row }">
					<el-tag size="small" :type="statusTag(row.status).type" effect="light">{{
						statusTag(row.status).label
					}}</el-tag>
				</template>
			</el-table-column>
			<el-table-column label="风险条款" width="110" align="center">
				<template #default="{ row }">
					<span v-if="row.risk_total != null" :class="{ red: row.critical > 0 }"
						>{{ row.risk_total }} 项</span
					>
					<span v-else class="text-muted">—</span>
				</template>
			</el-table-column>
			<el-table-column label="其中废标级" width="110" align="center">
				<template #default="{ row }">
					<el-tag v-if="row.critical" size="small" type="danger" effect="light"
						>{{ row.critical }} 项</el-tag
					>
					<span v-else class="text-muted">—</span>
				</template>
			</el-table-column>
			<el-table-column label="风险评分" width="100" align="center">
				<template #default="{ row }">
					<span v-if="row.score != null" class="score">{{ row.score }}</span>
					<span v-else class="text-muted">—</span>
				</template>
			</el-table-column>
			<el-table-column label="耗时" width="90" align="center">
				<template #default="{ row }">
					<span v-if="row.duration_sec" class="text-muted">{{ row.duration_sec }}s</span>
					<span v-else class="text-muted">—</span>
				</template>
			</el-table-column>
			<el-table-column label="操作" width="150" align="center" fixed="right">
				<template #default="{ row }">
					<el-button
						link
						type="primary"
						size="small"
						@click="router.push(`/analysis/${row.sid}`)"
						>查看详情</el-button
					>
					<el-button link type="danger" size="small" @click="onDelete(row)"
						>删除</el-button
					>
				</template>
			</el-table-column>
		</el-table>
	</div>
</template>

<style scoped>
.file-cell {
	display: flex;
	align-items: center;
	gap: 10px;
}
.file-icon {
	width: 32px;
	height: 32px;
	border-radius: 8px;
	background: var(--primary-soft);
	color: var(--primary);
	display: flex;
	align-items: center;
	justify-content: center;
	flex-shrink: 0;
}
.fname {
	font-size: 13px;
	font-weight: 500;
	max-width: 380px;
	white-space: nowrap;
	overflow: hidden;
	text-overflow: ellipsis;
}
.red {
	color: var(--danger);
	font-weight: 600;
}
.score {
	font-weight: 700;
}
</style>
