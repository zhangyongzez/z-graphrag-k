# z-graphrag-k

基于 Milvus + LangChain 的可插拔知识库系统，带完整的 Web 管理界面。

支持多知识库隔离、多格式文档解析、流式问答、AD 域登录，
向量库 / Embedding / 大模型均可插拔切换。

---

## 一、架构说明

### 分层结构

```
┌──────────────────────────────────────────────────────┐
│  Web 界面（原生 HTML/CSS/JS，无构建步骤）              │
│  看板 · 知识库 · 对话 · 系统配置 · 模型管理 · 用户管理   │
└───────────────────────┬──────────────────────────────┘
                        │ HTTP / SSE
┌───────────────────────▼──────────────────────────────┐
│  FastAPI                                             │
│  ├── routes_auth   登录 / 用户管理                    │
│  ├── routes_admin  配置 / 模型 / 看板 / 审计          │
│  ├── routes_kb     知识库 CRUD / 上传 / 权限          │
│  └── routes_chat   会话 / 检索 / 流式问答              │
└───────────────────────┬──────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   ┌─────────┐    ┌──────────┐    ┌─────────┐
   │ ingest  │    │retrieval │    │   kb    │
   │解析/切分 │    │检索/加权  │    │注册表/权限│
   └────┬────┘    └────┬─────┘    └────┬────┘
        └──────────────┼───────────────┘
                       ▼
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌──────────┐  ┌───────────┐  ┌─────────┐
  │embeddings│  │vectorstore│  │   llm   │
  └──────────┘  └───────────┘  └─────────┘
   local/openai   Milvus Lite    Claude/
   /ollama/hash   /服务端        OpenAI/Ollama
```

### 可插拔设计

所有实现由工厂统一产出，切换只改配置，不动代码：

| 能力 | 可选实现 | 工厂 |
|---|---|---|
| 向量库 | Milvus Lite / Milvus 服务端 | `get_vectorstore()` |
| Embedding | 本地 / OpenAI 兼容 / Ollama / 哈希 | `get_embeddings()` |
| 大模型 | Claude / OpenAI 兼容 / Ollama / Mock | `get_llm()` |

### 模型与配置的职责边界

这是系统的核心设计：**模型在「模型管理」维护，配置页只做选择。**

```
┌────────────────────────┐        ┌────────────────────────┐
│ 模型管理（models 表）   │        │ 系统配置（app_config）  │
│                        │        │                        │
│ 每条记录 = 完整模型     │        │ llm_model_id ──────┐   │
│ · kind: llm/embedding  │◀───────┼─ embedding_model_id┘   │
│ · provider             │  引用   │                        │
│ · model/base_url/密钥  │        │ + 运行时参数：          │
│ · path/dim             │        │   device / batch_size  │
│                        │        │   temperature / top_k  │
└────────────────────────┘        └────────────────────────┘
```

**回退链**：配置页选择 → `models` 表记录 → `.env` 默认值

这样换 API Key 只需改模型记录，同一模型可被多个会话引用。

### 数据模型

| 表 | 用途 |
|---|---|
| `users` / `sessions` | 用户与会话 |
| `kb_owner` / `kb_grant` | 知识库归属与授权 |
| `models` | 模型注册表（LLM + Embedding） |
| `app_config` | 运行时配置（覆盖 .env） |
| `conversations` / `messages` | 对话与消息 |
| `audit_log` | 操作审计 |

**知识库隔离**：每个知识库对应一个 Milvus collection（`kb_<12位hex>`），
注册表持久化在 `data/registry.json`。

**幂等入库**：主键 = `sha1(知识库 + 文件标识 + 块序号)`，
同一文件重复上传原地覆盖，不产生重复数据。

### 权限模型

| 角色 | 来源 | 可见范围 |
|---|---|---|
| 管理员 | 环境变量，唯一 | 全部 |
| 普通用户 | 本地创建 / AD 登录 | 自己的 + 被授权的 + 公共的 |

判定顺序：管理员 → 拥有者 → 公共 → 授权 → 不可见。

### 目录结构

