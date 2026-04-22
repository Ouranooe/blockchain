<template>
  <el-container class="layout-root">
    <el-aside width="220px" class="layout-aside">
      <div class="brand">医疗数据共享系统</div>
      <el-menu :default-active="route.path" router class="menu-panel">
        <el-menu-item v-for="item in menus" :key="item.path" :index="item.path">
          {{ item.label }}
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout-header">
        <div class="header-left">
          <strong>{{ user.real_name || user.username }}</strong>
          <span class="role-tag">{{ roleLabel }}</span>
        </div>
        <el-button type="danger" plain @click="logout">退出登录</el-button>
      </el-header>

      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

const route = useRoute();
const router = useRouter();

function loadUser() {
  try {
    return JSON.parse(localStorage.getItem("user") || "{}");
  } catch (err) {
    return {};
  }
}

const user = computed(() => loadUser());

const menuMap = {
  hospital: [
    { path: "/hospital/records", label: "数据列表" },
    { path: "/hospital/upload", label: "数据上传" },
    { path: "/hospital/requests", label: "发起访问申请" },
    { path: "/hospital/authorized", label: "已授权数据查看" }
  ],
  patient: [
    { path: "/patient/records", label: "我的医疗数据" },
    { path: "/patient/reviews", label: "待审批申请" },
    { path: "/patient/authorizations", label: "我的授权" }
  ],
  admin: [{ path: "/admin/audit", label: "区块链审计" }]
};

const menus = computed(() => menuMap[user.value.role] || []);

const roleLabel = computed(() => {
  if (user.value.role === "hospital") return "医院";
  if (user.value.role === "patient") return "患者";
  if (user.value.role === "admin") return "管理员";
  return "未知角色";
});

function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("user");
  router.push("/login");
}
</script>
