<template>
  <el-popover
    placement="bottom-end"
    :width="360"
    trigger="click"
    @show="onPopoverShow"
  >
    <template #reference>
      <el-badge :value="unreadCount" :hidden="unreadCount === 0" class="bell">
        <el-button circle size="small">
          <span class="bell-icon">🔔</span>
        </el-button>
      </el-badge>
    </template>

    <div class="notif-wrap">
      <div class="notif-head">
        <span>通知中心（链码事件实时推送）</span>
        <el-tag :type="wsOpen ? 'success' : 'info'" size="small">
          {{ wsOpen ? "在线" : "离线" }}
        </el-tag>
      </div>
      <el-empty
        v-if="recent.length === 0"
        description="暂无通知"
        :image-size="60"
      />
      <div v-else class="notif-list">
        <div
          v-for="(n, i) in recent"
          :key="n.timestamp_ms + ':' + i"
          class="notif-item"
          :class="{ unread: !n.read }"
        >
          <div class="notif-line">
            <el-tag size="small" :type="tagOf(n.event_type)">{{ n.event_type }}</el-tag>
            <span class="notif-time">{{ formatTime(n.timestamp_ms) }}</span>
          </div>
          <div class="notif-msg">{{ n.message || n.event_type }}</div>
          <div v-if="n.tx_id" class="notif-tx">TxID: {{ n.tx_id }}</div>
        </div>
      </div>
      <div class="notif-foot">
        <el-button size="small" @click="clearAll">清空</el-button>
      </div>
    </div>
  </el-popover>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElNotification } from "element-plus";

const recent = ref([]);
const wsOpen = ref(false);
let ws = null;
let reconnectTimer = null;

const unreadCount = computed(() => recent.value.filter((n) => !n.read).length);

function tagOf(eventType) {
  if (eventType === "UnauthorizedAttempt") return "danger";
  if (eventType === "AccessRevoked") return "warning";
  if (eventType === "AccessApproved" || eventType === "AccessRecorded") return "success";
  if (eventType === "AccessRejected") return "danger";
  return "info";
}

function formatTime(ms) {
  if (!ms) return "";
  const d = new Date(ms);
  return d.toLocaleTimeString();
}

function connect() {
  const token = localStorage.getItem("token");
  if (!token) return;
  const base = (import.meta.env.VITE_WS_BASE_URL || "ws://localhost:8000").replace(/\/$/, "");
  const url = `${base}/ws/notifications?token=${encodeURIComponent(token)}`;
  try {
    ws = new WebSocket(url);
  } catch (e) {
    scheduleReconnect();
    return;
  }
  ws.onopen = () => {
    wsOpen.value = true;
  };
  ws.onmessage = (ev) => {
    let data;
    try {
      data = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (data.event_type === "_connected") return;
    const item = { ...data, read: false };
    recent.value.unshift(item);
    if (recent.value.length > 50) recent.value = recent.value.slice(0, 50);
    ElNotification({
      title: prettyTitle(data.event_type),
      message: data.message || data.event_type,
      type: tagOf(data.event_type) === "danger" ? "error" : "info",
      duration: 3000,
    });
  };
  ws.onclose = () => {
    wsOpen.value = false;
    scheduleReconnect();
  };
  ws.onerror = () => {
    try { ws.close(); } catch {}
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, 3000);
}

function prettyTitle(eventType) {
  const map = {
    RecordCreated: "新病历",
    RecordUpdated: "病历修订",
    AccessRequestCreated: "新访问申请",
    AccessApproved: "申请被批准",
    AccessRejected: "申请被拒绝",
    AccessRevoked: "授权被撤销",
    AccessRecorded: "病历被访问",
    UnauthorizedAttempt: "非法访问尝试",
  };
  return map[eventType] || eventType;
}

function onPopoverShow() {
  recent.value = recent.value.map((n) => ({ ...n, read: true }));
}

function clearAll() {
  recent.value = [];
}

onMounted(connect);

onBeforeUnmount(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (ws) {
    try { ws.close(); } catch {}
  }
});
</script>

<style scoped>
.bell {
  margin-right: 10px;
}
.bell-icon {
  font-size: 14px;
}
.notif-wrap {
  display: flex;
  flex-direction: column;
  max-height: 420px;
}
.notif-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #eee;
  margin-bottom: 8px;
  font-weight: 600;
  font-size: 13px;
}
.notif-list {
  overflow-y: auto;
  max-height: 320px;
}
.notif-item {
  padding: 8px 4px;
  border-bottom: 1px dashed #eee;
}
.notif-item:last-child {
  border-bottom: none;
}
.notif-item.unread {
  background-color: #f4fbff;
}
.notif-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 3px;
}
.notif-time {
  color: #999;
  font-size: 11px;
}
.notif-msg {
  font-size: 12px;
  color: #333;
  line-height: 1.4;
}
.notif-tx {
  font-size: 11px;
  color: #888;
  font-family: Consolas, monospace;
  word-break: break-all;
  margin-top: 2px;
}
.notif-foot {
  padding-top: 8px;
  border-top: 1px solid #eee;
  margin-top: 6px;
  text-align: right;
}
</style>
