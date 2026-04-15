<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">医疗数据共享系统登录</div>
      </template>

      <el-form :model="form" label-width="80px" @submit.prevent>
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" @click="handleLogin">登录</el-button>
        </el-form-item>
      </el-form>

      <el-divider>测试账号</el-divider>
      <div class="account-tip">
        <p>管理员: admin / 123456</p>
        <p>患者: patient1 / 123456, patient2 / 123456</p>
        <p>医院: hospital_a / 123456, hospital_b / 123456</p>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";

import http from "../api/http";

const router = useRouter();
const loading = ref(false);
const form = reactive({
  username: "",
  password: ""
});

function pathByRole(role) {
  if (role === "hospital") return "/hospital/records";
  if (role === "patient") return "/patient/records";
  if (role === "admin") return "/admin/audit";
  return "/login";
}

async function handleLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning("请输入用户名和密码");
    return;
  }
  loading.value = true;
  try {
    const { data } = await http.post("/auth/login", form);
    localStorage.setItem("token", data.token);
    localStorage.setItem("user", JSON.stringify(data.user));
    ElMessage.success("登录成功");
    router.push(pathByRole(data.user.role));
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>
