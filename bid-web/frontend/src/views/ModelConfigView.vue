<script setup>
import { reactive, ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { getLlmConfig, saveLlmConfig } from '../api';

const loading = ref(true);
const saving = ref(false);
const hasKey = ref(false);
const form = reactive({
  api_key: '',
  base_url: '',
  model: '',
  temperature: 0.7,
  max_tokens: 4096,
  timeout: 60,
});

onMounted(async () => {
  try {
    const cfg = await getLlmConfig();
    hasKey.value = cfg.has_key;
    form.api_key = '';
    form.base_url = cfg.base_url;
    form.model = cfg.model;
    form.temperature = cfg.temperature;
    form.max_tokens = cfg.max_tokens;
    form.timeout = cfg.timeout;
  } finally {
    loading.value = false;
  }
});

async function onSave() {
  saving.value = true;
  try {
    const res = await saveLlmConfig({ ...form, api_key: form.api_key || undefined });
    hasKey.value = !!res.api_key;
    form.api_key = '';
    ElMessage.success('配置已保存，重新生成投标文件时生效');
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="panel config" v-loading="loading">
    <div class="panel-title">
      AI 模型配置
      <el-tag :type="hasKey ? 'success' : 'info'" size="small" effect="light">
        {{ hasKey ? '已配置 API Key' : '未配置（生成走模板降级模式）' }}
      </el-tag>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 18px"
      title="配置后技术标章节将由 AI 逐章生成；未配置时自动降级为模板骨架模式，不影响解析与审标。" />

    <el-form label-width="120px" style="max-width: 640px">
      <el-form-item label="API 地址">
        <el-input v-model="form.base_url" placeholder="https://api.deepseek.com/v1（或 Ollama: http://localhost:11434/v1）" />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.api_key" type="password" show-password
          :placeholder="hasKey ? '已配置（sk-***，留空表示不修改）' : 'sk-...'" />
      </el-form-item>
      <el-form-item label="模型名">
        <el-input v-model="form.model" placeholder="deepseek-chat / qwen-plus / glm-4 等" />
      </el-form-item>
      <el-form-item label="温度">
        <el-slider v-model="form.temperature" :min="0" :max="1" :step="0.1" show-input style="width: 100%" />
        <div class="text-muted tip">标书生成建议 0.5-0.7</div>
      </el-form-item>
      <el-form-item label="单次最大 Token">
        <el-input-number v-model="form.max_tokens" :min="1024" :max="32768" :step="1024" />
      </el-form-item>
      <el-form-item label="超时（秒）">
        <el-input-number v-model="form.timeout" :min="30" :max="600" :step="30" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="onSave">保存配置</el-button>
      </el-form-item>
    </el-form>

    <div class="text-muted note">
      * 配置保存到 templates/user_config.yaml；为保护安全，API Key 保存后仅显示末 4 位。
      本地内测数据全程不出库，调用云端 API 时请注意企业数据合规要求。
    </div>
  </div>
</template>

<style scoped>
.tip { width: 100%; }
.note { max-width: 640px; line-height: 1.8; }
</style>
