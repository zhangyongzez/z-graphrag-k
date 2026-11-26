# 项目状态

> 最后更新：2026-11-26

## 需求完成情况

对照 `.requirement` 逐条：

### 1. 管理员

| # | 需求 | 状态 | 实现 |
|---|---|---|---|
| 1.1.1 | 系统配置页：模型只做下拉选择、CPU/GPU、向量库 | ✅ | `/api/admin/config` + 「系统配置」页 |
| 1.1.2 | 模型管理：LLM + Embedding 模型统一维护 | ✅ | `/api/admin/models` + 「模型管理」页 |
| 1.1.3 | 管理员看板：模型配置及路径、所有服务地址、所有知识库 | ✅ | `/api/admin/dashboard` + 「系统看板」页 |
| 1.1.4 | AD 域配置，配置后可通过 AD 登录 | ✅ | `ldap3`，配置页「AD 域认证」分组 |
| 1.1.5 | 管理员可查看普通用户页面并配置；管理员权限仅由环境变量决定、唯一 | ✅ | `ADMIN_USERNAME`/`ADMIN_PASSWORD`；管理员绕过权限 |

### 2. 普通用户

| # | 需求 | 状态 | 实现 |
|---|---|---|---|
| 2.1 | 只能用自己的知识库和管理员配置的知识库 | ✅ | `kb_owner` / `kb_grant` / `is_public` 三层权限 |
| 2.2 | 新建对话、配置知识库权重、选择大模型 | ✅ | 会话表 `kb_weights` + `llm_model`；加权融合检索 |

### 3. 知识库维护

| # | 需求 | 状态 | 实现 |
|---|---|---|---|
| 3.1 | 多格式解析：txt、word、ppt、excel、html、md、xml | ✅ | `ingest/loader.py`，另有 pdf / csv / json |
| 3.2 | 一键启动脚本 | ✅ | `start.py` / `start.bat` / `start.sh` |

### 第二轮需求（模型/配置职责分离）

| # | 需求 | 状态 | 实现 |
|---|---|---|---|
| 1 | 模型管理页管理所有模型；配置页只选择、不维护模型 | ✅ | `models` 表统一存 LLM + Embedding；配置页 `model_select` 字段 |
| 2 | 配置页可选运行时参数 | ✅ | 设备 / 批大小 / 温度 / Top-K / 切块 等 |
| 3 | 配置页有默认配置；模型为下拉选择（含 LLM 与 Embedding） | ✅ | `DEFAULT_CONFIG` + `/api/admin/config/reset`；下拉选项来自 `models` 表 |
| 4 | 模型管理显示系统已有模型，含 LLM 管理，新增后配置页增加选项 | ✅ | 按 kind 分 Tab；新增即可见 |
| 5 | 配置页与模型管理参数增加含义说明 | ✅ | 每个字段 `help`，测试 `test_config_fields_have_help` 保证 |

---

## 已完成

### 核心链路

- [x] 项目骨架 + 配置层（pydantic-settings，全环境变量驱动）
- [x] Embedding 工厂：local / openai / ollama / hash
- [x] 向量库工厂：Milvus Lite / Milvus 服务端
- [x] LLM 工厂：anthropic / openai / ollama / mock
- [x] 文档解析：txt / md / pdf / docx / html / json / csv / xml / pptx / xlsx
- [x] 切分：Markdown 按标题结构切 + 递归字符切分
- [x] RAG 问答链
- [x] 幂等入库（同一文件重复上传不产生重复数据）

### 认证与权限

- [x] 本地用户（scrypt 密码哈希）
- [x] 会话（HttpOnly Cookie，7 天有效）
- [x] AD 域登录（LDAP 绑定校验）
- [x] 管理员唯一，由环境变量决定
- [x] 知识库归属 / 授权 / 公共三层权限
- [x] 用户管理（增删改、停用）
- [x] 操作审计

### 多知识库

- [x] 知识库注册表（JSON 持久化，原子写入）
- [x] 每个知识库独立 collection
- [x] 集合统计（行数、文档清单）
- [x] 单文档删除
- [x] 知识库删除（级联删 collection + 权限）
- [x] 跨库加权融合检索

### 对话

- [x] 会话管理（新建 / 列表 / 删除）
- [x] 会话级知识库权重
- [x] 会话级大模型选择
- [x] 消息持久化
- [x] 流式输出（SSE）+ 引用展示

### 管理员

