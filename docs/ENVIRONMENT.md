# 环境与踩坑记录

本文件记录搭建过程中遇到的环境问题及处理方式，方便换机器时复现。

## 1. torch / faiss / transformers 的 Windows DLL 导入顺序

**现象**

```
OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
Error loading "...\torch\lib\c10.dll" or one of its dependencies.
```

**根因**

在 Windows 上，`faiss`（`milvus-lite` 的依赖）或 `transformers` 如果先于
`torch` 被导入，会让 torch 的 DLL 初始化失败。实测组合：

| 导入顺序 | 结果 |
|---|---|
| `import torch` → `import faiss` | OK |
| `import faiss` → `import torch` | 失败 |
| `import torch` → `import transformers` | OK |
| `import transformers` → `import torch` | 失败 |

**处理**

`src/kgrag/__init__.py` 在包导入的最早期（早于任何 langchain / milvus /
faiss 的间接导入）预加载 torch，仅当 `EMBEDDING_PROVIDER=local` 时生效，
其他模式不付加载开销。`LocalEmbeddings.__init__` 里也做了同样的保护。

**排查过程**

```bash
# 逐个验证导入顺序
python -c "import faiss; import torch"
python -c "import torch; import faiss"
```

## 2. faiss-cpu 1.16.0 在 AVX512 机器上缺 .pyd

**现象**

```
ModuleNotFoundError: No module named 'faiss.swigfaiss_avx512_spr'
ModuleNotFoundError: No module named 'faiss.swigfaiss_avx512'
```

**根因**：1.16.0 的 wheel 只带了 `_swigfaiss.pyd`，但 `loader.py` 在检测到
CPU 支持 AVX512 时会去找 `swigfaiss_avx512*.pyd`。

**处理**：`pyproject.toml` 里把 Windows 的 faiss-cpu 锁到 `<1.13`。

## 3. uv 间歇性网络失败

**现象**

```
error: tcp connect error
cause: 以一种访问权限不允许的方式做了一个访问套接字的尝试。 (os error 10013)
```

**原因**：公司网络策略对部分进程放行不一致，uv 偶发被拦截。

**处理**：重试通常即可；持续失败时改用 pip：

```bash
.venv/Scripts/python.exe -m pip install <pkg>
```

注意 `uv venv` 创建的环境默认不含 pip，需要先执行：

```bash
.venv/Scripts/python.exe -m ensurepip --upgrade
```

## 4. torch 下载源

- PyPI 上的 `torch` 是 CPU 版
- CUDA 版必须走 `https://download.pytorch.org/whl/cuXXX`
- `download-r2.pytorch.org` 在本网络下被拦截，`download.pytorch.org` 可访问

安装 CUDA 版：

```bash
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch
```

> 如果 uv 报 `download-r2.pytorch.org` 连接失败，说明它重定向到了 R2 域名。
> 可尝试用浏览器/curl 从 `download.pytorch.org` 下载 whl 后本地安装。

## 5. HuggingFace 权重下载

- `huggingface.co` 可访问
- `cdn-lfs.huggingface.co`（权重 CDN）在部分网络下不可达

**处理**：设置镜像

```bash
HF_ENDPOINT=https://hf-mirror.com
```

模型下载到本地后，用 `EMBEDDING_LOCAL_PATH` 指向本地目录，并设置
`HF_HUB_OFFLINE=1` 避免启动时联网。

## 6. Neo4j 端口冲突

宿主机 7687 已被既有容器占用（memgraph）。`docker-compose.yml` 里 Neo4j
映射到 **7688**，通过 `NEO4J_URI` 配置。

## 7. Windows 控制台导致服务假死（重要）

**现象**

服务运行一段时间后所有请求超时，浏览器一直转圈，但：
- 进程还在，端口还在监听
- CPU 占用 0%
- 日志停止更新
- 重启后恢复正常

**原因**

Windows 控制台有 **QuickEdit（快速编辑）模式**：在 cmd 窗口里点一下鼠标，
就会进入「选择文本」状态，此时**控制台停止读取程序的输出**。

原来的日志实现在 uvicorn 的事件循环线程上同步写 `sys.stderr`：

```python
def write(self, message):
    sys.stderr.write(message)   # 控制台不读了 -> 永久阻塞
    sys.stderr.flush()
```

控制台缓冲区写满后，`write()` 就永远不返回，整个事件循环随之停摆。

用 py-spy 可以确认：

```bash
py-spy dump --pid <PID>
```

```
Thread ... (idle): "MainThread"
    _SafeStderrSink.write (kgrag\logging.py:43)
    StreamSink.write (loguru\_simple_sinks.py:31)
    Logger.info (loguru\_logger.py)
    RequestResponseCycle.send (uvicorn\protocols\http\httptools_impl.py:482)
    ...事件循环...
```

**修复**

控制台 sink 加 `enqueue=True`，把写操作交给 loguru 的后台线程：

```python
_loguru_logger.add(
    _SafeStderrSink(),
    enqueue=True,   # 关键
    ...
)
```

这样控制台卡住最多让队列积压，不会拖死事件循环。

**规避建议**

- 不要在运行服务的 cmd 窗口里点鼠标选中文字
- 推荐用 **Windows Terminal**（默认不开启 QuickEdit 选择模式）
- 或用 `python start.py > server.log 2>&1` 把输出重定向到文件

## 8. 当前环境基线

| 项 | 版本 |
|---|---|
| Python | 3.12.10 |
| torch | 2.14.1+cu126（本地 wheel） |
| CUDA | 12.8 驱动 / 12.6 运行时 |
| GPU | RTX A4000 16GB |
| faiss-cpu | 1.12.0 |
| 向量库 | milvus-lite 3.2.2 |
| Embedding | BAAI/bge-m3（1024 维，本地） |
