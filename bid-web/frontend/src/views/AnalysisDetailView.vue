<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { marked } from 'marked';
import { renderAsync } from 'docx-preview';
import { getHistoryDetail, deleteHistory, saveDraft, generateDraft, getLlmConfig } from '../api';
import ChapterTree from '../components/ChapterTree.vue';
import RiskList from '../components/RiskList.vue';

const route = useRoute();
const router = useRouter();
const sid = route.params.sid;

const loading = ref(true);
const detail = ref(null);
const activeTab = ref('summary');
const draftText = ref('');
const draftDirty = ref(false);
const saving = ref(false);
const regenerating = ref(false);
const hasLlmKey = ref(false); // 是否配置了 AI Key，决定重新生成的文案与模式
const editMode = ref('edit'); // 初稿编辑 tab：edit=源码编辑 / preview=渲染预览

// 重新生成初稿：仅本地模板骨架，不调用 AI
async function onRegenerate() {
	await ElMessageBox.confirm(
		'将按本地模板与企业素材重新生成初稿骨架并覆盖当前内容（不调用 AI），继续吗？',
		'重新生成初稿',
		{ type: 'warning', confirmButtonText: '开始生成', cancelButtonText: '取消' }
	);
	regenerating.value = true;
	try {
		const r = await generateDraft(sid);
		ElMessage.success(`初稿已生成（本地模板模式）：${r.bid_type}，共 ${r.sections} 个章节`);
		if (r.checklist_added?.length) {
			ElMessage.info(
				`已按招标文件「投标文件组成」补充 ${r.checklist_added.length} 个章节：${r.checklist_added.join('、')}`,
				{ duration: 6000 }
			);
		}
		await load();
	} finally {
		regenerating.value = false;
	}
}

// AI 生成：显式点击才调用 AI 润色技术标章节（消耗 API 额度）
async function onAiGenerate() {
	if (!hasLlmKey.value) {
		ElMessage.warning('尚未配置 AI Key，请先在「AI模型配置」中填写，或使用本地模板生成');
		return;
	}
	await ElMessageBox.confirm(
		'将调用 AI 逐章节撰写/润色技术标正文并覆盖当前内容（商务标为固定模板格式，约1-3分钟，消耗 API 额度），继续吗？',
		'AI生成',
		{ type: 'warning', confirmButtonText: 'AI生成', cancelButtonText: '取消' }
	);
	regenerating.value = true;
	try {
		const r = await generateDraft(sid, true);
		if (r.llm_used) {
			ElMessage.success(
				`AI初稿已生成：${r.bid_type}，${r.llm_sections_ok}/${r.tech_sections} 章节AI撰写`
			);
		} else {
			ElMessage.warning('AI 未生效，已回退本地模板骨架，请检查 AI模型配置');
		}
		if (r.checklist_added?.length) {
			ElMessage.info(
				`已按招标文件「投标文件组成」补充 ${r.checklist_added.length} 个章节：${r.checklist_added.join('、')}`,
				{ duration: 6000 }
			);
		}
		await load();
	} finally {
		regenerating.value = false;
	}
}

onMounted(load);

async function load() {
	loading.value = true;
	try {
		detail.value = await getHistoryDetail(sid);
		draftText.value = detail.value.draft || '';
		getLlmConfig()
			.then((c) => (hasLlmKey.value = !!c.has_key))
			.catch(() => {});
	} finally {
		loading.value = false;
	}
}

const score = computed(() => detail.value?.score ?? null);

const overviewText = computed(() => {
	if (score.value == null) return '尚未完成审标扫描，暂无综合评估。';
	const c = detail.value.critical ?? 0;
	if (c > 0)
		return `招标文件中存在 ${c} 处废标级（🔴）风险条款与 ${detail.value.risk_total} 处风险提示，均为招标方设置的门槛或陷阱。投标时须逐条核对并优先规避废标项，否则投标将被否决。`;
	return `招标文件中未发现废标级风险，共 ${detail.value.risk_total} 处风险提示，建议投标文件按评分标准逐项响应以提升竞争力。`;
});

