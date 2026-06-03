<template>
  <el-card>
    <template #header>
      <div class="card-title">区块链审计与治理</div>
    </template>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="审计事件" name="audit">
        <el-button type="primary" plain @click="fetchAudit" :loading="auditLoading">刷新</el-button>

        <el-table :data="events" v-loading="auditLoading" style="margin-top: 16px">
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
      </el-tab-pane>

      <el-tab-pane label="批量锚定" name="anchor">
        <div class="toolbar">
          <el-button type="primary" :loading="anchorRunning" @click="runAnchor">
            执行批量锚定
          </el-button>
          <el-button plain :loading="anchorLoading" @click="fetchBatches">刷新批次</el-button>
        </div>
        <p class="muted">
          把尚未锚定的病历哈希聚合成 Merkle Root 后上链。没有新增病历时不会重复上链。
        </p>
        <el-table :data="batches" v-loading="anchorLoading" style="margin-top: 12px">
          <el-table-column prop="batch_id" label="Batch" min-width="190" show-overflow-tooltip />
          <el-table-column prop="leaf_count" label="叶子数" width="90" />
          <el-table-column prop="record_id_low" label="起始记录" width="100" />
          <el-table-column prop="record_id_high" label="结束记录" width="100" />
          <el-table-column prop="merkle_root" label="Merkle Root" min-width="260" show-overflow-tooltip />
          <el-table-column prop="tx_id" label="TxID" min-width="220" show-overflow-tooltip />
          <el-table-column prop="created_at" label="时间" width="180" />
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="链上检索" name="chain-query">
        <el-form :model="chainHospitalQuery" inline>
          <el-form-item label="上传医院">
            <el-input v-model="chainHospitalQuery.hospital" placeholder="HospitalA" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="chainHospitalLoading" @click="queryChainByHospital">
              按医院查链上病历
            </el-button>
          </el-form-item>
        </el-form>

        <el-table :data="chainHospitalRecords.records" v-loading="chainHospitalLoading" size="small">
          <el-table-column prop="record_id" label="记录ID" width="90" />
          <el-table-column prop="patient_id" label="患者ID" width="90" />
          <el-table-column prop="uploader_hospital" label="上传医院" width="130" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="data_hash" label="链上哈希" min-width="220" show-overflow-tooltip />
          <el-table-column prop="tx_id" label="TxID" min-width="220" show-overflow-tooltip />
        </el-table>

        <div v-if="chainHospitalRecords.bookmark" class="load-more">
          <el-button size="small" :loading="chainHospitalLoading" @click="queryChainByHospital(chainHospitalRecords.bookmark)">
            加载下一页
          </el-button>
        </div>

        <el-form :model="chainDateQuery" inline style="margin-top: 22px">
          <el-form-item label="开始时间">
            <el-input v-model="chainDateQuery.from" placeholder="2020-01-01T00:00:00Z" />
          </el-form-item>
          <el-form-item label="结束时间">
            <el-input v-model="chainDateQuery.to" placeholder="2099-01-01T00:00:00Z" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="chainDateLoading" @click="queryChainByDate">
              按时间查链上病历
            </el-button>
          </el-form-item>
        </el-form>

        <el-table :data="chainDateRecords.records" v-loading="chainDateLoading" size="small">
          <el-table-column prop="record_id" label="记录ID" width="90" />
          <el-table-column prop="patient_id" label="患者ID" width="90" />
          <el-table-column prop="uploader_hospital" label="上传医院" width="130" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="created_at" label="创建时间" width="180" />
          <el-table-column prop="data_hash" label="链上哈希" min-width="220" show-overflow-tooltip />
          <el-table-column prop="tx_id" label="TxID" min-width="220" show-overflow-tooltip />
        </el-table>

        <div v-if="chainDateRecords.bookmark" class="load-more">
          <el-button size="small" :loading="chainDateLoading" @click="queryChainByDate(chainDateRecords.bookmark)">
            加载下一页
          </el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="治理面板" name="governance">
        <el-form :model="govForm" label-width="90px" class="gov-form">
          <el-form-item label="动作ID">
            <el-input v-model="govForm.action_id" placeholder="例如 unfreeze-1" />
          </el-form-item>
          <el-form-item label="动作类型">
            <el-select v-model="govForm.kind" style="width: 220px">
              <el-option label="解冻病历" value="UNFREEZE_RECORD" />
              <el-option label="冻结病历" value="FREEZE_RECORD" />
              <el-option label="批量撤销患者授权" value="BATCH_REVOKE_PATIENT" />
              <el-option label="强制删除病历" value="FORCE_DELETE_RECORD" />
            </el-select>
          </el-form-item>
          <el-form-item label="关联记录">
            <el-input-number v-model="govForm.record_id" :min="1" />
            <span class="muted" style="margin-left: 10px">UNFREEZE_RECORD 必填</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="govSubmitting" @click="proposeGovernance">
              发起治理提案
            </el-button>
            <el-button plain :loading="govLoading" @click="fetchGovernance">刷新</el-button>
          </el-form-item>
        </el-form>

        <el-table :data="governance" v-loading="govLoading" style="margin-top: 12px">
          <el-table-column prop="action_id" label="Action" min-width="150" show-overflow-tooltip />
          <el-table-column prop="kind" label="类型" width="180" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="govStatusType(row.status)" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="批准 MSP" width="180">
            <template #default="{ row }">
              {{ (row.approvers || []).map((x) => x.msp).join(", ") || "-" }}
            </template>
          </el-table-column>
          <el-table-column label="Payload" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">{{ JSON.stringify(row.payload || {}) }}</template>
          </el-table-column>
          <el-table-column prop="execute_tx_id" label="ExecuteTx" min-width="180" show-overflow-tooltip />
          <el-table-column label="操作" width="300" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="approveGovernance(row)">批准</el-button>
              <el-button size="small" type="danger" plain @click="rejectGovernance(row)">拒绝</el-button>
              <el-button size="small" type="primary" @click="executeGovernance(row)">执行</el-button>
              <el-button
                v-if="row.kind === 'UNFREEZE_RECORD' && row.status === 'EXECUTED'"
                size="small"
                type="success"
                @click="unfreezeByGovernance(row)"
              >解冻</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="v2 字段" name="v2">
        <div class="toolbar">
          <el-button plain :loading="systemLoading" @click="fetchSystemInfo">刷新链码版本</el-button>
          <el-tag v-if="systemInfo.schema_version" type="success">
            schema：{{ systemInfo.schema_version }}
          </el-tag>
        </div>

        <el-form :model="categoryQuery" inline style="margin-top: 14px">
          <el-form-item label="链上分类">
            <el-select v-model="categoryQuery.category" style="width: 180px">
              <el-option
                v-for="item in categories"
                :key="item"
                :label="categoryLabel(item)"
                :value="item"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="categoryLoading" @click="queryByCategory">
              链上查询
            </el-button>
          </el-form-item>
        </el-form>

        <el-table :data="categoryRecords.records" v-loading="categoryLoading">
          <el-table-column prop="record_id" label="记录ID" width="90" />
          <el-table-column prop="patient_id" label="患者ID" width="90" />
          <el-table-column prop="uploader_hospital" label="上传医院" width="130" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column prop="data_hash" label="链上哈希" min-width="240" show-overflow-tooltip />
          <el-table-column prop="tx_id" label="TxID" min-width="220" show-overflow-tooltip />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="dialogVisible" title="链上数据详情" width="620px">
      <pre class="json-box">{{ chainDetail }}</pre>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";