```
src/kgrag/
├── config.py          环境变量配置（pydantic-settings）
├── db.py              SQLite 数据层
├── settings/          运行时配置 + 配置页表单结构
├── models.py          模型注册表
├── conversations.py   会话与消息
├── audit.py           操作审计
├── auth/              认证（本地 / AD）
├── embeddings/        Embedding 工厂
├── llm/               LLM 工厂
├── vectorstore/       向量库工厂
├── ingest/            多格式解析 + 切分 + 入库
├── retrieval/         检索 + 加权融合 + 问答
├── kb/                知识库注册表 / 集合操作 / 权限
├── api/               FastAPI 路由
└── web/               管理页面（HTML/CSS/JS）
```

---

## 二、系统说明

### 功能模块

| 模块 | 说明 |
|---|---|
| **看板** | 知识库、文档、向量块统计；当前模型配置 |
| **知识库** | 创建、上传文档、删除文档、删除知识库 |
| **对话** | 多会话、知识库权重、模型选择、流式输出、引用展示 |
| **系统配置** | 选择模型 + 调整运行时参数，每项带说明，可一键恢复默认 |
| **模型管理** | 维护 LLM 与 Embedding 模型（含连接信息），新增后配置页可选 |
| **系统看板** | 模型配置、服务健康状态、全部知识库（管理员） |
| **用户管理** | 用户增删改、启停用、操作审计（管理员） |

### 支持的文件格式

| 类别 | 扩展名 |
|---|---|
| 文本 | `txt` `md` `markdown` |
| 文档 | `pdf` `doc` `docx` |
| 演示 | `pptx` |
| 表格 | `xlsx` `xls` `csv` |
| 网页 / 标记 | `html` `htm` `xml` `json` |

### 运行时参数

| 参数 | 说明 |
|---|---|
| `embedding_device` | 推理设备：`auto` / `cpu` / `cuda` |
| `embedding_batch_size` | 批大小，显存不足时调小 |
| `embedding_max_length` | 单条文本最大 token 数 |
| `embedding_normalize` | 向量归一化，余弦相似度时应开启 |
| `llm_temperature` | 温度，0 最确定 |
| `llm_max_tokens` | 单次回答最大生成长度 |
| `llm_timeout` | 调用超时（秒） |
| `retrieval_top_k` | 检索返回片段数，一般 3~8 |
| `retrieval_score_threshold` | 相似度阈值，0 表示不过滤 |
| `chunk_size` | 切块大小，中文一般 500~1000 |
| `chunk_overlap` | 切块重叠，一般 10%~20% |

### 部署形态

| 模式 | 说明 |
|---|---|
| **Milvus Lite** | 嵌入式本地文件，零外部依赖，适合单机开发 |
| **Milvus 服务端** | etcd + minio + milvus，支持多客户端并发，适合生产 |

---

## 三、启动说明

### 环境要求

- Python 3.11 ~ 3.13
- Windows / Linux / macOS

### 一键启动（推荐）

```bash
# Windows：双击 start.bat，或在终端执行
start.bat

# Linux / macOS
chmod +x start.sh && ./start.sh

# 或者直接用 Python
python start.py
```

首次运行会自动完成：

1. 创建虚拟环境 `.venv`
2. 安装依赖
3. 从 `.env.example` 生成 `.env`
4. 初始化数据库
5. 启动服务并打印管理员密码

启动后打开 <http://localhost:8000>。

**可选参数**：

```bash
python start.py --port 9000      # 指定端口
python start.py --no-browser     # 不自动打开浏览器
python start.py --reload         # 开发模式自动重载
python start.py --skip-deps      # 跳过依赖检查
```

### 手动安装

```bash
cp .env.example .env
# 编辑 .env，至少设置 ADMIN_PASSWORD

python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -e ".[local,dev]"

python -m kgrag.cli doctor      # 自检
python -m kgrag.cli serve       # 启动
```

### 必填配置

