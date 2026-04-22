<template>
  <el-card>
    <template #header>
      <div class="card-title">我的医疗数据</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="records" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="记录ID" width="80" />
      <el-table-column prop="title" label="标题" min-width="150" />
      <el-table-column prop="diagnosis" label="诊断" width="130" />
      <el-table-column prop="uploader_hospital" label="上传医院" width="120" />
      <el-table-column label="版本" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.version > 1 ? 'warning' : 'success'">
            v{{ row.version || 1 }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="病历正文" min-width="260" show-overflow-tooltip />
      <el-table-column prop="content_hash" label="内容哈希" min-width="200" show-overflow-tooltip />
      <el-table-column prop="tx_id" label="最新TxID" min-width="200" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="160" />
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openHistory(row)">版本链</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="historyVisible" title="链上版本链" size="540px">
      <div v-if="historyLoading" v-loading="true" style="height: 80px"></div>
      <template v-else-if="history">
        <p class="muted">
          病历 ID：{{ history.record_id }}，最新版本：v{{ history.latest_version }}
        </p>
        <el-timeline>
          <el-timeline-item
            v-for="v in [...history.versions].reverse()"
            :key="v.version"
            :type="v.version === history.latest_version ? 'primary' : 'info'"
            :timestamp="v.updated_at || v.created_at || ''"
          >
            <div><b>v{{ v.version }}</b></div>
            <div class="mono">哈希：{{ v.data_hash }}</div>
            <div class="mono">TxID：{{ v.tx_id }}</div>
            <div v-if="v.previous_tx_id" class="mono">
              上一版：{{ v.previous_tx_id }}
            </div>
          </el-timeline-item>
        </el-timeline>
      </template>
    </el-drawer>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const records = ref([]);

const historyVisible = ref(false);
const historyLoading = ref(false);
const history = ref(null);

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/patient/records");
    records.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function openHistory(row) {
  historyVisible.value = true;
  historyLoading.value = true;
  history.value = null;
  try {
    const { data } = await http.get(`/records/${row.id}/history`);
    history.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载版本链失败");
  } finally {
    historyLoading.value = false;
  }
}

onMounted(fetchData);
</script>

<style scoped>
.muted {
  color: #999;
  margin-bottom: 12px;
}
.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  word-break: break-all;
  color: #555;
}
</style>
