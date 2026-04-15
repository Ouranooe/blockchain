import { createRouter, createWebHistory } from "vue-router";

import AppLayout from "../components/AppLayout.vue";
import LoginView from "../views/LoginView.vue";
import RecordListView from "../views/hospital/RecordListView.vue";
import UploadView from "../views/hospital/UploadView.vue";
import RequestView from "../views/hospital/RequestView.vue";
import AuthorizedView from "../views/hospital/AuthorizedView.vue";
import MyRecordsView from "../views/patient/MyRecordsView.vue";
import PendingApprovalsView from "../views/patient/PendingApprovalsView.vue";
import AuditView from "../views/admin/AuditView.vue";

function getUser() {
  try {
    return JSON.parse(localStorage.getItem("user") || "null");
  } catch (err) {
    return null;
  }
}

function defaultPathByRole(role) {
  if (role === "hospital") return "/hospital/records";
  if (role === "patient") return "/patient/records";
  if (role === "admin") return "/admin/audit";
  return "/login";
}

const routes = [
  {
    path: "/login",
    component: LoginView,
    meta: { public: true }
  },
  {
    path: "/",
    component: AppLayout,
    children: [
      {
        path: "hospital/records",
        component: RecordListView,
        meta: { roles: ["hospital"] }
      },
      {
        path: "hospital/upload",
        component: UploadView,
        meta: { roles: ["hospital"] }
      },
      {
        path: "hospital/requests",
        component: RequestView,
        meta: { roles: ["hospital"] }
      },
      {
        path: "hospital/authorized",
        component: AuthorizedView,
        meta: { roles: ["hospital"] }
      },
      {
        path: "patient/records",
        component: MyRecordsView,
        meta: { roles: ["patient"] }
      },
      {
        path: "patient/reviews",
        component: PendingApprovalsView,
        meta: { roles: ["patient"] }
      },
      {
        path: "admin/audit",
        component: AuditView,
        meta: { roles: ["admin"] }
      }
    ]
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: () => {
      const user = getUser();
      return defaultPathByRole(user?.role);
    }
  }
];

const router = createRouter({
  history: createWebHistory(),
  routes
});

router.beforeEach((to) => {
  if (to.meta.public) return true;

  const token = localStorage.getItem("token");
  const user = getUser();
  if (!token || !user) {
    return "/login";
  }

  if (to.meta.roles && !to.meta.roles.includes(user.role)) {
    return defaultPathByRole(user.role);
  }

  if (to.path === "/") {
    return defaultPathByRole(user.role);
  }

  return true;
});

export default router;
