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
      <el-table-column label="文件" width="170">
        <template #default="{ row }">
          <template v-if="row.has_file">
            <el-tag size="small" type="success">链上哈希 ✓</el-tag>
            <el-tag
              v-if="verifiedIds.has(row.id)"
              size="small"
              type="success"
              style="margin-left: 4px"
            >文件完整性 ✓</el-tag>
          </template>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openHistory(row)">版本链</el-button>
          <el-button
            v-if="row.has_file && row.can_view_content"
            size="small"
            type="success"
            @click="downloadFile(row)"
          >下载</el-button>
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

    <!-- 链上时间线抽屉（迭代 3：来自 Fabric GetHistoryForKey） -->
    <el-drawer v-model="historyVisible" title="链上时间线" size="580px">
      <div v-if="historyLoading" v-loading="true" style="height: 80px"></div>
      <template v-else-if="chainHistory">
        <p class="muted">
          病历 ID：{{ chainHistory.record_id }}，链上 {{ chainHistory.entries.length }} 次变更
          <el-tag
            size="small"
            :type="chainHistory.cache === 'hit' ? 'success' : 'info'"
            style="margin-left: 8px"
          >缓存：{{ chainHistory.cache === "hit" ? "命中" : "穿透" }}</el-tag>
        </p>
        <el-timeline>
          <el-timeline-item
            v-for="(entry, idx) in chainHistory.entries"
            :key="entry.tx_id + idx"
            :type="idx === 0 ? 'primary' : 'info'"
            :timestamp="entry.timestamp || ''"
          >
            <div>
              <b>v{{ entry.value?.version }}</b>
              <el-tag v-if="entry.is_delete" size="small" type="danger" style="margin-left: 6px">
                DELETE
              </el-tag>
              <el-tag v-if="idx === 0" size="small" type="success" style="margin-left: 6px">
                最新
              </el-tag>
            </div>
            <div class="mono">TxID：{{ entry.tx_id }}</div>
            <div v-if="entry.value" class="mono">哈希：{{ entry.value.dataHash }}</div>
            <div v-if="entry.value?.previousTxId" class="mono">
              上一版 Tx：{{ entry.value.previousTxId }}
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
const verifiedIds = ref(new Set());

const reviseVisible = ref(false);
const reviseSubmitting = ref(false);
const currentRecord = ref(null);
const reviseForm = reactive({ diagnosis: "", content: "" });

const historyVisible = ref(false);
const historyLoading = ref(false);
const chainHistory = ref(null);

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

async function downloadFile(row) {
  try {
    const resp = await http.get(`/records/${row.id}/download`, {
      responseType: "blob",
    });
    // 触发浏览器下载
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
    verifiedIds.value.add(row.id);
    // 强制触发响应式
    verifiedIds.value = new Set(verifiedIds.value);
    ElMessage.success(
      `下载成功，链上哈希已通过校验（${row.content_hash?.slice(0, 12)}…）`
    );
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "下载失败，可能是文件完整性校验未通过");
  }
}

async function openHistory(row) {
  historyVisible.value = true;
  historyLoading.value = true;
  chainHistory.value = null;
  try {
    const { data } = await http.get(`/records/${row.id}/chain-history`);
    chainHistory.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载链上时间线失败");
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
