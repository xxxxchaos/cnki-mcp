# CNKI MCP Server

基于 [FastMCP](https://github.com/jlowin/fastmcp) 框架的中国知网 (CNKI) 论文检索 MCP 服务器，使 Cursor、Claude Desktop 等 AI Agent 可以直接搜索和获取 CNKI 论文信息。

## 功能特性

- **search_cnki**: 搜索 CNKI 论文，支持多种搜索类型（主题、关键词、作者等）
- **get_paper_detail**: 获取论文详情（标题、摘要、作者、机构、DOI 等）
- **find_best_match**: 快速匹配最相似的论文标题

## 快速使用 (无需手动安装)

本项目的代码已发布到 GitHub，你可以通过 `uvx` 直接运行，无需先把代码克隆到本地安装。

**前置要求**: 
- 需安装 [uv](https://github.com/astral-sh/uv)
- 系统需安装 Chrome 浏览器（Selenium 依赖）

### 1. Cursor 配置

在 Cursor 设置中添加 MCP 服务器。编辑 `~/.cursor/mcp.json` (或通过 UI 添加):

```json
{
  "mcpServers": {
    "cnki": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/h-lu/cnki-mcp",
        "cnki-mcp"
      ]
    }
  }
}
```

### 2. Claude Desktop 配置

编辑配置文件 (通常位于 `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "cnki": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/h-lu/cnki-mcp",
        "cnki-mcp"
      ]
    }
  }
}
```

配置保存后，重启 Cursor 或 Claude Desktop 即可看到 `cnki` 服务器已连接。

## 使用示例

在 Cursor / Claude 中直接提问:

> 帮我搜索关于"大语言模型"的 CNKI 论文

> 获取这篇论文的详细信息: https://kns.cnki.net/kcms2/article/abstract?v=...

> 查找这篇论文: "Transformer: Attention Is All You Need" 在知网上的情况

## 支持的搜索类型

| 中文 | 英文别名 |
|------|---------|
| 主题 | subject, theme |
| 关键词 | keyword, keywords |
| 篇名 | title |
| 作者 | author |
| 作者单位 | affiliation, institution |
| DOI | doi |

## 注意事项

- 建议每次搜索 1-3 页，避免频繁请求
- 搜索间隔建议 2-3 秒
- CNKI 可能有反爬限制，如遇问题请适当降低请求频率
- 首次运行时会自动下载 ChromeDriver，可能会花费一点时间

## License

MIT
