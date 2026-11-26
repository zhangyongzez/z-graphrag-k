"""命令行入口：``kgrag <command>``。"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from kgrag import __version__

app = typer.Typer(
    name="kgrag",
    help="z-graphrag-k 知识库命令行",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def serve(
    host: str = typer.Option(None, help="监听地址，默认取 APP_HOST"),
    port: int = typer.Option(None, help="监听端口，默认取 APP_PORT"),
    reload: bool = typer.Option(False, "--reload", help="开发模式自动重载"),
) -> None:
    """启动 API 服务。"""
    import uvicorn

    from kgrag.config import get_settings

    s = get_settings()
    uvicorn.run(
        "kgrag.api.app:app",
        host=host or s.app_host,
        port=port or s.app_port,
        reload=reload,
    )


@app.command()
def ingest(
    path: str = typer.Argument(..., help="文件或目录路径"),
    tags: str = typer.Option("", help="逗号分隔的标签"),
) -> None:
    """把文件/目录写入向量库。"""
    from kgrag.ingest import ingest_path

    p = Path(path)
    if not p.exists():
        console.print(f"[red]路径不存在: {path}[/red]")
        raise typer.Exit(1)

    extra = {"tags": [t.strip() for t in tags.split(",") if t.strip()]} if tags else None
    with console.status(f"正在入库 {path} ..."):
        result = ingest_path(p, extra_metadata=extra)
    console.print(
        f"[green]完成[/green] 文件 {result['files']} 个，"
        f"切块 {result['chunks']} 个，跳过 {result['skipped']} 个"
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="检索词"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
) -> None:
    """只做向量检索，不调用 LLM。"""
    from kgrag.retrieval import retrieve

    docs = retrieve(query, top_k=top_k)
    if not docs:
        console.print("[yellow]没有命中结果[/yellow]")
        return
    table = Table(title=f"检索结果（{len(docs)} 条）")
    table.add_column("#", width=3)
    table.add_column("来源", style="cyan", width=28)
    table.add_column("内容", width=70)
    for i, d in enumerate(docs, 1):
        table.add_row(
            str(i),
            d.metadata.get("filename", "?"),
            d.page_content[:120].replace("\n", " "),
        )
    console.print(table)


@app.command()
def ask(
    question: str = typer.Argument(..., help="问题"),
    top_k: int = typer.Option(None, "--top-k", "-k"),
) -> None:
    """检索并回答。"""
    from kgrag.retrieval import answer

    with console.status("检索并生成回答..."):
        result = answer(question, top_k=top_k)
    console.print(f"\n[bold green]回答[/bold green]\n{result['answer']}\n")
    if result["sources"]:
        console.print("[bold]来源[/bold]")
        for s in result["sources"]:
            console.print(f"  - {s.get('filename')} (chunk {s.get('chunk_index')})")


@app.command()
def doctor() -> None:
    """自检：检查配置与各组件连通性。"""
    from kgrag.config import get_settings

    s = get_settings()
    table = Table(title=f"z-graphrag-k {__version__} 自检")
    table.add_column("组件", style="cyan")
    table.add_column("配置")
    table.add_column("状态")

    table.add_row("向量库", s.vectorstore_provider, _check_vectorstore())
    table.add_row("Embedding", s.embedding_provider, _check_embedding())
    table.add_row("LLM", s.llm_provider, _check_llm())
    table.add_row("图库", "enabled" if s.graph_enabled else "disabled", "-")
    console.print(table)


def _check_vectorstore() -> str:
    try:
        from kgrag.vectorstore import get_vectorstore

        get_vectorstore()
        return "[green]OK[/green]"
    except Exception as e:  # noqa: BLE001
        return f"[red]失败: {e}[/red]"


def _check_embedding() -> str:
    try:
        from kgrag.embeddings import get_embeddings

        v = get_embeddings().embed_query("测试")
        return f"[green]OK[/green] dim={len(v)}"
    except Exception as e:  # noqa: BLE001
        return f"[red]失败: {e}[/red]"


def _check_llm() -> str:
    try:
        from kgrag.config import get_settings
        from kgrag.llm import get_llm

        get_llm()
        return "[green]OK[/green]" if get_settings().llm_provider != "mock" else "[yellow]mock[/yellow]"
    except Exception as e:  # noqa: BLE001
        return f"[red]失败: {e}[/red]"


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
