<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useAnalysisStore } from '../store/analysis';
import { getLlmConfig } from '../api';

const router = useRouter();
const store = useAnalysisStore();

const uploading = ref(false);
const uploadRef = ref();
const hasLlmKey = ref(false);

onMounted(async () => {
	try {
		const c = await getLlmConfig();
		hasLlmKey.value = !!c.has_key;
	} catch {
		hasLlmKey.value = false;
	}
});

const done = computed(() => store.steps.every((s) => s.status === 'success'));

const ocrPages = computed(() => store.result?.points?.summary?.OCR页数 ?? 0);
const totalPages = computed(() => store.result?.points?.summary?.总页数 ?? 0);
const agentWarning = computed(
	() => store.result?.points?.agent?.warning || store.result?.points?.points?.智能体?.warning || ''
);
const outlineChapters = computed(
	() => store.result?.points?.outline?.chapters || store.result?.points?.summary?.大纲章节 || 0
);
const agentMode = computed(() => store.result?.points?.agent?.mode);

async function onFile(file) {
	const f = file.raw || file;
	if (!/\.(pdf|docx|md|txt)$/i.test(f.name)) {
		ElMessage.error('仅支持 PDF / Word(docx) / Markdown / TXT 格式');
		return;
	}
	uploading.value = true;
	const result = await store.run(f);
	uploading.value = false;
	if (result) {
		ElMessage.success('分析完成');
	}
}

function onChange(file) {
	if (file?.raw && !store.running && !uploading.value) onFile(file);
}
</script>

<template>
	<div class="new-analysis">
		<div
			class="panel"
			v-loading="uploading || store.running"
			element-loading-text="正在处理，请稍候…"
		>
			<div class="panel-title">新建分析</div>
			<el-alert
				v-if="!hasLlmKey && !store.sid"
				title="尚未配置 AI Key：仍可分析，但大纲主要靠本地规则，偏差会较大。请先到「AI模型配置」填写后重新上传。"
				type="warning"
				show-icon
				:closable="false"
				style="margin-bottom: 16px"
			/>

			<!-- 上传区 -->
			<el-upload
				v-if="!store.sid"
				ref="uploadRef"
				drag
				accept=".pdf,.docx,.md,.txt"
				:auto-upload="false"
				:show-file-list="false"
				:on-change="onChange"
				class="dropzone"
			>
				<div class="dz-inner">
					<el-icon :size="42" color="#0b57d0"><UploadFilled /></el-icon>
					<div class="dz-title">拖拽文件到此处，或 <em>点击选择文件</em></div>
					<div class="text-muted">
						支持 PDF / Word(docx) / Markdown / TXT，单文件最大 50MB
					</div>
					<div class="text-muted" style="margin-top: 4px">
						上传后智能体自动执行：本地规则抽取 + 模型起草大纲（规则验收缺口会回修）→
						风险扫描 → 初稿骨架
					</div>
				</div>
			</el-upload>

			<!-- 流程进度 -->
			<template v-if="store.sid">
				<div class="file-chip">
					<el-icon><Document /></el-icon>
					<span class="fname">{{ store.filename }}</span>
					<el-tag size="small" type="info">会话 {{ store.sid }}</el-tag>
				</div>

				<el-steps
					:active="store.current"
					align-center
					class="steps"
					:process-status="store.error ? 'error' : 'process'"
				>
					<el-step
						v-for="s in store.steps"
						:key="s.key"
						:title="s.label"
						:status="s.status === 'wait' && store.current > -1 ? undefined : s.status"
						:icon="undefined"
					/>
				</el-steps>

				<el-alert
					v-if="store.error"
					:title="store.error"
					type="error"
					show-icon
					:closable="false"
					style="margin: 18px 0"
				/>
				<el-alert
					v-if="agentWarning"
					:title="agentWarning"
					type="warning"
					show-icon
					:closable="false"
					style="margin: 18px 0"
				/>

				<el-alert
					v-if="ocrPages > 0"
					:title="`已启用本地 OCR 识别（${ocrPages}/${totalPages} 页为扫描页，识别耗时较长属正常）`"
					type="info"
					show-icon
					:closable="false"
					style="margin: 18px 0"
				/>

				<!-- 完成摘要 -->
				<template v-if="done && store.result">
					<div class="summary-grid">
						<div class="panel sum">
							<div class="stat-label">废标红线</div>
							<div class="sum-num red">
								{{ store.result.points?.summary?.废标红线 ?? 0 }}
							</div>
						</div>
						<div class="panel sum">
							<div class="stat-label">评分项</div>
							<div class="sum-num">
								{{ store.result.points?.summary?.评分项 ?? 0 }}
							</div>
						</div>
						<div class="panel sum">
							<div class="stat-label">识别风险</div>
							<div class="sum-num orange">{{ store.result.review?.total ?? 0 }}</div>
						</div>
						<div class="panel sum">
							<div class="stat-label">大纲章节</div>
							<div class="sum-num green">{{ outlineChapters }}</div>
						</div>
						<div class="panel sum">
							<div class="stat-label">智能体</div>
							<div class="sum-num" style="font-size: 16px; padding-top: 8px">
								{{ agentMode === 'hybrid' ? '模型+规则' : '仅规则' }}
							</div>
						</div>
					</div>
				</template>

				<div class="actions">
					<el-button
						v-if="done"
						type="primary"
						@click="router.push(`/analysis/${store.sid}`)"
					>
						查看分析报告 →
					</el-button>
					<el-button
						v-if="done || store.error"
						@click="
							store.reset();
							store.sid = '';
						"
						>再分析一个文件</el-button
					>
				</div>
			</template>
		</div>
	</div>
</template>

<style scoped>
.dropzone :deep(.el-upload-dragger) {
	padding: 48px 24px;
	border-radius: 12px;
}
.dz-inner {
	display: flex;
	flex-direction: column;
	align-items: center;
	gap: 8px;
}
.dz-title {
	font-size: 15px;
	color: var(--text);
}
.dz-title em {
	color: var(--primary);
	font-style: normal;
}

.file-chip {
	display: flex;
	align-items: center;
	gap: 8px;
	background: var(--primary-light);
	border-radius: 8px;
	padding: 10px 14px;
	color: var(--primary-deep);
	font-size: 13px;
	margin-bottom: 22px;
}
.fname {
	font-weight: 600;
}

.steps {
	margin: 10px 0 22px;
}

.summary-grid {
	display: grid;
	grid-template-columns: repeat(5, 1fr);
	gap: 12px;
	margin-top: 6px;
}
.sum {
	padding: 14px 18px;
}
.stat-label {
	font-size: 12px;
	color: var(--muted);
	margin-bottom: 6px;
}
.sum-num {
	font-size: 26px;
	font-weight: 700;
}
.sum-num.red {
	color: var(--danger);
}
.sum-num.orange {
	color: var(--warning);
}
.sum-num.green {
	color: var(--success);
}

.actions {
	margin-top: 22px;
	display: flex;
	justify-content: center;
	gap: 10px;
}

@media (max-width: 1100px) {
	.summary-grid {
		grid-template-columns: repeat(2, 1fr);
	}
}
</style>
