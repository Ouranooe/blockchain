<template>
  <el-card>
    <template #header>
      <div class="card-title">待审批访问申请</div>
    </template>

    <div class="toolbar">
      <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>
      <el-button plain :loading="chainLoading" @click="openChainPending">
        链上待审批
      </el-button>
    </div>

    <el-table :data="requests" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="申请ID" width="80" />
      <el-table-column prop="record_id" label="记录ID" width="80" />
      <el-table-column prop="record_title" label="记录标题" min-width="160" />
      <el-table-column prop="applicant_hospital" label="申请医院" width="120" />
      <el-table-column label="用途" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ purposeLabel(row.purpose) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="申请理由" min-width="220" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column label="操作" width="200">
        <template #default="{ row }">
          <el-button type="success" size="small" @click="openApprove(row)">同意</el-button>
          <el-button type="danger" size="small" @click="reject(row.id)">拒绝</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 同意对话框：必须指定有效期与最大读取次数 -->
    <el-dialog v-model="approveVisible" title="批准访问（上链）" width="480px">
      <div v-if="current" class="muted">
        申请 ID：{{ current.id }}，申请医院：{{ current.applicant_hospital }}
      </div>
      <el-form :model="approveForm" label-width="110px" style="margin-top: 14px">
        <el-form-item label="有效天数">
          <el-input-number v-model="approveForm.duration_days" :min="1" :max="365" />
        </el-form-item>
        <el-form-item label="最大读取次数">
          <el-input-number v-model="approveForm.max_reads" :min="1" :max="1000" />
        </el-form-item>
        <el-form-item>
          <div class="muted">
            这些策略将写入链码；每次下载都会调用 AccessRecord 消费一次授权，到期或次数耗尽后链码层自动拒绝。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="confirmApprove">
          批准并上链
        </el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="chainVisible" title="链上 PENDING 申请" size="680px">
      <p class="muted">
        数据来自 `/access-requests/chain/pending`，直接查询链上 AccessRequest 状态。
      </p>
      <el-table :data="chainRequests" v-loading="chainLoading" size="small">
        <el-table-column prop="request_id" label="申请ID" width="90" />
        <el-table-column prop="record_id" label="记录ID" width="90" />
        <el-table-column prop="patient_id" label="患者ID" width="90" />
        <el-table-column prop="applicant_hospital" label="申请医院" width="130" />
        <el-table-column prop="applicant_msp" label="MSP" width="120" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
      </el-table>
      <div v-if="chainBookmark" class="load-more">
        <el-button size="small" :loading="chainLoading" @click="fetchChainPending(chainBookmark)">
          加载下一页
        </el-button>
      </div>
    </el-drawer>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const requests = ref([]);
const approveVisible = ref(false);
const submitting = ref(false);
const current = ref(null);
const approveForm = reactive({ duration_days: 7, max_reads: 3 });
const chainVisible = ref(false);
const chainLoading = ref(false);
const chainRequests = ref([]);
const chainBookmark = ref("");

function purposeLabel(value) {
  return {
    TREATMENT: "治疗",
    RESEARCH: "科研",
    AUDIT: "审计",
  }[value] || value || "治疗";
}

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

async function openChainPending() {
  chainVisible.value = true;
  await fetchChainPending("");
}

async function fetchChainPending(bookmark = "") {
  chainLoading.value = true;
  try {
    const { data } = await http.get("/access-requests/chain/pending", {
      params: { page_size: 20, bookmark },
    });
    const rows = data.requests || [];
    chainRequests.value = bookmark ? chainRequests.value.concat(rows) : rows;
    chainBookmark.value = data.bookmark || "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "链上待审批加载失败");
  } finally {
    chainLoading.value = false;
  }
}

function openApprove(row) {
  current.value = row;
  approveForm.duration_days = 7;
  approveForm.max_reads = 3;
  approveVisible.value = true;
}

async function confirmApprove() {
  submitting.value = true;
  try {
    await http.post(`/access-requests/${current.value.id}/review`, {
      decision: "APPROVED",
      duration_days: approveForm.duration_days,
      max_reads: approveForm.max_reads,
    });
    ElMessage.success("已批准并上链：将允许申请医院在有效期内消费指定次数");
    approveVisible.value = false;
    fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "审批失败");
  } finally {
    submitting.value = false;
  }
}

async function reject(id) {
  try {
    await ElMessageBox.confirm("确认拒绝该申请？", "提示", {
      confirmButtonText: "拒绝",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await http.post(`/access-requests/${id}/review`, { decision: "REJECTED" });
    ElMessage.success("已拒绝并上链");
    fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "操作失败");
  }
}

onMounted(fetchData);
</script>

<style scoped>
.muted {
  color: #888;
  font-size: 12px;
}
.toolbar {
  display: flex;
  gap: 8px;
}
.load-more {
  text-align: center;
  margin-top: 10px;
}
</style>