- [x] 配置页（5 个分组，42 个字段，热更新 / 需重启标注）
- [x] 模型管理（登记 / 导入 / 上传 / 删除）
- [x] 系统看板（模型、服务状态、全部知识库）
- [x] 用户管理 + 审计日志

### 基础设施

- [x] Docker Compose（Milvus 栈 + Neo4j，profile 隔离）
- [x] 77 个测试全通过
- [x] ruff 静态检查零告警
- [x] 一键启动脚本（Python / Windows .bat / Linux .sh）
- [x] 文档：架构、使用、环境、状态

## 实测基线

| 组件 | 状态 |
|---|---|
| bge-m3 本地 embedding（GPU） | ✅ 1024 维 |
| Milvus Lite | ✅ 读写正常 |
| Milvus 服务端 v2.5.4 | ✅ 完整栈运行中 |
| 端到端检索 | ✅ 语义命中准确 |
| 幂等入库 | ✅ 重复上传不产生重复数据 |
| 7 种格式上传 | ✅ 连续 3 轮全部成功 |
| 流式对话 | ✅ SSE 逐字输出 |
| 权限隔离 | ✅ 普通用户无法访问他人知识库 |

## 已知问题与约束

1. **Windows 控制台卡住会导致服务假死（已修复）**

   **现象**：服务运行一段时间后所有请求超时，CPU 占用 0%，日志停止更新，
   但进程还在。

   **原因**：`_SafeStderrSink` 在 uvicorn 的事件循环线程上同步写
   `sys.stderr`。当控制台停止消费输出时（Windows 最常见：在 cmd 窗口里
   **点了一下鼠标**，进入 QuickEdit 选择模式），`sys.stderr.write()`
   永久阻塞，整个事件循环随之停摆。

   py-spy 抓到的线程栈：
   ```
   MainThread:
     _SafeStderrSink.write (kgrag/logging.py)
     StreamSink.write (loguru)
     Logger.info
     RequestResponseCycle.send (uvicorn)
   ```

   **修复**：控制台 sink 加 `enqueue=True`，写操作移到 loguru 后台线程。

   **规避**：在 Windows 上运行服务时不要在 cmd 窗口里点鼠标选中文字；
   或改用 Windows Terminal（默认不开启 QuickEdit 选择模式）。

2. **管理员密码以环境变量为准**
   `ensure_admin()` 在启动时检查：若 `.env` 里的 `ADMIN_PASSWORD` 与库中
   不一致，会同步更新。所以改了 `.env` 重启即可生效，不必删库重建。
   `ADMIN_PASSWORD` 为空时不会清空已有密码。

3. **Milvus Lite 只能有一个连接**
   milvus-lite 在进程内起一个本地 gRPC 服务，第二个 `MilvusClient(同一路径)`
   会互相踢下线（`Too many pings` / `Open local milvus failed`）。
   `kb/collections.py` 现在复用 langchain 的客户端，不要自己再建。

4. **minio 官方镜像已从 Docker Hub 下架**
   改用 `alpine/minio:RELEASE.2025-10-15T17-29-55Z`。
   该镜像 entrypoint 已是 `minio`，compose 里 command 只写子命令；
   且需要 `user: "0:0"` 避免命名卷权限问题。

5. **Milvus 服务端 `get_collection_stats` 有延迟**
   刚写入时基于已落盘段统计，会返回 0。
   已改为遍历实际行数，牺牲一点性能换准确性。

6. **milvus-lite 的 gRPC 噪音**
   退出时打印 `NotImplementedError: Method not implemented!`
   （AllocTimestamp），是 milvus-lite 对 pymilvus 3.x 部分方法未实现，
   不影响功能。

7. **faiss AVX 警告**
   `ModuleNotFoundError: No module named 'faiss.swigfaiss_avx512*'`，
   是 faiss 探测可选优化模块，已锁定 1.12.0。

8. **Git Bash 下 curl 传中文文件名乱码**
   终端编码问题，浏览器和 httpx 正常。

9. **系统看板响应约 2 秒**
   需要并发探测向量库 / 大模型 / Ollama 三个外部地址（各 1 秒超时）。
   前端已做缓存，切页时先显示上次数据再后台刷新。

## 待办

- [ ] 配置真实 LLM（当前默认 anthropic，需填 API Key）
- [ ] Neo4j 图谱增强（GraphRAG）
- [ ] 文档解析增强（OCR、表格、代码）
- [ ] 知识库文档预览
- [ ] 批量导入（服务器本地路径）的页面入口
- [ ] 配置页的「测试连接」按钮（AD 已有接口，前端未接）
