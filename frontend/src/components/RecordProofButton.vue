<template>
  <span>
    <el-button size="small" plain :loading="loading" @click="openProof">证明</el-button>
    <el-drawer v-model="visible" title="Merkle 包含证明" size="560px">
      <div v-if="proof">
        <p class="muted">病历 ID：{{ proof.record_id }}</p>
        <p>
          <el-tag :type="verifyResult?.ok ? 'success' : 'info'" size="small">
            {{ verifyResult?.ok ? "链上验证通过" : "待验证" }}
          </el-tag>
          <span class="muted" style="margin-left: 8px">
            Anchored in {{ proof.batch.batch_id }}
          </span>
        </p>
        <div class="mono">Leaf：{{ proof.leaf_hash }}</div>
        <div class="mono">Root：{{ proof.batch.merkle_root }}</div>
        <div class="mono">TxID：{{ proof.batch.tx_id || "-" }}</div>

        <el-button
          type="primary"
          size="small"
          style="margin: 12px 0"
          :loading="verifyLoading"
          @click="verifyProof"
        >
          链上验证
        </el-button>

        <el-timeline>
          <el-timeline-item
            v-for="(step, idx) in proof.proof"
            :key="idx"
            :timestamp="step.position"
          >
            <div class="mono">{{ step.hash }}</div>
          </el-timeline-item>
        </el-timeline>
      </div>
      <el-empty v-else description="暂无证明数据" />
    </el-drawer>
  </span>
</template>

<script setup>
import { ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../api/http";

const props = defineProps({
  recordId: {
    type: Number,
    required: true,
  },
});

const visible = ref(false);
const loading = ref(false);
const verifyLoading = ref(false);
const proof = ref(null);
const verifyResult = ref(null);

async function openProof() {
  visible.value = true;
  loading.value = true;
  proof.value = null;
  verifyResult.value = null;
  try {
    const { data } = await http.get(`/records/${props.recordId}/proof`);
    proof.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "该病历尚未生成包含证明");
  } finally {
    loading.value = false;
  }
}

async function verifyProof() {
  if (!proof.value) return;
  verifyLoading.value = true;
  try {
    const { data } = await http.post("/anchor/verify", {
      batch_id: proof.value.batch.batch_id,
      leaf_hash: proof.value.leaf_hash,
      proof: proof.value.proof,
    });
    verifyResult.value = data;
    ElMessage.success(data.ok ? "链上验证通过" : "链上验证未通过");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "链上验证失败");
  } finally {
    verifyLoading.value = false;
  }
}
</script>

<style scoped>
.muted {
  color: #64748b;
  font-size: 12px;
}
.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  word-break: break-all;
  color: #4b5563;
  margin: 4px 0;
}
</style>
