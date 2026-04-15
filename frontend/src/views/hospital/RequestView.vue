<template>
  <el-card>
    <template #header>
      <div class="card-title">发起访问申请</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="candidateRecords" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="记录ID" width="90" />
      <el-table-column prop="title" label="标题" min-width="170" />
      <el-table-column prop="patient_name" label="患者" width="120" />
      <el-table-column prop="uploader_hospital" label="上传医院" width="140" />
      <el-table-column prop="diagnosis" label="诊断" min-width="160" />
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button type="primary" link @click="openDialog(row)">申请访问</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="填写申请理由" width="520px">
      <el-input
        v-model="reason"
        type="textarea"
        :rows="5"
        placeholder="请输入访问该医疗数据的理由"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitLoading" @click="submit">提交申请</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const submitLoading = ref(false);
const records = ref([]);
const dialogVisible = ref(false);
const selectedRecord = ref(null);
const reason = ref("");

const user = computed(() => JSON.parse(localStorage.getItem("user") || "{}"));
const candidateRecords = computed(() =>
  records.value.filter(
    (item) =>
      item.uploader_hospital !== user.value.hospital_name &&
      !item.can_view_content
  )
);

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/records");
    records.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    loading.value = false;
  }
}

function openDialog(row) {
  selectedRecord.value = row;
  reason.value = "";
  dialogVisible.value = true;
}

async function submit() {
  if (!selectedRecord.value?.id || !reason.value.trim()) {
    ElMessage.warning("请填写申请理由");
    return;
  }
  submitLoading.value = true;
  try {
    await http.post("/access-requests", {
      record_id: selectedRecord.value.id,
      reason: reason.value
    });
    ElMessage.success("申请已提交并上链");
    dialogVisible.value = false;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "提交失败");
  } finally {
    submitLoading.value = false;
  }
}

onMounted(fetchData);
</script>
