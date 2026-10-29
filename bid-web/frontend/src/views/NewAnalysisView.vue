<script setup>
import { ref, computed } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { useAnalysisStore } from '../store/analysis';
import { uploadFile } from '../api';

const router = useRouter();
const store = useAnalysisStore();

const uploading = ref(false);
const uploadRef = ref();

const done = computed(() => store.steps.every((s) => s.status === 'success'));

const ocrPages = computed(() => store.result?.points?.summary?.OCR页数 ?? 0);
const totalPages = computed(() => store.result?.points?.summary?.总页数 ?? 0);

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
						上传后将自动执行：要点解析 → 风险扫描 → 生成投标初稿
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
							<div class="stat-label">初稿章节</div>
							<div class="sum-num green">
								{{ store.result.generate?.sections ?? 0 }}
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
	grid-template-columns: repeat(4, 1fr);
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
</style>
