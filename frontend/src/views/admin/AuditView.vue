<template>
  <el-card>
    <template #header>
      <div class="card-title">区块链审计记录</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="events" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="event_type" label="事件类型" width="220" />
      <el-table-column prop="business_id" label="业务ID" width="90" />
      <el-table-column prop="status" label="状态" width="120" />
      <el-table-column prop="operator" label="操作人" width="120" />
      <el-table-column prop="tx_id" label="TxID" min-width="260" show-overflow-tooltip />
      <el-table-column prop="created_at" label="时间" width="180" />
      <el-table-column label="链上查询" width="120">
        <template #default="{ row }">
          <el-button
            v-if="row.event_type.includes('ACCESS_REQUEST')"
            type="primary"
            link
            @click="queryChain(row.business_id)"
          >
            查看详情
          </el-button>
          <span v-else class="text-muted">-</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="链上数据详情" width="620px">
      <pre class="json-box">{{ chainDetail }}</pre>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const events = ref([]);
const dialogVisible = ref(false);
const chainDetail = ref("");

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/audit");
    events.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function queryChain(requestId) {
  try {
    const { data } = await http.get(`/access-requests/${requestId}/chain`);
    chainDetail.value = JSON.stringify(data, null, 2);
    dialogVisible.value = true;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "链上查询失败");
  }
}

onMounted(fetchData);
</script>
