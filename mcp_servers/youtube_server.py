"""youtube_server.py — YouTube 搜索 MCP 服务器

基于 FastMCP 构建的独立 MCP 服务器，将 ``search_youtube_video`` 工具
投需接受标准 MCP 协议连接的客户端。

业务逻辑全部委托至 ``fithealth_agent.youtube_search`` 模块，
保持本文件的责任单一性。

运行方式：
    # 供调试时独立运行：
    python mcp_servers/youtube_server.py

环境变量（必须配置）：
    YOUTUBE_API_KEY: 从 Google Cloud Console 获取的 YouTube Data API v3 密钥。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，使得当本文件作为子进程独立启动时能找到 fithealth_agent
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
from fastmcp import FastMCP

from fithealth_agent.youtube_search import search_videos

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [youtube_mcp] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

mcp = FastMCP(name="YouTubeSearchServer")


@mcp.tool()
def search_youtube_video(query: str, max_results: int = 3) -> str:
    """Search YouTube for fitness exercise tutorial videos.

    Call this tool when recommending a specific exercise or explaining
    how to perform one correctly. Returns structured JSON with video
    titles and URLs.

    Args:
        query:       Search keyword. Recommended format:
                     "<exercise name> proper form tutorial",
                     e.g. "barbell squat proper form tutorial".
        max_results: Number of videos to return. Range [1, 5]. Default 3.

    Returns:
        JSON string with ``query``, ``results`` (list of rank/title/url/channel),
        and ``total_found``.  On failure, returns ``error`` field instead.
    """
    return search_videos(query=query, max_results=max_results)


if __name__ == "__main__":
    logger.info("YouTube MCP Server 启动，等待客户端连接...")
    mcp.run(transport="stdio")