const reviewItems = computed(() => detail.value?.review?.items || []);

// 废标自查：未覆盖的排最前
const dqItems = computed(() => {
	const items = detail.value?.dq_check?.items || [];
	return [...items].sort(
		(a, b) => (a.status === 'missing' ? 0 : 1) - (b.status === 'missing' ? 0 : 1)
	);
});

// 保证金兼容：后端可能返回 dict（投标/履约/缴纳方式/退还）或字符串
const depositItems = computed(() => {
	const d = detail.value?.points?.保证金;
	if (!d) return [];
	if (typeof d === 'string') return d.trim() ? [{ k: '保证金', v: d.trim() }] : [];
	return Object.entries(d).map(([k, v]) => ({ k, v: String(v) }));
});

// PDF/Word 原文走 iframe 内嵌预览（docx 用 docx-preview 渲染进 iframe），其余格式走文本对照
const isPdfSrc = computed(() => (detail.value?.filename || '').toLowerCase().endsWith('.pdf'));
const isDocxSrc = computed(() => /\.(docx|docm)$/i.test(detail.value?.filename || ''));
const sourceUrl = computed(() => `/api/source/${sid}`);

// docx → iframe 渲染（保留 Word 排版），仅在首次切到「原文对照」时加载一次
const docxFrame = ref(null);
let docxRendered = '';
watch(
	() => [activeTab.value, detail.value, isDocxSrc.value],
	async ([tab, det, isDocx]) => {
		if (tab !== 'source' || !isDocx || !det) return;
		if (docxRendered === sid) return;
		await nextTick();
		const frame = docxFrame.value;
		if (!frame) return;
		try {
			const res = await fetch(sourceUrl.value);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const buf = await res.arrayBuffer();
			const doc = frame.contentDocument;
			doc.open();
			doc.write(
				'<!DOCTYPE html><html><head><meta charset="utf-8"><style>' +
					'body{margin:0;background:#e8eaed;overflow:auto}' +
					'</style></head><body></body></html>'
			);
			doc.close();
			await renderAsync(buf, doc.body, doc.head, {
				className: 'docx',
				inWrapper: true,
				breakPages: true,
				ignoreLastRenderedPageBreak: false,
				experimental: true,
				useBase64URL: true
			});
			docxRendered = sid;
		} catch (e) {
			console.error('docx 预览失败', e);
			docxRendered = '';
			doc.body.innerHTML =
				'<p style="padding:16px;color:#909399">Word 原文预览加载失败，请改用下方文本对照。</p>';
		}
	},
	{ immediate: true }
);

// 初稿 markdown 渲染（对照页用已保存内容，编辑页用当前输入）
const draftHtml = computed(() => marked.parse(detail.value?.draft || ''));
const draftEditHtml = computed(() => marked.parse(draftText.value || ''));

// 原文对照仅渲染前 4 万字符，避免超长文档拖垮页面
const LIMIT = 40000;
const srcPreview = computed(() => (detail.value?.src_md || '').slice(0, LIMIT));

async function saveDraftText() {
	saving.value = true;
	try {
		await saveDraft(sid, draftText.value);
		draftDirty.value = false;
		ElMessage.success('初稿已保存');
	} finally {
		saving.value = false;
	}
}

async function onDelete() {
	await ElMessageBox.confirm('确定删除本条分析记录与全部产物文件吗？', '删除确认', {
		type: 'warning',
		confirmButtonText: '删除',
		cancelButtonText: '取消'
	});
	await deleteHistory(sid);
	ElMessage.success('已删除');
	router.push('/history');
}
</script>

