<template>
  <el-card>
    <template #header>
      <div class="card-title">我的授权</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>
    <p class="muted" style="margin-top: 8px">
      这里列出我已经批准过的访问授权。可以在有效期内主动撤销（操作会上链）。
    </p>

    <el-table :data="approvals" v-loading="loading" style="margin-top: 4px">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="record_id" label="记录ID" width="80" />
      <el-table-column prop="applicant_hospital" label="申请医院" width="120" />
      <el-table-column prop="reason" label="理由" min-width="180" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="剩余次数 / 上限" width="140">
        <template #default="{ row }">
          <span v-if="row.max_reads != null">
            {{ row.remaining_reads ?? 0 }} / {{ row.max_reads }}
          </span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="到期时间" width="200">
        <template #default="{ row }">
          {{ row.expires_at ? formatDate(row.expires_at) : "—" }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'APPROVED'"
            size="small"
            type="warning"
            @click="revoke(row)"
          >撤销授权</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const approvals = ref([]);

function statusTagType(status) {
  return {
    APPROVED: "success",
    PENDING: "info",
    REJECTED: "danger",
    REVOKED: "warning",
    EXPIRED: "warning",
    EXHAUSTED: "warning",
  }[status] || "";
}

function formatDate(s) {
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

async function fetchData() {
  loading.value = true;
  try {
    // 复用审计接口的原思路：拉取本人的全部申请（后端已有 /access-requests/pending 只含 PENDING）
    // 这里新增一个端点会更干净，但最小改动先用 patient/records 上的 history 接口不合适。
    // 暂时用 /audit 过滤 or 直接请求 /access-requests/mine
    const { data } = await http.get("/access-requests/mine");
    approvals.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

async function revoke(row) {
  try {
    await ElMessageBox.confirm(
      `确认撤销对 ${row.applicant_hospital} 访问记录 ${row.record_id} 的授权？`,
      "撤销授权（上链）",
      {
        confirmButtonText: "撤销并上链",
        cancelButtonText: "取消",
        type: "warning",
      }
    );
  } catch {
    return;
  }
  try {
    await http.post(`/access-requests/${row.id}/revoke`);
    ElMessage.success("已撤销并上链，该医院将无法再消费此授权");
    fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "撤销失败");
  }
}

onMounted(fetchData);
</script>

<style scoped>
.muted {
  color: #888;
  font-size: 12px;
}
</style>
