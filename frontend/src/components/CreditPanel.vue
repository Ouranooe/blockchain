<template>
  <div class="credit-panel">
    <el-button size="small" plain :loading="balanceLoading" @click="open">
      积分：{{ balance.balance }}
    </el-button>

    <el-drawer v-model="visible" title="共享积分" size="520px" @open="loadAll">
      <div class="credit-summary">
        <div>
          <div class="muted">链上账户</div>
          <div class="account-id">{{ balance.user_id || "-" }}</div>
        </div>
        <div class="balance-box">
          <span class="muted">余额</span>
          <strong>{{ balance.balance }}</strong>
        </div>
      </div>

      <el-form :model="transferForm" label-width="90px" class="transfer-form">
        <el-form-item label="转给账户">
          <el-input
            v-model="transferForm.to_user_id"
            placeholder="医院填 HospitalA；患者填用户 ID"
          />
        </el-form-item>
        <el-form-item label="积分数量">
          <el-input-number v-model="transferForm.amount" :min="1" :max="1000000" />
        </el-form-item>
        <el-form-item label="原因">
          <el-input v-model="transferForm.reason_code" placeholder="TRANSFER" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="transferLoading" @click="transfer">
            链上转账
          </el-button>
          <el-button @click="loadAll">刷新</el-button>
        </el-form-item>
      </el-form>

      <div class="section-title">最近流水</div>
      <el-table :data="history.items" v-loading="historyLoading" size="small">
        <el-table-column label="方向" width="72">
          <template #default="{ row }">
            <el-tag :type="row.to_user_id === balance.user_id ? 'success' : 'warning'" size="small">
              {{ row.to_user_id === balance.user_id ? "收入" : "支出" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="amount" label="数量" width="80" />
        <el-table-column prop="reason_code" label="原因" width="130" show-overflow-tooltip />
        <el-table-column label="对方" min-width="130" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.to_user_id === balance.user_id ? row.from_user_id : row.to_user_id }}
          </template>
        </el-table-column>
        <el-table-column prop="tx_id" label="TxID" min-width="180" show-overflow-tooltip />
      </el-table>

      <div v-if="history.bookmark" class="load-more">
        <el-button size="small" :loading="historyLoading" @click="loadHistory(history.bookmark)">
          加载更多
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../api/http";

const visible = ref(false);
const balanceLoading = ref(false);
const historyLoading = ref(false);
const transferLoading = ref(false);
const balance = reactive({ user_id: "", balance: 0 });
const history = reactive({ items: [], bookmark: "", fetched_count: 0 });
const transferForm = reactive({
  to_user_id: "",
  amount: 1,
  reason_code: "TRANSFER",
});

function open() {
  visible.value = true;
}

async function loadBalance() {
  balanceLoading.value = true;
  try {
    const { data } = await http.get("/credits/balance");
    balance.user_id = data.user_id;
    balance.balance = data.balance;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "积分余额加载失败");
  } finally {
    balanceLoading.value = false;
  }
}

async function loadHistory(bookmark = "") {
  historyLoading.value = true;
  try {
    const { data } = await http.get("/credits/history", {
      params: { page_size: 20, bookmark },
    });
    const nextItems = data.items || [];
    history.items = bookmark ? history.items.concat(nextItems) : nextItems;
    history.bookmark = data.bookmark || "";
    history.fetched_count = data.fetched_count || nextItems.length;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "积分流水加载失败");
  } finally {
    historyLoading.value = false;
  }
}

async function loadAll() {
  await loadBalance();
  await loadHistory("");
}

async function transfer() {
  if (!transferForm.to_user_id.trim()) {
    ElMessage.warning("请填写目标账户");
    return;
  }
  transferLoading.value = true;
  try {
    const { data } = await http.post("/credits/transfer", {
      to_user_id: transferForm.to_user_id.trim(),
      amount: transferForm.amount,
      reason_code: transferForm.reason_code || "TRANSFER",
    });
    balance.user_id = data.user_id;
    balance.balance = data.balance;
    transferForm.to_user_id = "";
    transferForm.amount = 1;
    ElMessage.success("转账已上链");
    await loadHistory("");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "转账失败");
  } finally {
    transferLoading.value = false;
  }
}

function onCreditEvent() {
  loadBalance();
  if (visible.value) loadHistory("");
}

onMounted(() => {
  loadBalance();
  window.addEventListener("medshare:credit-refresh", onCreditEvent);
});

onBeforeUnmount(() => {
  window.removeEventListener("medshare:credit-refresh", onCreditEvent);
});
</script>

<style scoped>
.credit-panel {
  display: inline-flex;
}
.credit-summary {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px;
  border: 1px solid #e7ecf3;
  border-radius: 6px;
  background: #f8fafc;
}
.account-id {
  margin-top: 4px;
  font-family: Consolas, "Courier New", monospace;
  word-break: break-all;
}
.balance-box {
  min-width: 100px;
  text-align: right;
}
.balance-box strong {
  display: block;
  font-size: 28px;
  margin-top: 2px;
}
.transfer-form {
  margin-top: 18px;
}
.section-title {
  font-weight: 700;
  margin: 18px 0 8px;
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