`.env` 里最关键的一项：

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=你的密码
```

> 管理员账号只由环境变量决定且唯一。修改 `ADMIN_PASSWORD` 后重启即生效。

其余配置（模型、向量库、检索参数等）都可以在启动后由管理员在
「系统配置」和「模型管理」页面维护，无需改文件。

### 首次使用流程

> **关于「开箱即用」**
>
> 系统启动后**开箱即可登录使用**，但需要先配置模型才能用完整功能。
>
> **未配置模型时也能用**：
> - 首次登录会弹出配置向导，引导你完成设置
> - 未配大模型时，对话自动降级为「**纯检索模式**」——直接返回知识库中
>   匹配的原文片段（不生成总结）
> - 只有 Embedding 是必需的，没有它无法检索文档
>
> 侧边栏底部的状态显示：
> - `lite` → 向量库已就绪
> - <span style="color:orange">Embedding 未配置</span> → **黄色警告**，必须配置
> - <span style="color:orange">LLM 未配置</span> → **黄色警告**，不配则降级为纯检索
>
> 详细的模型配置说明见 [**模型配置手册**](docs/MODEL_CONFIG.md)。

1. 用管理员登录
2. 进入「**模型管理**」，新增 Embedding 模型和 LLM 模型
   - 本地模型：填目录路径（含 `config.json` + 权重文件）
   - 远程模型：填 API 地址、API Key、模型标识
   - 详见 [模型配置手册](docs/MODEL_CONFIG.md)
3. 进入「**系统配置**」，在下拉框中选择刚新增的模型
4. 进入「**知识库**」，创建知识库并上传文档
5. 进入「**对话**」，选择知识库和模型，开始提问

### Docker 部署

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env，至少设置 ADMIN_PASSWORD

# 2. 启动（默认用 Milvus Lite，数据存在命名卷里）
docker compose up -d --build

# 3. 查看日志
docker compose logs -f app

# 4. 访问
# http://localhost:8000
```

**常用组合**：

```bash
docker compose up -d                                  # 仅应用（Milvus Lite）
docker compose --profile milvus up -d                 # 应用 + Milvus 服务端
docker compose --profile graph up -d                  # 应用 + Neo4j
docker compose --profile milvus --profile graph up -d # 全部
```

**数据持久化**：

| 挂载点 | 内容 |
|---|---|
| `app_data:/app/data` | SQLite、向量库文件、日志、上传文件 |
| `./models:/app/models:ro` | 本地 embedding 模型（可选，只读挂载） |

**启用本地 embedding**（镜像会变大，含 torch）：

```bash
docker compose build --build-arg INSTALL_LOCAL=1
docker compose up -d
```

> 容器内以非 root 用户 `kgrag` 运行；`app_data` 卷首次创建时权限已配好。

### 切换到 Milvus 服务端

```bash
# 1. 启动 Milvus 栈（etcd + minio + milvus）
docker compose --profile milvus up -d

# 2. 确认健康
docker compose --profile milvus ps

# 3. 修改 .env（用 Docker 时 milvus 地址是容器名）
VECTORSTORE_PROVIDER=milvus
MILVUS_URI=http://milvus:19530
```

不用 Docker 时，`MILVUS_URI` 填 `http://localhost:19530`。

### Linux 直接部署

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[local,dev]"

cp .env.example .env
vim .env                        # 至少设置 ADMIN_PASSWORD

chmod +x start.sh && ./start.sh
```

生产环境建议用 systemd 托管：

```ini
# /etc/systemd/system/kgrag.service
[Unit]
Description=z-graphrag-k
After=network.target

[Service]
Type=simple
User=kgrag
WorkingDirectory=/opt/z-graphrag-k
ExecStart=/opt/z-graphrag-k/.venv/bin/python start.py --no-browser
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kgrag
```

### 命令行工具

```bash
python -m kgrag.cli doctor              # 自检
python -m kgrag.cli serve               # 启动服务
python -m kgrag.cli ingest ./docs       # 批量入库
python -m kgrag.cli search "问题"        # 检索
python -m kgrag.cli ask "问题"           # 问答
```

### 开发

```bash
make test      # 跑测试（77 个）
make lint      # 静态检查
make fmt       # 格式化
```

---

## 文档

- [**模型配置手册**](docs/MODEL_CONFIG.md) — **本地模型 / API 接口怎么配，各服务商参数对照**
- [架构说明](docs/ARCHITECTURE.md) — 详细设计、数据模型、权限模型
- [使用指南](docs/USAGE.md) — 各页面操作说明、接口速查
- [环境说明](docs/ENVIRONMENT.md) — 环境依赖与踩坑记录
- [当前状态](docs/STATUS.md) — 需求对照、已知问题

接口文档：<http://localhost:8000/docs>