<template>
	<div class="detail" v-loading="loading">
		<template v-if="detail">
			<!-- 顶部标题栏 -->
			<div class="head">
				<div class="head-main">
					<el-button link @click="router.push('/history')"
						><el-icon><ArrowLeft /></el-icon> 返回记录</el-button
					>
					<h2 class="title">{{ detail.filename }}</h2>
					<div class="meta text-muted">
						<span>编号 TDR-{{ detail.sid }}</span>
						<span v-if="detail.created_at">{{ detail.created_at }}</span>
						<el-tag v-if="detail.critical" type="danger" size="small" effect="light"
							>废标级 {{ detail.critical }} 项</el-tag
						>
					</div>
				</div>
				<div class="head-actions">
					<el-button @click="onDelete">删除</el-button>
					<el-button type="primary" @click="router.push(`/new`)">新建分析</el-button>
					<a v-if="detail.download_draft" :href="detail.download_draft">
						<el-button type="primary" plain
							><el-icon style="margin-right: 4px"><Download /></el-icon
							>下载初稿</el-button
						>
					</a>
					<a v-if="detail.download_draft" :href="`/api/export/${sid}`">
						<el-button type="success" plain
							><el-icon style="margin-right: 4px"><Document /></el-icon
							>下载Word</el-button
						>
					</a>
				</div>
			</div>

			<!-- Tabs -->
			<el-tabs v-model="activeTab" class="tabs">
				<!-- 智能摘要：招标文件原文要点 -->
				<el-tab-pane label="智能摘要" name="summary">
					<div class="col-stack">
						<!-- 项目关键信息 -->
						<div class="panel">
							<div class="panel-title">项目关键信息（提取自招标文件原文）</div>
							<div class="info-grid">
								<div class="info-item">
									<div class="text-muted">预算金额</div>
									<div v-if="detail.points?.预算?.length" class="info-value">
										<div v-for="(b, i) in detail.points.预算" :key="i">
											{{ b }}
										</div>
									</div>
									<div v-else class="info-value text-muted">未识别</div>
								</div>
								<div class="info-item">
									<div class="text-muted">保证金</div>
									<div v-if="depositItems.length" class="info-value">
										<div v-for="(d, i) in depositItems" :key="i">
											{{ d.k }}：{{ d.v }}
										</div>
									</div>
									<div v-else class="info-value text-muted">未识别</div>
								</div>
								<div class="info-item">
									<div class="text-muted">评分总分</div>
									<div class="info-value">
										{{ detail.points?.评分总分 ?? '—' }} 分
									</div>
								</div>
							</div>
							<div v-if="detail.points?.时间节点?.length" class="timeline">
								<span
									v-for="(t, i) in detail.points.时间节点"
									:key="i"
									class="tl-item"
								>
									<b>{{ t.事件 }}</b
									>：{{ t.时间 }}
								</span>
							</div>
						</div>

						<!-- 资质要求 -->
						<div class="panel" v-if="detail.points?.资质要求?.length">
							<div class="panel-title">资质要求（资格门槛）</div>
							<div
								class="qual-item"
								v-for="(q, i) in detail.points.资质要求"
								:key="i"
							>
								<el-tag
									v-if="q.type"
									:type="q.type.includes('必') ? 'danger' : 'info'"
									size="small"
									effect="light"
									>{{ q.type }}</el-tag
								>
								<span class="qual-req">{{ q.requirement }}</span>
								<span v-if="q.certificate" class="qual-cert text-muted"
									>所需证书：{{ q.certificate }}</span
								>
							</div>
						</div>

						<!-- 评分项明细 -->
						<div class="panel" v-if="detail.points?.评分项?.length">
							<div class="panel-title">
								<span
									>评分项明细
									<span class="text-muted" style="font-weight: 400"
										>共 {{ detail.points.评分项.length }} 项，总分
										{{ detail.points?.评分总分 ?? '—' }} 分</span
									>
									<el-tag
										v-if="detail.points?.评分校验?.声明总分"
										:type="
											detail.points.评分校验.校验 === 'pass'
												? 'success'
												: 'warning'
										"
										size="small"
										style="margin-left: 8px"
										>声明 {{ detail.points.评分校验.声明总分 }} 分 ·
										{{
											detail.points.评分校验.校验 === 'pass'
												? '校验通过'
												: '差异待复核'
										}}</el-tag
									>
									<el-tag
										v-for="(v, k) in detail.points?.评分分类汇总 || {}"
										:key="k"
										size="small"
										type="info"
										style="margin-left: 8px"
										>{{ k }} {{ v }} 分</el-tag
									>
								</span>
							</div>
							<el-alert
								v-if="detail.points?.评分校验?.需人工复核"
								type="warning"
								:closable="false"
								style="margin-bottom: 10px"
								:title="`评分总分 ${detail.points.评分总分} 与招标声明总分 ${detail.points.评分校验.声明总分 ?? '未知'} 不一致，存在漏提或多提，请人工对照原文复核`"
							/>
							<el-table
								:data="detail.points.评分项"
								size="small"
								border
								style="width: 100%"
							>
								<el-table-column prop="类别" label="类别" width="120" />
								<el-table-column prop="项目" label="评分项目" min-width="160" />
								<el-table-column
									prop="分值"
									label="分值"
									width="70"
									align="center"
								/>
								<el-table-column
									prop="评审标准"
									label="评审标准"
									min-width="280"
									show-overflow-tooltip
								/>
							</el-table>
						</div>

						<!-- 废标红线 -->
						<div class="panel" v-if="detail.points?.废标红线?.length">
							<div class="panel-title">废标红线（招标文件实质性条款）</div>
							<div class="redline" v-for="(r, i) in detail.points.废标红线" :key="i">
								<el-icon color="#e34d59"><WarningFilled /></el-icon>
								<span>{{ r }}</span>
							</div>
						</div>

						<!-- 文件清单 -->
						<div class="panel" v-if="detail.points?.文件清单?.length">
							<div class="panel-title">投标文件组成清单</div>
							<div class="doc-item" v-for="(d, i) in detail.points.文件清单" :key="i">
								{{ d }}
							</div>
						</div>
					</div>
				</el-tab-pane>

				<!-- 审标分析：风险扫描 + 废标自查 -->
				<el-tab-pane label="审标分析" name="review">
					<div class="col-stack">
						<!-- 分析总览 -->
						<div class="panel overview">
							<div class="ov-text">
								<div class="ov-title">分析总览</div>
								<p class="ov-desc">{{ overviewText }}</p>
								<div class="ov-sub text-muted">
									基于废标红线、评分项与风险扫描结果的综合评估（评分 = 100 -
									风险条款数）
								</div>
							</div>
							<el-progress
								type="dashboard"
								:percentage="score ?? 0"
								:width="104"
								:stroke-width="10"
								:color="
									score >= 80 ? '#2ba471' : score >= 50 ? '#e6a23c' : '#e34d59'
								"
							>
								<template #default>
									<div class="score-num" :class="{ low: score < 50 }">
										{{ score ?? '—' }}
									</div>
									<div class="text-muted">风险评分</div>
								</template>
							</el-progress>
						</div>

						<!-- 三统计卡 -->
						<div class="mini-grid">
							<div class="panel mini">
								<div class="text-muted">废标级风险</div>
								<div class="mini-num red">
									{{ detail.critical ?? '—' }} <span class="unit">项</span>
								</div>
								<div class="text-muted">不处理将导致投标无效</div>
							</div>
							<div class="panel mini">
								<div class="text-muted">风险条款</div>
								<div class="mini-num orange">
									{{ detail.risk_total ?? '—' }} <span class="unit">项</span>
								</div>
								<div class="text-muted">评分/格式/合同类提示</div>
							</div>
							<div class="panel mini">
								<div class="text-muted">评分项</div>
								<div class="mini-num">
									{{ detail.points?.评分项?.length ?? '—' }}
									<span class="unit">项</span>
								</div>
								<div class="text-muted">
									总分 {{ detail.points?.评分总分 ?? '—' }} 分
								</div>
							</div>
						</div>

						<!-- 风险条款 -->
						<div class="panel">
							<div class="panel-title">
								<span
									>招标文件风险条款
									<span class="text-muted" style="font-weight: 400"
										>扫描自招标文件原文，非投标文件问题；点击展开应对建议</span
									></span
								>
								<a
									v-if="detail.download_report"
									:href="detail.download_report"
									class="report-a"
								>
									<el-button plain size="small"
										><el-icon style="margin-right: 4px"><Document /></el-icon
										>查看审标报告</el-button
									>
								</a>
							</div>
							<RiskList :items="reviewItems" />
						</div>

						<!-- 废标自查：初稿 vs 废标项逐条对照 -->
						<div
							class="panel"
							v-if="detail.dq_check && detail.dq_check.status === 'ok'"
						>
							<div class="panel-title">
								废标自查（投标初稿逐条对照）
								<el-tag
									:type="detail.dq_check.missing ? 'danger' : 'success'"
									size="small"
									effect="light"
								>
									已响应 {{ detail.dq_check.covered }}/{{ detail.dq_check.total }}
									<template v-if="detail.dq_check.missing"
										>· 未覆盖 {{ detail.dq_check.missing }}</template
									>
								</el-tag>
							</div>
							<div class="dq-item" v-for="(it, i) in dqItems" :key="i">
								<span class="dq-icon">{{
									it.status === 'covered' ? '✅' : '❌'
								}}</span>
								<span class="dq-kw" :class="{ miss: it.status === 'missing' }">{{
									it.keyword
								}}</span>
								<span class="dq-sug text-muted">{{
									it.status === 'covered' ? '初稿已有对应响应' : it.suggestion
								}}</span>
							</div>
						</div>
					</div>
				</el-tab-pane>

				<!-- 原文对照 -->
				<el-tab-pane label="原文对照" name="source">
					<div class="compare">
						<div class="panel cmp">
							<div class="panel-title">招标文件原文</div>
							<iframe
								v-if="isPdfSrc"
								:src="sourceUrl"
								class="pdf-frame"
								title="招标文件原文预览"
							/>
							<iframe
								v-else-if="isDocxSrc"
								ref="docxFrame"
								class="pdf-frame docx-frame"
								title="招标文件原文预览"
							/>
							<el-empty
								v-else-if="!detail.src_md"
								description="该格式暂未生成对照文本"
								:image-size="70"
							/>
							<pre v-else class="doc">{{ srcPreview }}</pre>
						</div>
						<div class="panel cmp">
							<div class="panel-title">投标初稿</div>
							<el-empty
								v-if="!detail.draft"
								description="尚未生成投标初稿"
								:image-size="70"
							/>
							<div v-else class="doc md-view" v-html="draftHtml"></div>
						</div>
					</div>
				</el-tab-pane>

				<!-- 初稿编辑：左章节树 + 右编辑器 -->
				<el-tab-pane name="edit">
					<template #label>
						<span>初稿编辑 <el-badge v-if="draftDirty" is-dot class="dot" /></span>
					</template>
					<div class="edit-layout">
						<div class="side">
							<ChapterTree :markdown="detail.draft || ''" />
						</div>
						<div class="panel edit-panel">
							<div class="panel-title">
								<span
									>投标文件初稿
									<span class="text-muted" style="font-weight: 400"
										>Markdown 源码可编辑，预览模式查看排版效果</span
									></span
								>
								<div>
									<el-radio-group
										v-model="editMode"
										size="small"
										style="margin-right: 8px"
									>
										<el-radio-button value="edit">编辑</el-radio-button>
										<el-radio-button value="preview">预览</el-radio-button>
									</el-radio-group>
									<el-button
										size="small"
										:loading="regenerating"
										@click="onRegenerate"
										>重新生成初稿</el-button
									>
									<el-tooltip
										:content="
											hasLlmKey
												? '调用 AI 撰写技术标正文（消耗 API 额度）'
												: '未配置 AI Key，请先在 AI模型配置 中填写'
										"
										placement="top"
									>
										<el-button
											size="small"
											type="success"
											:loading="regenerating"
											@click="onAiGenerate"
											>AI生成</el-button
										>
									</el-tooltip>
									<el-button
										size="small"
										@click="
											draftText = detail.draft || '';
											draftDirty = false;
										"
										:disabled="!draftDirty"
										>放弃修改</el-button
									>
									<el-button
										size="small"
										type="primary"
										:loading="saving"
										:disabled="!draftDirty"
										@click="saveDraftText"
										>保存修正</el-button
									>
								</div>
							</div>
							<el-input
								v-if="editMode === 'edit'"
								v-model="draftText"
								type="textarea"
								:autosize="{ minRows: 24 }"
								placeholder="暂无初稿内容"
								@input="draftDirty = true"
							/>
							<div v-else class="doc md-view" v-html="draftEditHtml"></div>
							<div class="text-muted edit-tip">
								* 修改后点击「保存修正」写回本地记录；占位符 {如：姓名}
								请在投标前替换为真实数据。
							</div>
						</div>
					</div>
				</el-tab-pane>
			</el-tabs>
		</template>
	</div>
