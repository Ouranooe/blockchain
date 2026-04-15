<template>
  <el-card>
    <template #header>
      <div class="card-title">待审批访问申请</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="requests" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="申请ID" width="90" />
      <el-table-column prop="record_id" label="记录ID" width="90" />
      <el-table-column prop="record_title" label="记录标题" min-width="170" />
      <el-table-column prop="applicant_hospital" label="申请医院" width="140" />
      <el-table-column prop="reason" label="申请理由" min-width="260" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column label="操作" width="220">
        <template #default="{ row }">
          <el-button type="success" size="small" @click="review(row.id, 'APPROVED')">同意</el-button>
          <el-button type="danger" size="small" @click="review(row.id, 'REJECTED')">拒绝</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const requests = ref([]);

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/access-requests/pending");
    requests.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function review(id, decision) {
  try {
    await http.post(`/access-requests/${id}/review`, { decision });
    ElMessage.success(decision === "APPROVED" ? "已同意并上链" : "已拒绝并上链");
    fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "审批失败");
  }
}

onMounted(fetchData);
</script>
