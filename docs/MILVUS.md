# Milvus 部署与配置

本文档说明如何在 **Milvus Lite** 和 **Milvus 服务端** 之间选择与切换。

---

## 一、两种模式对比

| | Milvus Lite | Milvus 服务端 |
|---|---|---|
| **是什么** | 嵌入式库，跑在 Python 进程内 | 独立服务（etcd + minio + milvus） |
| **安装** | pip 依赖，随代码装好 | 需要 Docker 或手动部署 |
| **配置** | 零配置 | 需要填地址、可能还要 Token |
| **数据** | 单个 `.db` 文件 | 分布式存储 |
| **并发** | 单进程独占 | 多客户端并发 |
| **规模** | 百万级以内 | 千万级以上 |
| **适用** | 开发、单机、小规模 | 生产、多实例、大规模 |

**结论**：

- 单机自用 / 开发调试 → **Milvus Lite**（默认，不用改任何东西）
- 多用户 / 多实例 / 数据量大 → **Milvus 服务端**

---

## 二、Milvus Lite（默认）

### 配置

无需任何操作。`.env` 默认：

```bash
VECTORSTORE_PROVIDER=lite
VECTORSTORE_LITE_PATH=./data/milvus/kgrag.db
```

### 数据文件

所有向量存在 `data/milvus/kgrag.db`，备份直接复制这个文件即可。

### 限制

- ⚠️ **只能有一个进程访问**。同时开两个服务实例会报 `Too many pings`
- ⚠️ 不支持多客户端并发

### 为什么看板显示「本地文件」

因为 Lite 就是文件，没有网络端口。看板显示的是数据文件路径，这是正确的。

---

## 三、Milvus 服务端

### 3.1 前置要求

- Docker 20.10+
- Docker Compose v2
- 至少 4 GB 可用内存
- 端口 `19530`（gRPC）、`9091`（HTTP）、`9000/9001`（minio）、`2379`（etcd）未被占用

### 3.2 启动

项目已内置 `docker-compose.yml`，直接：

```bash
# 启动 Milvus 栈（etcd + minio + milvus）
docker compose --profile milvus up -d

# 确认三个容器都 healthy
docker compose --profile milvus ps
```

预期输出：

```
NAME                  STATUS
z-graphrag-k-etcd-1     Up (healthy)
z-graphrag-k-minio-1    Up (healthy)
z-graphrag-k-milvus-1   Up (healthy)   0.0.0.0:19530->19530/tcp
```

> ⚠️ **首次启动需要 60~90 秒**，milvus 要等 etcd 和 minio 就绪。
> 这期间 `docker compose ps` 可能显示 `starting`，属正常。

### 3.3 验证服务可用

```bash
# 方式一：HTTP 健康检查
curl http://localhost:9091/healthz

# 方式二：看容器状态
docker compose --profile milvus ps

# 方式三：看日志有没有报错
docker compose logs milvus | tail -20
```

### 3.4 切换到服务端

**方式 A：管理页面（推荐）**

1. 登录 → 「系统配置」→「向量库」标签
2. 提供方选 `milvus`
3. 地址填 `http://localhost:19530`
4. 保存
5. **重启服务**（向量库连接需要重建）

**方式 B：改 .env**

```bash
VECTORSTORE_PROVIDER=milvus
MILVUS_URI=http://localhost:19530
```

然后重启服务。

### 3.5 地址怎么填

| 部署方式 | MILVUS_URI |
|---|---|
| 服务端和本应用在同一台机器 | `http://localhost:19530` |
| 服务端在另一台机器 | `http://192.168.1.100:19530` |
| **应用也在 Docker 里** | `http://milvus:19530`（用容器名，不是 localhost） |

> ⚠️ **最常见的坑**：应用在 Docker 里、Milvus 也在 Docker 里，
> 却填了 `localhost` —— 容器内的 localhost 指向容器自己，连不上。
> 必须用 compose 里的服务名 `milvus`。

