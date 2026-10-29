<script setup>
import { ref, reactive, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { getCompanyDoc, saveCompanyDoc } from '../api';

const TABS = [
  { name: 'company_info', label: '公司信息' },
  { name: 'qualifications', label: '资质证书' },
  { name: 'team', label: '团队人员' },
  { name: 'performance', label: '业绩案例' },
];

const activeTab = ref('company_info');
const loading = ref(true);
const saving = ref(false);
const contents = reactive({});
const dirty = reactive({});

onMounted(async () => {
  try {
    await Promise.all(
      TABS.map(async (t) => {
        const res = await getCompanyDoc(t.name);
        contents[t.name] = res.content || '';
      })
    );
  } finally {
    loading.value = false;
  }
});

async function onSave(name) {
  saving.value = true;
  try {
    await saveCompanyDoc(name, contents[name]);
    dirty[name] = false;
    ElMessage.success('素材已保存，生成投标文件时将自动引用');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="panel" v-loading="loading">
    <div class="panel-title">
      我的投标知识库
      <span class="text-muted" style="font-weight: 400">企业素材将自动注入投标文件生成流程</span>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane v-for="t in TABS" :key="t.name" :name="t.name">
        <template #label>
          {{ t.label }} <el-badge v-if="dirty[t.name]" is-dot />
        </template>
        <el-input
          v-model="contents[t.name]"
          type="textarea"
          :autosize="{ minRows: 18 }"
          :placeholder="`用 Markdown 维护${t.label}素材，支持表格。生成投标文件时按章节自动引用。`"
          @input="dirty[t.name] = true"
        />
        <div class="foot">
          <el-button type="primary" size="small" :loading="saving" :disabled="!dirty[t.name]" @click="onSave(t.name)">
            保存{{ t.label }}
          </el-button>
          <span class="text-muted">* 建议：资质写证书名称+等级+有效期；业绩写项目名+金额+年份；人员写岗位+证书+年限。</span>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<style scoped>
.foot {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 14px;
}
</style>
