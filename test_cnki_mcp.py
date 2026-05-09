"""
CNKI MCP Server 测试脚本

使用 FastMCP 的 in-memory transport 直接测试 MCP 服务器功能。
无需启动服务器，直接在进程内调用工具。

运行方式:
    python test_cnki_mcp.py
"""

import asyncio
import json
from fastmcp import Client

# 导入我们的 MCP 服务器实例
from cnki_mcp_server import mcp


async def test_list_tools():
    """测试: 列出所有可用工具"""
    print("\n" + "=" * 60)
    print("测试 1: 列出所有可用工具")
    print("=" * 60)
    
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"发现 {len(tools)} 个工具:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description[:50]}...")
    
    return True


async def test_list_resources():
    """测试: 列出所有可用资源"""
    print("\n" + "=" * 60)
    print("测试 2: 列出所有可用资源")
    print("=" * 60)
    
    async with Client(mcp) as client:
        resources = await client.list_resources()
        print(f"发现 {len(resources)} 个资源:")
        for resource in resources:
            print(f"  - {resource.uri}")
    
    return True


async def test_read_resource():
    """测试: 读取资源"""
    print("\n" + "=" * 60)
    print("测试 3: 读取服务器状态资源")
    print("=" * 60)
    
    async with Client(mcp) as client:
        # 读取服务器状态
        result = await client.read_resource("cnki://status")
        if result:
            print(f"DEBUG: Resource result type: {type(result)}")
            print(f"DEBUG: Resource item type: {type(result[0])}")
            if hasattr(result[0], 'content'):
                print(f"DEBUG: Content: {result[0].content!r}")
            elif hasattr(result[0], 'text'):
                print(f"DEBUG: Text: {result[0].text!r}")
            
            # 兼容性处理
            if hasattr(result[0], 'text') and result[0].text:
                content = result[0].text
            elif hasattr(result[0], 'content'):
                content = result[0].content
            else:
                content = str(result[0])
            
            if isinstance(content, bytes):
                content = content.decode('utf-8')
                
            try:
                status = json.loads(content)
                print(f"服务器名称: {status.get('server_name')}")
                print(f"版本: {status.get('version')}")
                print(f"特性: {', '.join(status.get('features', []))}")
            except Exception as e:
                print(f"解析 JSON 失败: {e}")
        
        # 读取搜索类型
        result2 = await client.read_resource("cnki://search-types")
        if result2:
            try:
                if hasattr(result2[0], 'text') and result2[0].text:
                    content2 = result2[0].text
                elif hasattr(result2[0], 'content'):
                    content2 = result2[0].content
                else:
                    content2 = str(result2[0])
                
                if isinstance(content2, bytes):
                    content2 = content2.decode('utf-8')

                types = json.loads(content2)
                print(f"\n支持的搜索类型: {', '.join(types.get('chinese_types', []))}")
            except Exception as e:
                print(f"解析类型 JSON 失败: {e}")
    
    return True


async def test_search_cnki():
    """测试: 搜索 CNKI 论文"""
    print("\n" + "=" * 60)
    print("测试 4: 搜索 CNKI 论文 (关键词: '人工智能')")
    print("=" * 60)
    
    async with Client(mcp) as client:
        async def progress_handler(progress, total, *args):
            print(f"  进度: {progress}/{total}")
        
        result = await client.call_tool(
            "search_cnki",
            {
                "query": "人工智能",
                "search_type": "主题",
                "pages": 1
            },
            progress_handler=progress_handler
        )
        
        # 解析结果
        if hasattr(result, 'data'):
            data = result.data
        else:
            # 尝试从 content 获取
            data = result
        
        if isinstance(data, dict):
            if data.get("isError"):
                print(f"❌ 搜索失败: {data.get('error')}")
                return False
            else:
                print(f"✅ 搜索成功!")
                print(f"  查询: {data.get('query')}")
                print(f"  搜索类型: {data.get('search_type')}")
                print(f"  总页数: {data.get('total_pages')}")
                print(f"  论文数量: {data.get('total_papers')}")
                
                papers = data.get("papers", [])
                if papers:
                    print(f"\n  前 3 篇论文:")
                    for i, paper in enumerate(papers[:3], 1):
                        print(f"    {i}. {paper.get('title', '无标题')[:40]}...")
                        print(f"       作者: {', '.join(paper.get('authors', []))[:30]}")
                        print(f"       来源: {paper.get('source', '未知')}")
                        print(f"       引用: {paper.get('cited_count', '0')}")
                
                return True
        else:
            print(f"结果: {data}")
            return True


async def test_find_best_match():
    """测试: 查找最佳匹配"""
    print("\n" + "=" * 60)
    print("测试 5: 查找最佳匹配 (标题: '深度学习在自然语言处理中的应用')")
    print("=" * 60)
    
    async with Client(mcp) as client:
        result = await client.call_tool(
            "find_best_match",
            {"query": "深度学习在自然语言处理中的应用"}
        )
        
        if hasattr(result, 'data'):
            data = result.data
        else:
            data = result
        
        if isinstance(data, dict):
            if data.get("isError"):
                print(f"❌ 匹配失败: {data.get('error')}")
                return False
            elif data.get("best_match"):
                match = data["best_match"]
                print(f"✅ 找到最佳匹配!")
                print(f"  标题: {match.get('title', '无标题')}")
                print(f"  URL: {match.get('url', '无URL')[:60]}...")
                print(f"  总结果数: {data.get('total_results', 0)}")
                return True
            else:
                print(f"⚠️ 未找到匹配结果")
                return True
        else:
            print(f"结果: {data}")
            return True


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("CNKI MCP Server 测试脚本")
    print("=" * 60)
    
    tests = [
        ("列出工具", test_list_tools),
        ("列出资源", test_list_resources),
        ("读取资源", test_read_resource),
        ("搜索论文", test_search_cnki),
        ("最佳匹配", test_find_best_match),
    ]
    
    results = {}
    
    for name, test_func in tests:
        try:
            results[name] = await test_func()
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            results[name] = False
    
    # 打印总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
    
    passed_count = sum(1 for v in results.values() if v)
    print(f"\n总计: {passed_count}/{len(results)} 测试通过")


if __name__ == "__main__":
    asyncio.run(main())