### 3.6 鉴权（可选）

默认无鉴权。如需开启，编辑 `docker-compose.yml` 的 milvus 服务：

```yaml
milvus:
  environment:
    ETCD_ENDPOINTS: etcd:2379
    MINIO_ADDRESS: minio:9000
    # 新增：
    COMMON_SECURITY_AUTHORIZATION_ENABLED: "true"
```

然后在「系统配置」填写 `milvus_token`，格式 `用户名:密码`。

> 开启鉴权前需先用 Milvus 客户端创建用户，否则连不上。

### 3.7 停止与清理

```bash
# 停止（保留数据）
docker compose --profile milvus down

# 停止并删除数据卷（危险，数据全丢）
docker compose --profile milvus down -v
```

---

## 四、常见问题

### 切换后看板显示红色 err

说明连不上服务端。依次检查：

```bash
# 1. 容器是否在跑
docker compose --profile milvus ps

# 2. 端口是否通
curl http://localhost:9091/healthz

# 3. 看 milvus 日志
docker compose logs milvus | tail -30

# 4. 地址填对了吗
#    - 本机部署：localhost:19530
#    - Docker 内部：milvus:19530
```

### `Too many pings` 或 `Open local milvus failed`

说明同时有多个进程访问同一个 Lite 文件。检查：

- 是不是开了两个服务实例？
- 有没有残留的 Python 进程？

解决：关掉多余进程，或切换到 Milvus 服务端。

### 从 Lite 切到服务端，原有知识库没了

**这是正常的**，两种模式的数据完全独立：

- Lite 的数据在 `data/milvus/kgrag.db`
- 服务端的数据在 Docker 卷里

切换后需要**重新上传文档**。如果数据重要，建议切换前先备份 Lite 文件。

### 服务端启动后很快退出

```bash
docker compose logs milvus | grep -i error
```

常见原因：
- 内存不足（需要 ≥4 GB）
- 端口被占用
- etcd 或 minio 没起来

### 能否内置 Milvus 服务端？

**不能，也不建议。** 原因：

1. Milvus 服务端是 **etcd + minio + milvus 三个独立进程**，无法打包进 Python 包
2. 镜像体积超 2 GB
3. 它天生是分布式服务，需要独立部署、独立扩缩容、独立备份
4. **内置版已经存在**——就是 Milvus Lite

---

## 五、生产环境建议

### 资源规划

| 数据量 | 内存 | 磁盘 | 说明 |
|---|---|---|---|
| < 100 万向量 | 4 GB | 20 GB | 单机足够 |
| 100 万 ~ 1000 万 | 16 GB | 200 GB | 建议独立部署 |
| > 1000 万 | 32 GB+ | 1 TB+ | 用 Milvus 集群版 |

### 数据备份

**Lite**：

```bash
# 停服务后直接复制
cp data/milvus/kgrag.db backup/kgrag-$(date +%F).db
```

**服务端**：

```bash
# 备份 milvus 数据卷
docker run --rm -v z-graphrag-k_milvus_data:/data \
  -v $(pwd)/backup:/backup alpine \
  tar czf /backup/milvus-$(date +%F).tar.gz -C /data .
```

### 反向代理

Milvus 的 gRPC 需要 HTTP/2 支持：

```nginx
server {
    listen 19530 http2;
    location / {
        grpc_pass grpc://127.0.0.1:19530;
    }
}
```

---

## 六、配置速查

| 场景 | VECTORSTORE_PROVIDER | MILVUS_URI |
|---|---|---|
| 开发 / 单机 | `lite` | 不填 |
| 本机起服务端 | `milvus` | `http://localhost:19530` |
| 远程服务端 | `milvus` | `http://192.168.1.100:19530` |
| 应用和服务端都在 Docker | `milvus` | `http://milvus:19530` |
| 应用在 Docker、服务端在宿主机 | `milvus` | `http://host.docker.internal:19530` |
