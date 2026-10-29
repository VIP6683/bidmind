<script setup>
import { computed } from 'vue';

const props = defineProps({
	markdown: { type: String, default: '' }
});

/** 从 markdown 的 #/##/### 标题构建章节树 */
const treeData = computed(() => {
	const roots = [];
	const stack = [{ level: 0, children: roots }];
	for (const line of (props.markdown || '').split('\n')) {
		const m = /^(#{1,4})\s+(.+)$/.exec(line.trim());
		if (!m) continue;
		const level = m[1].length;
		const node = { label: m[2].trim(), children: [] };
		while (stack.length > 1 && stack[stack.length - 1].level >= level) stack.pop();
		stack[stack.length - 1].children.push(node);
		stack.push({ level, children: node.children });
	}
	// 清理空 children
	const clean = (nodes) => {
		for (const n of nodes) {
			if (n.children.length) clean(n.children);
			else delete n.children;
		}
	};
	clean(roots);
	return roots;
});
</script>

<template>
	<div class="chapter-tree">
		<div class="tree-head">投标初稿章节<span class="tree-sub">系统生成</span></div>
		<el-empty v-if="!treeData.length" description="暂无章节" :image-size="60" />
		<el-tree
			v-else
			:data="treeData"
			default-expand-all
			:expand-on-click-node="true"
			:props="{ children: 'children', label: 'label' }"
		/>
	</div>
</template>

<style scoped>
.chapter-tree {
	border: 1px solid var(--border);
	border-radius: 10px;
	padding: 14px;
	background: #fff;
}
.tree-head {
	font-size: 13px;
	font-weight: 600;
	margin-bottom: 10px;
	padding-bottom: 8px;
	border-bottom: 1px solid var(--bg);
}
.tree-sub {
	font-size: 11px;
	font-weight: 400;
	color: #909399;
	margin-left: 6px;
}
:deep(.el-tree-node__content) {
	height: 30px;
	border-radius: 6px;
}
:deep(.el-tree-node__label),
:deep(.el-tree-node__content span) {
	font-size: 13px;
}
</style>