</template>

<style scoped>
.head {
	display: flex;
	justify-content: space-between;
	align-items: flex-start;
	margin-bottom: 6px;
}
.title {
	font-size: 20px;
	font-weight: 700;
	margin: 6px 0;
}
.meta {
	display: flex;
	align-items: center;
	gap: 14px;
}
.head-actions {
	display: flex;
	gap: 8px;
	flex-shrink: 0;
}

.tabs {
	margin-top: 10px;
}

/* 单栏纵向堆叠（智能摘要 / 审标分析共用） */
.col-stack {
	display: flex;
	flex-direction: column;
	gap: 14px;
}

/* 项目关键信息 */
.info-grid {
	display: grid;
	grid-template-columns: repeat(3, 1fr);
	gap: 14px;
}
.info-item {
	padding: 12px 14px;
	background: var(--bg);
	border-radius: 8px;
	font-size: 13px;
}
.info-value {
	margin-top: 6px;
	font-size: 14px;
	font-weight: 600;
	line-height: 1.7;
	word-break: break-all;
}
.timeline {
	margin-top: 12px;
	display: flex;
	flex-wrap: wrap;
	gap: 8px 18px;
	font-size: 13px;
}
.tl-item b {
	color: var(--danger, #e34d59);
}

/* 资质要求 */
.qual-item {
	display: flex;
	gap: 8px;
	align-items: baseline;
	font-size: 13px;
	line-height: 1.7;
	padding: 8px 12px;
	border-bottom: 1px dashed var(--border, #eee);
}
.qual-item:last-child {
	border-bottom: none;
}
.qual-req {
	flex: 1;
}
.qual-cert {
	font-size: 12px;
	flex-shrink: 0;
}

/* 文件清单 */
.doc-item {
	font-size: 13px;
	line-height: 1.9;
	padding: 4px 12px;
	border-bottom: 1px dashed var(--border, #eee);
}
.doc-item:last-child {
	border-bottom: none;
}

/* 审标报告链接 */
.report-a {
	margin-left: auto;
}

.overview {
	display: flex;
	justify-content: space-between;
	align-items: center;
	gap: 20px;
}
.ov-title {
	font-size: 15px;
	font-weight: 700;
	margin-bottom: 8px;
}
.ov-desc {
	font-size: 14px;
	line-height: 1.8;
	margin-bottom: 8px;
}
.ov-sub {
	font-size: 12px;
}
.score-num {
	font-size: 30px;
	font-weight: 700;
}
.score-num.low {
	color: var(--danger);
}

.mini-grid {
	display: grid;
	grid-template-columns: repeat(3, 1fr);
	gap: 14px;
}
.mini {
	padding: 14px 18px;
}
.mini-num {
	font-size: 26px;
	font-weight: 700;
	margin: 6px 0 4px;
}
.mini-num .unit {
	font-size: 13px;
	font-weight: 400;
	color: var(--muted);
}
.mini-num.red {
	color: var(--danger);
}
.mini-num.orange {
	color: var(--warning);
}

.redline {
	display: flex;
	gap: 8px;
	align-items: flex-start;
	font-size: 13px;
	line-height: 1.7;
	padding: 10px 12px;
	background: #fef6f6;
	border-radius: 6px;
	margin-bottom: 8px;
}

.dq-item {
	display: flex;
	gap: 8px;
	align-items: baseline;
	font-size: 13px;
	line-height: 1.7;
	padding: 8px 12px;
	border-bottom: 1px dashed var(--border, #eee);
}
.dq-item:last-child {
	border-bottom: none;
}
.dq-kw {
	font-weight: 600;
	flex-shrink: 0;
}
.dq-kw.miss {
	color: var(--danger, #e34d59);
}
.dq-sug {
	font-size: 12px;
}

.compare {
	display: grid;
	grid-template-columns: 1fr 1fr;
	gap: 14px;
}
.cmp {
	max-height: calc(100vh - 220px);
	display: flex;
	flex-direction: column;
}
.doc {
	flex: 1;
	overflow: auto;
	font-size: 12px;
	line-height: 1.7;
	white-space: pre-wrap;
	word-break: break-all;
	background: var(--bg);
	border-radius: 8px;
	padding: 14px;
	max-height: calc(100vh - 300px);
}
.pdf-frame {
	width: 100%;
	height: calc(100vh - 300px);
	border: 1px solid var(--border, #eee);
	border-radius: 8px;
	background: #525659;
}
.docx-frame {
	background: #e8eaed;
}

/* markdown 渲染视图（原文对照初稿栏 / 初稿编辑预览） */
.md-view {
	flex: 1;
	overflow: auto;
	font-size: 13px;
	line-height: 1.8;
	background: #fff;
	border: 1px solid var(--border, #eee);
	border-radius: 8px;
	padding: 18px 22px;
	max-height: calc(100vh - 300px);
	color: #303133;
}
.md-view h1 {
	font-size: 20px;
	border-bottom: 2px solid #ebeef5;
	padding-bottom: 8px;
	margin: 4px 0 14px;
}
.md-view h2 {
	font-size: 17px;
	margin: 20px 0 10px;
	padding-left: 8px;
	border-left: 4px solid #409eff;
}
.md-view h3 {
	font-size: 15px;
	margin: 16px 0 8px;
}
.md-view h4 {
	font-size: 14px;
	margin: 12px 0 6px;
}
.md-view p {
	margin: 6px 0;
}
.md-view ul,
.md-view ol {
	padding-left: 22px;
	margin: 6px 0;
}
.md-view table {
	border-collapse: collapse;
	width: 100%;
	margin: 10px 0;
	font-size: 12px;
}
.md-view th,
.md-view td {
	border: 1px solid #dcdfe6;
	padding: 6px 10px;
	text-align: left;
}
.md-view th {
	background: #f5f7fa;
	font-weight: 600;
}
.md-view blockquote {
	margin: 8px 0;
	padding: 6px 12px;
	border-left: 3px solid #c0c4cc;
	background: #f5f7fa;
	color: #606266;
}
.md-view code {
	background: #f5f7fa;
	padding: 1px 5px;
	border-radius: 4px;
	font-size: 12px;
}

/* 初稿编辑：左树右编辑 */
.edit-layout {
	display: grid;
	grid-template-columns: 260px 1fr;
	gap: 14px;
	align-items: start;
}
.side {
	position: sticky;
	top: 0;
}
.edit-panel {
	min-height: 400px;
}
.panel-title > div {
	display: flex;
	gap: 8px;
}
.edit-tip {
	margin-top: 10px;
}
.dot {
	margin-left: 2px;
}

@media (max-width: 1100px) {
	.info-grid,
	.mini-grid {
		grid-template-columns: 1fr;
	}
	.edit-layout {
		grid-template-columns: 1fr;
	}
	.side {
		position: static;
	}
	.compare {
		grid-template-columns: 1fr;
	}
}
</style>
