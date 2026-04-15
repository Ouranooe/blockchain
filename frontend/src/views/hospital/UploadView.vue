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

      <el-form-item label="病历正文">
        <el-input
          v-model="form.content"
          type="textarea"
          :rows="8"
          placeholder="请输入医疗记录正文"
        />
      </el-form-item>

      <el-form-item>
        <el-button type="primary" :loading="loading" @click="submit">提交并上链</el-button>
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
const form = reactive({
  patient_id: undefined,
  title: "",
  diagnosis: "",
  content: ""
});

async function fetchPatients() {
  try {
    const { data } = await http.get("/users/patients");
    patients.value = data;
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "患者列表加载失败");
  }
}

async function submit() {
  if (!form.patient_id || !form.title || !form.diagnosis || !form.content) {
    ElMessage.warning("请完整填写上传信息");
    return;
  }
  loading.value = true;
  try {
    const { data } = await http.post("/records", form);
    ElMessage.success(`上传成功，TxID: ${data.tx_id || "无"}`);
    form.title = "";
    form.diagnosis = "";
    form.content = "";
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "上传失败");
  } finally {
    loading.value = false;
  }
}

onMounted(fetchPatients);
</script>
