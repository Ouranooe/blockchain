<template>
  <el-card>
    <template #header>
      <div class="card-title">上传医疗数据</div>
    </template>

    <el-form :model="form" label-width="110px" style="max-width: 760px">
      <el-form-item label="患者">
        <el-select v-model="form.patient_id" placeholder="选择患者" style="width: 220px">
          <el-option
            v-for="item in patients"
            :key="item.id"
            :label="`${item.real_name} (${item.username})`"
            :value="item.id"
          />
        </el-select>
      </el-form-item>

      <el-form-item label="记录标题">
        <el-input v-model="form.title" placeholder="例如：2026春季门诊记录" />
      </el-form-item>

      <el-form-item label="诊断结果">
        <el-input v-model="form.diagnosis" placeholder="例如：轻度贫血" />
      </el-form-item>

      <el-form-item label="上传模式">
        <el-radio-group v-model="mode">
          <el-radio value="text">文本</el-radio>
          <el-radio value="file">文件（PDF/图片，加密上链）</el-radio>
        </el-radio-group>
      </el-form-item>

      <el-form-item v-if="mode === 'text'" label="病历正文">
        <el-input
          v-model="form.content"
          type="textarea"
          :rows="8"
          placeholder="请输入医疗记录正文"
        />
      </el-form-item>

      <template v-else>
        <el-form-item label="附件">
          <input
            ref="fileInput"
            type="file"
            accept="application/pdf,image/jpeg,image/png"
            @change="onFileChange"
          />
          <div v-if="selectedFile" class="muted">
            已选：{{ selectedFile.name }}（{{ prettySize(selectedFile.size) }}）
          </div>
        </el-form-item>

        <el-form-item label="备注">
          <el-input
            v-model="form.description"
            placeholder="可选：对该文件的描述，将作为 content 字段存入 DB（明文）"
          />
        </el-form-item>
      </template>

      <el-form-item>
        <el-button type="primary" :loading="loading" @click="submit">
          {{ mode === "file" ? "加密上链并提交" : "提交并上链" }}
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import http from "../../api/http";

const loading = ref(false);
const patients = ref([]);
const mode = ref("text");
const selectedFile = ref(null);
const fileInput = ref(null);
const form = reactive({
  patient_id: undefined,
  title: "",
  diagnosis: "",
  content: "",
  description: "",
});

function prettySize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function onFileChange(ev) {
  selectedFile.value = ev.target.files?.[0] || null;
}

async function fetchPatients() {
  try {
    const { data } = await http.get("/users/patients");
    patients.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "患者列表加载失败");
  }
}

async function submit() {
  if (!form.patient_id || !form.title || !form.diagnosis) {
    ElMessage.warning("请完整填写基础信息");
    return;
  }
  if (mode.value === "text" && !form.content) {
    ElMessage.warning("请填写病历正文");
    return;
  }
  if (mode.value === "file" && !selectedFile.value) {
    ElMessage.warning("请选择要上传的文件");
    return;
  }
  loading.value = true;
  try {
    if (mode.value === "file") {
      const fd = new FormData();
      fd.append("patient_id", form.patient_id);
      fd.append("title", form.title);
      fd.append("diagnosis", form.diagnosis);
      fd.append("description", form.description || "");
      fd.append("file", selectedFile.value);
      const { data } = await http.post("/records/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      ElMessage.success(
        `上传成功：v${data.version}，哈希 ${data.content_hash.slice(0, 12)}…  TxID: ${data.tx_id}`
      );
      selectedFile.value = null;
      if (fileInput.value) fileInput.value.value = "";
    } else {
      const { data } = await http.post("/records", {
        patient_id: form.patient_id,
        title: form.title,
        diagnosis: form.diagnosis,
        content: form.content,
      });
      ElMessage.success(`上传成功，TxID: ${data.tx_id || "无"}`);
      form.content = "";
    }
    form.title = "";
    form.diagnosis = "";
    form.description = "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "上传失败");
  } finally {
    loading.value = false;
  }
}

onMounted(fetchPatients);
</script>

<style scoped>
.muted {
  color: #888;
  font-size: 12px;
  margin-top: 6px;
}
</style>