import http from "../../api/http";

const activeTab = ref("audit");
const auditLoading = ref(false);
const events = ref([]);
const dialogVisible = ref(false);
const chainDetail = ref("");

const anchorLoading = ref(false);
const anchorRunning = ref(false);
const batches = ref([]);
const chainHospitalLoading = ref(false);
const chainDateLoading = ref(false);
const chainHospitalQuery = reactive({ hospital: "HospitalA" });
const chainDateQuery = reactive({
  from: "2020-01-01T00:00:00Z",
  to: "2099-01-01T00:00:00Z",
});
const chainHospitalRecords = reactive({ records: [], bookmark: "" });
const chainDateRecords = reactive({ records: [], bookmark: "" });

const govLoading = ref(false);
const govSubmitting = ref(false);
const governance = ref([]);
const govForm = reactive({
  action_id: "",
  kind: "UNFREEZE_RECORD",
  record_id: 1,
});

const systemLoading = ref(false);
const systemInfo = reactive({ schema_version: "", contract_kinds: {} });
const categories = ref(["GENERAL", "INPATIENT", "OUTPATIENT", "EMERGENCY"]);
const categoryLoading = ref(false);
const categoryQuery = reactive({ category: "GENERAL" });
const categoryRecords = reactive({ records: [], bookmark: "", fetched_count: 0 });

async function fetchAudit() {
  auditLoading.value = true;
  try {
    const { data } = await http.get("/audit");
    events.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "加载失败");
  } finally {
    auditLoading.value = false;
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

async function fetchBatches() {
  anchorLoading.value = true;
  try {
    const { data } = await http.get("/anchor/batches");
    batches.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "锚定批次加载失败");
  } finally {
    anchorLoading.value = false;
  }
}

async function runAnchor() {
  anchorRunning.value = true;
  try {
    const { data } = await http.post("/anchor/run");
    ElMessage.success(data.anchored > 0 ? `已锚定 ${data.anchored} 条病历` : data.detail);
    await fetchBatches();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "批量锚定失败");
  } finally {
    anchorRunning.value = false;
  }
}

async function queryChainByHospital(bookmark = "") {
  if (!chainHospitalQuery.hospital.trim()) {
    ElMessage.warning("请填写医院名称");
    return;
  }
  chainHospitalLoading.value = true;
  try {
    const { data } = await http.get("/records/chain/by-hospital", {
      params: {
        hospital: chainHospitalQuery.hospital.trim(),
        page_size: 20,
        bookmark: typeof bookmark === "string" ? bookmark : "",
      },
    });
    const rows = data.records || [];
    chainHospitalRecords.records = bookmark ? chainHospitalRecords.records.concat(rows) : rows;
    chainHospitalRecords.bookmark = data.bookmark || "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "按医院链上查询失败");
  } finally {
    chainHospitalLoading.value = false;
  }
}

