# Caliper 压测（迭代 8）

本目录提供 Hyperledger Caliper 2.x 的链上性能基准，三场景：

| 场景 | 目标 | 负载类型 |
|------|------|---------|
| `create-records` | TPS ≥ 30 | 500 笔写入，fixed-rate 30 TPS |
| `query-records` | TPS ≥ 200 | 60s 只读，fixed-load 50 并发 |
| `mixed-rw` | 观察 P95/错误率 | 90s 混合，80% 查 20% 写 |

## 前置条件

```bash
# 1. 全局安装 Caliper CLI（0.5.x 系列）
npm install -g @hyperledger/caliper-cli@0.5

# 2. 绑定 Fabric SUT
mkdir -p ~/caliper-workspace && cd ~/caliper-workspace
caliper bind --caliper-bind-sut fabric:2.2

# 3. 确保真实 Fabric 网络运行
bash fabric-network/scripts/bootstrap.sh
```

## 运行

```bash
# 在项目根目录执行
caliper launch manager \
  --caliper-workspace . \
  --caliper-benchconfig caliper/benchmarks/medshare-benchmark.yaml \
  --caliper-networkconfig caliper/networks/medshare-network.yaml
```

运行完成后 Caliper 会在 `report.html` 输出：
- 每场景的 TPS、Avg/Min/Max/P95 延迟
- peer/orderer 容器的 CPU/内存曲线
- 错误率与重试次数

## 验证目标

- 场景 2（query）**TPS ≥ 200**
- 场景 1（create）**TPS ≥ 30**
- 混合场景 **错误率 < 1%**

## 索引对比（迭代 7 遗留任务）

把 `QueryRecordsByHospital` 的 `use_index` 去除后再跑一次 `query-records`（改为调用富查询），
对比带 / 不带索引的 TPS：**目标 有索引 TPS > 无索引 × 3.3**（= 无索引 <0.3 × 有索引）。
