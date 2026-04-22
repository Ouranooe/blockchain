<template>
  <el-card>
    <template #header>
      <div class="card-title">病历数据列表</div>
    </template>

    <el-button type="primary" plain @click="fetchData" :loading="loading">刷新</el-button>

    <el-table :data="records" v-loading="loading" style="margin-top: 16px">
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="title" label="标题" min-width="150" />
      <el-table-column prop="patient_name" label="患者" width="100" />
      <el-table-column prop="uploader_hospital" label="上传医院" width="120" />
      <el-table-column prop="diagnosis" label="诊断" min-width="160" />
      <el-table-column label="版本" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.version > 1 ? 'warning' : 'success'">
            v{{ row.version || 1 }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="content_hash" label="内容哈希" min-width="200" show-overflow-tooltip />
      <el-table-column label="正文查看" width="240">
        <template #default="{ row }">
          <span v-if="row.can_view_content">{{ row.content }}</span>
          <span v-else class="text-muted">未授权不可查看</span>
        </template>
      </el-table-column>
      <el-table-column prop="tx_id" label="最新TxID" min-width="200" show-overflow-tooltip />
      <el-table-column prop="created_at" label="创建时间" width="160" />
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openHistory(row)">版本链</el-button>
          <el-button
            v-if="canRevise(row)"
            size="small"
            type="primary"
            @click="openRevise(row)"
          >修订</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 修订对话框 -->
    <el-dialog v-model="reviseVisible" title="修订病历（将生成新版本上链）" width="620px">
      <template v-if="currentRecord">
        <p class="muted">病历 ID：{{ currentRecord.id }}（当前版本 v{{ currentRecord.version || 1 }}）</p>
        <el-form :model="reviseForm" label-width="80px">
          <el-form-item label="诊断">
            <el-input v-model="reviseForm.diagnosis" placeholder="可选：更新诊断" />
          </el-form-item>
          <el-form-item label="新正文">
            <el-input
              v-model="reviseForm.content"
              type="textarea"
              :rows="6"
              placeholder="请填写修订后的完整正文"
            />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="reviseVisible = false">取消</el-button>
        <el-button type="primary" :loading="reviseSubmitting" @click="submitRevise">
          提交修订并上链
        </el-button>
      </template>
    </el-dialog>

    <!-- 版本链抽屉 -->
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
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const records = ref([]);

const reviseVisible = ref(false);
const reviseSubmitting = ref(false);
const currentRecord = ref(null);
const reviseForm = reactive({ diagnosis: "", content: "" });

const historyVisible = ref(false);
const historyLoading = ref(false);
const history = ref(null);

const currentUser = (() => {
  try {
    return JSON.parse(localStorage.getItem("user") || "null");
  } catch {
    return null;
  }
})();

function canRevise(row) {
  return (
    currentUser &&
    currentUser.role === "hospital" &&
    row.uploader_hospital === currentUser.hospital_name
  );
}

async function fetchData() {
  loading.value = true;
  try {
    const { data } = await http.get("/records");
    records.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载数据失败");
  } finally {
    loading.value = false;
  }
}

function openRevise(row) {
  currentRecord.value = row;
  reviseForm.diagnosis = row.diagnosis || "";
  reviseForm.content = row.content || "";
  reviseVisible.value = true;
}

async function submitRevise() {
  if (!reviseForm.content) {
    ElMessage.warning("新正文不能为空");
    return;
  }
  reviseSubmitting.value = true;
  try {
    const { data } = await http.post(
      `/records/${currentRecord.value.id}/revise`,
      { diagnosis: reviseForm.diagnosis, content: reviseForm.content }
    );
    ElMessage.success(`修订成功，版本已更新为 v${data.version}（TxID: ${data.tx_id}）`);
    reviseVisible.value = false;
    await fetchData();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "修订失败");
  } finally {
    reviseSubmitting.value = false;
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