async function queryChainByDate(bookmark = "") {
  if (!chainDateQuery.from || !chainDateQuery.to) {
    ElMessage.warning("请填写时间范围");
    return;
  }
  chainDateLoading.value = true;
  try {
    const { data } = await http.get("/records/chain/by-date", {
      params: {
        from: chainDateQuery.from,
        to: chainDateQuery.to,
        page_size: 20,
        bookmark: typeof bookmark === "string" ? bookmark : "",
      },
    });
    const rows = data.records || [];
    chainDateRecords.records = bookmark ? chainDateRecords.records.concat(rows) : rows;
    chainDateRecords.bookmark = data.bookmark || "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "按时间链上查询失败");
  } finally {
    chainDateLoading.value = false;
  }
}

async function fetchGovernance() {
  govLoading.value = true;
  try {
    const { data } = await http.get("/governance/actions");
    governance.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "治理列表加载失败");
  } finally {
    govLoading.value = false;
  }
}

function govPayload() {
  if (govForm.kind === "UNFREEZE_RECORD" || govForm.kind === "FREEZE_RECORD") {
    return { recordId: String(govForm.record_id) };
  }
  return {};
}

async function proposeGovernance() {
  if (!govForm.action_id.trim()) {
    ElMessage.warning("请填写动作 ID");
    return;
  }
  govSubmitting.value = true;
  try {
    await http.post("/governance/actions", {
      action_id: govForm.action_id.trim(),
      kind: govForm.kind,
      payload: govPayload(),
    });
    ElMessage.success("治理提案已上链");
    govForm.action_id = "";
    await fetchGovernance();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "发起治理失败");
  } finally {
    govSubmitting.value = false;
  }
}

async function approveGovernance(row) {
  try {
    await http.post(`/governance/actions/${row.action_id}/approve`);
    ElMessage.success("已批准");
    fetchGovernance();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "批准失败");
  }
}

async function rejectGovernance(row) {
  try {
    await ElMessageBox.confirm(`确认拒绝治理动作 ${row.action_id}？`, "拒绝治理", {
      confirmButtonText: "拒绝",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await http.post(`/governance/actions/${row.action_id}/reject`);
    ElMessage.success("已拒绝");
    fetchGovernance();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "拒绝失败");
  }
}

async function executeGovernance(row) {
  try {
    await http.post(`/governance/actions/${row.action_id}/execute`);
    ElMessage.success("已执行");
    fetchGovernance();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "执行失败");
  }
}

async function unfreezeByGovernance(row) {
  const recordId = Number(row.payload?.recordId);
  if (!recordId) {
    ElMessage.warning("治理 payload 中缺少 recordId");
    return;
  }
  try {
    await http.post(`/records/${recordId}/unfreeze`, null, {
      params: { governance_action_id: row.action_id },
    });
    ElMessage.success("病历已解冻");
    fetchGovernance();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "解冻失败");
  }
}

function govStatusType(status) {
  return {
    PROPOSED: "info",
    PARTIALLY_APPROVED: "warning",
    APPROVED: "success",
    EXECUTED: "success",
    REJECTED: "danger",
  }[status] || "";
}

async function fetchSystemInfo() {
  systemLoading.value = true;
  try {
    const { data } = await http.get("/system/info");
    systemInfo.schema_version = data.schema_version;
    systemInfo.contract_kinds = data.contract_kinds || {};
    categories.value = systemInfo.contract_kinds.record_categories || categories.value;
    if (!categories.value.includes(categoryQuery.category)) {
      categoryQuery.category = categories.value[0] || "GENERAL";
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "链码版本加载失败");
  } finally {
    systemLoading.value = false;
  }
}

async function queryByCategory() {
  categoryLoading.value = true;
  try {
    const { data } = await http.get("/records/chain/by-category", {
      params: { category: categoryQuery.category, page_size: 50 },
    });
    categoryRecords.records = data.records || [];
    categoryRecords.bookmark = data.bookmark || "";
    categoryRecords.fetched_count = data.fetched_count || categoryRecords.records.length;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "链上分类查询失败");
  } finally {
    categoryLoading.value = false;
  }
}

function categoryLabel(value) {
  return {
    GENERAL: "通用",
    INPATIENT: "住院",
    OUTPATIENT: "门诊",
    EMERGENCY: "急诊",
  }[value] || value;
}

onMounted(() => {
  fetchAudit();
  fetchBatches();
  fetchGovernance();
  fetchSystemInfo();
});
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.gov-form {
  max-width: 620px;
}
.muted {
  color: #64748b;
  font-size: 12px;
}
.load-more {
  text-align: center;
  margin-top: 10px;
}
</style>
