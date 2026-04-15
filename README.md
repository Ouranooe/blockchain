# 项目部署说明（MedShare）

本项目使用 Docker Compose 部署，包含：
- 前端：Vue3 + Nginx
- 后端：FastAPI
- 网关：Node.js Fabric Gateway
- 数据库：MySQL 8.0
- 区块链：Hyperledger Fabric（由 bootstrap 脚本初始化）

## 1. 环境要求

- Windows 10/11（推荐）或 Linux/macOS
- Docker Desktop（需包含 Docker Compose v2）
- 确认 Docker 已启动

建议资源：
- 内存 >= 8GB
- 磁盘可用空间 >= 10GB（首次拉取镜像较大）

## 2. 进入项目目录

```powershell
cd "D:\Files In Disk D\codeJunior\blockChain\design"
```

## 3. 首次部署（必须先做）

首次部署需要先初始化 Fabric 网络并部署链码。

```powershell
docker compose --profile fabric-init up --build fabric-bootstrap
```

说明：
- 该步骤会下载 Fabric 二进制、拉取镜像、启动 test-network、创建通道、部署链码。
- 首次执行时间较长，属于正常现象。
- 看到 bootstrap 成功结束后，再执行下一步。

然后启动业务服务：

```powershell
docker compose up -d --build
```

## 4. 日常启动（非首次）

```powershell
docker compose up -d
```

如果你发现 Fabric 相关容器不存在或链码不可用，再执行一次第 3 步的 bootstrap 初始化。

## 5. 部署成功验证

### 5.1 查看容器状态

```powershell
docker compose ps
```

期望看到：
- `medshare-frontend` Up
- `medshare-backend` Up
- `medshare-gateway` Up
- `medshare-mysql` Up (healthy)

### 5.2 访问地址

- 前端：http://localhost:5173
- 后端 Swagger：http://localhost:8000/docs
- Gateway 健康检查：http://localhost:3000/health

## 6. 测试账号

- 管理员：`admin / 123456`
- 患者：`patient1 / 123456`、`patient2 / 123456`
- 医院：`hospital_a / 123456`、`hospital_b / 123456`

## 7. 停止服务

```powershell
docker compose down
```

## 8. 完整清理（重置 Fabric + 业务容器）

```powershell
docker compose down
bash fabric-network/scripts/teardown.sh
```

说明：
- 第 2 条会清理 Fabric test-network 相关容器、通道和证书。
- 在 Windows 下可用 Git Bash 执行 `bash` 命令。

## 9. 常见问题

### 9.1 访问接口报错：Docker 连接失败

先确认 Docker Desktop 已启动，再重试。

### 9.2 Gateway 返回 500

常见原因是 Fabric 未初始化或链码未部署，重新执行：

```powershell
docker compose --profile fabric-init up --build fabric-bootstrap
```

### 9.3 页面中文显示异常

重建 MySQL 和后端容器：

```powershell
docker compose up -d --build mysql backend
```

## 10. 一条命令完成首次部署（可选）

```powershell
docker compose --profile fabric-init up --build fabric-bootstrap; docker compose up -d --build
```

---
如需我再补一版“服务器部署（公网 Linux + Nginx 反向代理 + HTTPS）”README，我可以直接在当前仓库追加 `README.deploy-server.md`。
