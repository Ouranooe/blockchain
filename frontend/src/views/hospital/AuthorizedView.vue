<template>
  <el-card>
    <template #header>
      <div class="card-title">已授权数据查看</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="records" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="记录ID" width="90" />
      <el-table-column prop="title" label="标题" min-width="160" />
      <el-table-column prop="patient_name" label="患者" width="110" />
      <el-table-column prop="uploader_hospital" label="上传医院" width="140" />
      <el-table-column prop="diagnosis" label="诊断" width="140" />
      <el-table-column prop="content" label="病历正文" min-width="260" show-overflow-tooltip />
      <el-table-column prop="tx_id" label="记录上链TxID" min-width="220" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="180" />
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const records = ref([]);

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/authorized-records");
    records.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

onMounted(fetchData);
</script>
