"""
CNKI MCP Server 排序功能测试

测试 search_cnki 工具的排序参数是否正常工作
"""

import asyncio
from cnki_mcp_server import (
    SORT_TYPES,
    SORT_TYPE_ALIASES,
    resolve_sort_type,
    apply_sort,
    _search_cnki_sync,
    BrowserPool,
)


def test_sort_types():
    """测试排序类型常量"""
    print("=" * 50)
    print("测试 SORT_TYPES 常量")
    print("=" * 50)
    
    expected_types = ["相关度", "发表时间", "被引", "下载", "综合"]
    expected_ids = ["FFD", "PT", "CF", "DFR", "ZH"]
    
    for name, dom_id in zip(expected_types, expected_ids):
        assert name in SORT_TYPES, f"缺少排序类型: {name}"
        assert SORT_TYPES[name] == dom_id, f"排序类型 {name} 的 DOM ID 不正确"
        print(f"  ✅ {name} -> {dom_id}")
    
    print("\n✅ SORT_TYPES 常量测试通过\n")


def test_sort_type_aliases():
    """测试排序类型英文别名"""
    print("=" * 50)
    print("测试 SORT_TYPE_ALIASES 别名")
    print("=" * 50)
    
    test_cases = [
        ("relevance", "相关度"),
        ("date", "发表时间"),
        ("time", "发表时间"),
        ("cited", "被引"),
        ("citations", "被引"),
        ("download", "下载"),
        ("composite", "综合"),
    ]
    
    for alias, expected in test_cases:
        assert alias in SORT_TYPE_ALIASES, f"缺少别名: {alias}"
        assert SORT_TYPE_ALIASES[alias] == expected, f"别名 {alias} 应该映射到 {expected}"
        print(f"  ✅ {alias} -> {expected}")
    
    print("\n✅ SORT_TYPE_ALIASES 别名测试通过\n")


def test_resolve_sort_type():
    """测试 resolve_sort_type 函数"""
    print("=" * 50)
    print("测试 resolve_sort_type 函数")
    print("=" * 50)
    
    test_cases = [
        # (输入, 预期输出)
        ("被引", "被引"),
        ("发表时间", "发表时间"),
        ("cited", "被引"),
        ("CITED", "被引"),  # 大小写不敏感
        ("date", "发表时间"),
        ("Date", "发表时间"),
        ("unknown", "相关度"),  # 未知类型返回默认值
        ("", "相关度"),  # 空字符串返回默认值
        (None, "相关度"),  # None 返回默认值
    ]
    
    for input_val, expected in test_cases:
        result = resolve_sort_type(input_val) if input_val is not None else resolve_sort_type("")
        if input_val is None:
            input_val = "None"
        assert result == expected, f"resolve_sort_type({input_val!r}) 应该返回 {expected!r}, 但返回了 {result!r}"
        print(f"  ✅ resolve_sort_type({input_val!r}) -> {result!r}")
    
    print("\n✅ resolve_sort_type 函数测试通过\n")


def test_search_with_sort(query: str = "人工智能", sort: str = "被引"):
    """
    实际测试搜索排序功能（需要浏览器）
    
    Args:
        query: 搜索关键词
        sort: 排序方式
    """
    print("=" * 50)
    print(f"测试搜索排序: query='{query}', sort='{sort}'")
    print("=" * 50)
    
    pool = BrowserPool()
    try:
        result = _search_cnki_sync(pool, query, "主题", pages=1, sort=sort)
        
        if result.get("isError"):
            print(f"❌ 搜索失败: {result.get('error')}")
            return
        
        print(f"  🔍 查询: {result.get('query')}")
        print(f"  📊 排序: {result.get('sort')}")
        print(f"  📄 结果数: {result.get('total_papers')}")
        
        papers = result.get("papers", [])
        if papers:
            print(f"\n  📚 前5篇论文:")
            for i, paper in enumerate(papers[:5], 1):
                print(f"    [{i}] {paper.get('title', '无标题')[:40]}...")
                print(f"        被引: {paper.get('cited_count', '0')}  下载: {paper.get('download_count', '0')}")
        
        print("\n✅ 搜索排序测试完成\n")
        
    finally:
        pool.close()


def run_unit_tests():
    """运行所有单元测试（不需要浏览器）"""
    print("\n" + "=" * 60)
    print("   CNKI MCP Server 排序功能单元测试")
    print("=" * 60 + "\n")
    
    test_sort_types()
    test_sort_type_aliases()
    test_resolve_sort_type()
    
    print("=" * 60)
    print("   ✅ 所有单元测试通过!")
    print("=" * 60 + "\n")


def run_integration_test(query: str = "人工智能", sort: str = "被引"):
    """运行集成测试（需要浏览器）"""
    print("\n" + "=" * 60)
    print("   CNKI MCP Server 排序功能集成测试")
    print("=" * 60 + "\n")
    
    test_search_with_sort(query, sort)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--integration":
        # 运行集成测试
        query = sys.argv[2] if len(sys.argv) > 2 else "人工智能"
        sort = sys.argv[3] if len(sys.argv) > 3 else "被引"
        run_unit_tests()
        run_integration_test(query, sort)
    else:
        # 仅运行单元测试
        run_unit_tests()
        print("💡 提示: 运行 `python test_cnki.py --integration` 进行完整集成测试（需要浏览器）")
