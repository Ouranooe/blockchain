<template>
  <el-card>
    <template #header>
      <div class="card-title">已授权数据查看</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>
    <p class="muted" style="margin-top: 8px">
      已授权数据会在过期 / 次数用尽 / 被患者撤销时从列表中消失（链码层会同步拒绝访问）。
    </p>

    <el-table :data="records" v-loading="loading" style="margin-top: 4px">
      <el-table-column prop="id" label="记录ID" width="80" />
      <el-table-column prop="title" label="标题" min-width="150" />
      <el-table-column prop="patient_name" label="患者" width="100" />
      <el-table-column prop="uploader_hospital" label="上传医院" width="120" />
      <el-table-column label="分类" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ categoryLabel(row.category) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="冻结" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.frozen" size="small" type="danger">已冻结</el-tag>
          <span v-else class="text-muted">正常</span>
        </template>
      </el-table-column>
      <el-table-column prop="diagnosis" label="诊断" width="130" />
      <el-table-column prop="content" label="病历正文" min-width="220" show-overflow-tooltip />
      <el-table-column label="附件" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.has_file" size="small" type="success">链上哈希 ✓</el-tag>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="tx_id" label="最新TxID" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <RecordProofButton :record-id="row.id" />
          <el-button
            v-if="row.has_file"
            size="small"
            type="success"
            @click="downloadFile(row)"
          >下载（消费一次授权）</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";
import RecordProofButton from "../../components/RecordProofButton.vue";

const loading = ref(false);
const records = ref([]);

function categoryLabel(value) {
  return {
    GENERAL: "通用",
    INPATIENT: "住院",
    OUTPATIENT: "门诊",
    EMERGENCY: "急诊",
  }[value] || value || "通用";
}

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

async function downloadFile(row) {
  try {
    const resp = await http.get(`/records/${row.id}/download`, {
      responseType: "blob",
    });
    const blob = new Blob([resp.data], {
      type: row.file_mime || "application/octet-stream",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = row.file_name || `record-${row.id}.bin`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    const remaining = resp.headers["x-remaining-reads"];
    const accessTx = resp.headers["x-access-tx"];
    ElMessage.success(
      `下载成功 · 剩余次数：${remaining ?? "N/A"} · AccessTx：${accessTx?.slice(0, 16) || "N/A"}`
    );
    // 刷新列表使耗尽的行自动消失
    fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "下载失败，可能是链码层拒绝（过期/次数/MSP/状态）");
  }
}

onMounted(fetchData);
</script>

<style scoped>
.muted {
  color: #999;
  font-size: 12px;
}
</style>
