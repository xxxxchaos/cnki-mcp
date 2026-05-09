from fastapi import FastAPI, Query, HTTPException
from typing import List, Optional
import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import os

app = FastAPI(title="CNKI 论文标题检索服务", description="使用原始 find_closest_title 匹配算法，返回最相似论文标题")

# =================== 配置参数 ===================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/535.11 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
]

# CNKI 搜索类型映射（中文名 -> 代码）
SEARCH_TYPES = {
    "主题": "SU",       # Subject - 主题（默认）
    "篇关摘": "TKA",    # Title/Keywords/Abstract - 篇名关键词摘要
    "关键词": "KY",     # Keywords - 关键词
    "篇名": "TI",       # Title - 篇名/标题
    "全文": "FT",       # Fulltext - 全文
    "作者": "AU",       # Author - 作者
    "第一作者": "FI",   # First Author - 第一作者
    "通讯作者": "RP",   # Corresponding Author - 通讯作者
    "作者单位": "AF",   # Affiliation - 作者单位/机构
    "基金": "FU",       # Fund - 基金项目
    "摘要": "AB",       # Abstract - 摘要
    "参考文献": "RF",   # References - 参考文献
    "分类号": "CLC",    # Classification Code - 分类号
    "文献来源": "LY",   # Source - 文献来源
    "DOI": "DOI",       # DOI
}

# CNKI 下拉菜单的 value 属性（中文名 -> value 属性值）
SEARCH_TYPE_VALUES = {
    "主题": "SU$%=|",
    "篇关摘": "TKA$%=|",
    "关键词": "KY$=|",
    "篇名": "TI$%=|",
    "全文": "FT$%=|",
    "作者": "AU$=|",
    "第一作者": "FI$=|",
    "通讯作者": "RP$%=|",
    "作者单位": "AF$%",
    "基金": "FU$%|",
    "摘要": "AB$%=|",
    "参考文献": "RF$%=|",
    "分类号": "CLC$=|??",
    "文献来源": "LY$%=|",
    "DOI": "DOI$=|?",
}

# 搜索类型简写映射（方便使用英文）
SEARCH_TYPE_ALIASES = {
    "subject": "主题",
    "theme": "主题",
    "keyword": "关键词",
    "keywords": "关键词",
    "title": "篇名",
    "author": "作者",
    "first_author": "第一作者",
    "corresponding_author": "通讯作者",
    "affiliation": "作者单位",
    "institution": "作者单位",
    "fund": "基金",
    "abstract": "摘要",
    "fulltext": "全文",
    "reference": "参考文献",
    "source": "文献来源",
    "doi": "DOI",
}

# =================== 工具函数 ===================

def init_browser(headless: bool = False):
    """初始化浏览器，带防检测配置"""
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")
    if headless:
        options.add_argument("--headless=new")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # 使用 webdriver-manager 自动管理 ChromeDriver
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
          """
    })

    return driver

def find_closest_title(title: str, result_titles: List[str]) -> int:
    """根据字符匹配度选择最接近的搜索结果（原版逻辑）"""
    max_similar = 0
    best_index = 0
    for i, t in enumerate(result_titles):
        common_chars = sum(c in t for c in title)
        if common_chars > max_similar:
            max_similar = common_chars
            best_index = i
    return best_index


def resolve_search_type(search_type: str) -> str:
    """
    解析搜索类型，支持中文名称或英文别名
    
    Args:
        search_type: 搜索类型（如 "主题", "关键词", "author", "title" 等）
    
    Returns:
        str: 中文搜索类型名称
    """
    if not search_type:
        return "主题"  # 默认使用主题搜索
    
    # 转换为小写以便匹配英文别名
    search_type_lower = search_type.lower().strip()
    
    # 检查是否是英文别名
    if search_type_lower in SEARCH_TYPE_ALIASES:
        return SEARCH_TYPE_ALIASES[search_type_lower]
    
    # 检查是否是中文名称
    if search_type in SEARCH_TYPES:
        return search_type
    
    # 默认返回主题
    print(f"⚠️ 未知搜索类型 '{search_type}'，使用默认类型 '主题'")
    return "主题"


def select_search_type(driver, search_type: str):
    """
    在页面上选择搜索类型
    
    Args:
        driver: Selenium WebDriver
        search_type: 中文搜索类型名称
    """
    try:
        # 获取对应的 value 属性值
        value = SEARCH_TYPE_VALUES.get(search_type)
        if not value:
            print(f"⚠️ 未知搜索类型: {search_type}")
            return False
        
        # 点击下拉菜单触发器 #DBFieldBox
        try:
            dropdown = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "DBFieldBox"))
            )
            dropdown.click()
            time.sleep(0.8)
        except Exception as e:
            print(f"⚠️ 未找到搜索类型下拉菜单 (#DBFieldBox): {e}")
            return False
        
        # 在 #DBFieldList 中选择对应的选项（通过 value 属性）
        try:
            option = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'#DBFieldList a[value="{value}"]'))
            )
            option.click()
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ 未找到搜索类型选项: {search_type} (value={value}): {e}")
            return False
        
        print(f"✅ 已选择搜索类型: {search_type}")
        return True
    except Exception as e:
        print(f"⚠️ 选择搜索类型失败: {e}")
        return False

@app.get("/search")
def search_paper(query: str = Query(..., min_length=1)):
    """
    检索知网中与输入标题最相似的论文标题。
    
    - 使用原始 find_closest_title 算法（按字符出现次数匹配）
    - 不过滤“模拟器”等关键词（可后续添加）
    - 返回最佳匹配标题
    """
    if not query or len(query.strip()) == 0:
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    try:
        driver = init_browser()
        driver.get("https://www.cnki.net/")
        time.sleep(random.uniform(1, 2))

        # 搜索框输入
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "txt_SearchText"))
        )
        search_box.clear()
        for char in query:
            search_box.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
        driver.find_element(By.CLASS_NAME, "search-btn").click()

        time.sleep(random.uniform(2, 3))

        # 获取搜索结果
        try:
            results = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.XPATH, '//div[@id="gridTable"]//a[@class="fz14"]'))
            )
            result_titles = [r.text.strip() for r in results]
        except Exception as e:
            print(f"未找到结果: {e}")
            result_titles = []

        # 使用原始函数找最佳匹配
        if not result_titles:
            best_title = ""
        else:
            idx = find_closest_title(query, result_titles)
            best_title = result_titles[idx]

        driver.quit()

        return {
            "query": query,
            "best_match": best_title,
            "total_results": len(result_titles),
            "message": "成功检索并匹配" if best_title else "未找到结果"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索失败：{str(e)}")

@app.get("/")
def root():
    return {"message": "CNKI 论文标题检索服务已启动，请访问 /search?query=xxx 查询"}


# =================== 直接测试函数 ===================

def parse_paper_info(row_element):
    """从搜索结果行中提取论文完整信息"""
    paper = {}
    
    try:
        # 标题
        title_elem = row_element.find_element(By.XPATH, './/a[@class="fz14"]')
        paper["title"] = title_elem.text.strip()
        paper["url"] = title_elem.get_attribute("href")
    except:
        paper["title"] = ""
        paper["url"] = ""
    
    try:
        # 作者
        authors = row_element.find_elements(By.XPATH, './/td[@class="author"]//a')
        paper["authors"] = [a.text.strip() for a in authors if a.text.strip()]
    except:
        paper["authors"] = []
    
    try:
        # 来源（期刊/会议/学位）
        source_elem = row_element.find_element(By.XPATH, './/td[@class="source"]//a')
        paper["source"] = source_elem.text.strip()
    except:
        paper["source"] = ""
    
    try:
        # 发表日期
        date_elem = row_element.find_element(By.XPATH, './/td[@class="date"]')
        paper["date"] = date_elem.text.strip()
    except:
        paper["date"] = ""
    
    try:
        # 被引次数
        cite_elem = row_element.find_element(By.XPATH, './/td[@class="quote"]//a')
        paper["cited_count"] = cite_elem.text.strip()
    except:
        paper["cited_count"] = "0"
    
    try:
        # 下载次数
        download_elem = row_element.find_element(By.XPATH, './/td[@class="download"]//a')
        paper["download_count"] = download_elem.text.strip()
    except:
        paper["download_count"] = "0"
    
    return paper


def fetch_paper_detail(url: str, headless: bool = True, output_file: str = None):
    """
    从 CNKI 论文详情页获取完整信息
    
    Args:
        url: 论文详情页 URL
        headless: 是否使用无头模式
        output_file: 输出 JSON 文件路径（可选）
    
    Returns:
        dict: 包含论文完整信息的字典
    """
    import json
    
    print(f"📖 正在获取论文详情...")
    print(f"   URL: {url[:80]}...")
    
    paper = {
        "url": url,
        "title": "",
        "title_en": "",
        "authors": [],
        "institutions": [],
        "abstract": "",
        "abstract_en": "",
        "keywords": [],
        "keywords_en": [],
        "source": "",
        "year": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "doi": "",
        "cited_count": "",
        "download_count": "",
        "fund": "",
        "classification": "",
    }
    
    try:
        driver = init_browser(headless=headless)
        driver.get(url)
        time.sleep(random.uniform(2, 3))
        print("✅ 已打开论文页面")
        
        # 标题 - 使用 .wx-tit > h1
        try:
            title_elem = driver.find_element(By.XPATH, '//div[@class="wx-tit"]/h1')
            paper["title"] = title_elem.text.strip()
        except:
            try:
                title_elem = driver.find_element(By.XPATH, '//h1')
                paper["title"] = title_elem.text.strip()
            except:
                pass
        
        # 英文标题
        try:
            title_en_elem = driver.find_element(By.XPATH, '//div[@class="wx-tit"]/h2')
            paper["title_en"] = title_en_elem.text.strip()
        except:
            pass
        
        # 作者 - 区分作者和机构
        try:
            author_elems = driver.find_elements(By.XPATH, '//h3[@class="author"]/span/a')
            paper["authors"] = [a.text.strip() for a in author_elems if a.text.strip()]
        except:
            pass
        
        # 机构
        try:
            org_elems = driver.find_elements(By.XPATH, '//h3[@class="orgn"]/span/a')
            paper["institutions"] = [o.text.strip() for o in org_elems if o.text.strip()]
        except:
            pass
        
        # 摘要
        try:
            abstract_elem = driver.find_element(By.XPATH, '//span[@id="ChDivSummary"]')
            paper["abstract"] = abstract_elem.text.strip()
        except:
            pass
        
        # 英文摘要
        try:
            abstract_en_elem = driver.find_element(By.XPATH, '//span[@id="EnChDivSummary"]')
            paper["abstract_en"] = abstract_en_elem.text.strip()
        except:
            pass
        
        # 关键词
        try:
            keyword_elems = driver.find_elements(By.XPATH, '//p[@class="keywords"]//a')
            paper["keywords"] = [k.text.strip().rstrip(';；') for k in keyword_elems if k.text.strip()]
        except:
            pass
        
        # 英文关键词
        try:
            keyword_en_elems = driver.find_elements(By.XPATH, '//p[@class="keywords" and @id="catalog_KEYWORD_EN"]//a')
            paper["keywords_en"] = [k.text.strip().rstrip(';；') for k in keyword_en_elems if k.text.strip()]
        except:
            pass
        
        # 来源（期刊名）- 使用 .top-tip 中指向 navi.cnki.net 的链接
        try:
            source_elem = driver.find_element(By.XPATH, '//div[@class="top-tip"]//a[contains(@href, "navi.cnki.net")]')
            paper["source"] = source_elem.text.strip().rstrip(' .')
        except:
            try:
                source_elem = driver.find_element(By.XPATH, '//a[@class="KnsjiLink"]')
                paper["source"] = source_elem.text.strip().rstrip(' .')
            except:
                pass
        
        # 年/卷/期/页
        try:
            info_elem = driver.find_element(By.XPATH, '//div[@class="top-tip"]//span')
            info_text = info_elem.text.strip()
            # 解析 "2025,36(01):1-15" 格式
            if ',' in info_text:
                parts = info_text.split(',')
                paper["year"] = parts[0].strip()
                if len(parts) > 1:
                    rest = parts[1]
                    if '(' in rest and ')' in rest:
                        paper["volume"] = rest.split('(')[0].strip()
                        paper["issue"] = rest.split('(')[1].split(')')[0].strip()
                    if ':' in rest:
                        paper["pages"] = rest.split(':')[-1].strip()
        except:
            pass
        
        # DOI - 在 li.top-space 中查找
        try:
            doi_elem = driver.find_element(By.XPATH, '//li[contains(@class, "top-space") and contains(., "DOI")]/p')
            paper["doi"] = doi_elem.text.strip()
        except:
            try:
                # 备用方法：查找包含 DOI 格式的文本
                doi_elem = driver.find_element(By.XPATH, '//*[contains(text(), "10.") and contains(text(), "/")]')
                paper["doi"] = doi_elem.text.strip()
            except:
                pass
        
        # 被引次数
        try:
            cite_elem = driver.find_element(By.XPATH, '//span[@id="refs"]//a | //div[@class="total-inform"]//span[contains(text(),"被引")]/../em')
            paper["cited_count"] = cite_elem.text.strip()
        except:
            pass
        
        # 下载次数
        try:
            download_elem = driver.find_element(By.XPATH, '//span[@id="DownLoadParts"]//a | //div[@class="total-inform"]//span[contains(text(),"下载")]/../em')
            paper["download_count"] = download_elem.text.strip()
        except:
            pass
        
        # 基金项目
        try:
            fund_elem = driver.find_element(By.XPATH, '//li[contains(text(),"基金")]/p | //p[@class="funds"]/span')
            paper["fund"] = fund_elem.text.strip()
        except:
            pass
        
        # 分类号
        try:
            class_elem = driver.find_element(By.XPATH, '//li[contains(text(),"分类号")]/p')
            paper["classification"] = class_elem.text.strip()
        except:
            pass
        
        driver.quit()
        
        # 显示结果
        print(f"\n📄 论文信息:")
        print(f"   📌 标题: {paper['title']}")
        if paper['authors']:
            print(f"   👤 作者: {', '.join(paper['authors'])}")
        if paper['institutions']:
            print(f"   🏛️ 机构: {', '.join(paper['institutions'][:3])}")
        if paper['source']:
            print(f"   📖 来源: {paper['source']}")
        if paper['year']:
            print(f"   📅 年份: {paper['year']}")
        if paper['keywords']:
            print(f"   🏷️ 关键词: {', '.join(paper['keywords'])}")
        if paper['abstract']:
            abstract_preview = paper['abstract'][:100] + "..." if len(paper['abstract']) > 100 else paper['abstract']
            print(f"   📝 摘要: {abstract_preview}")
        if paper['doi']:
            print(f"   🔗 DOI: {paper['doi']}")
        
        # 保存到 JSON 文件
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(paper, f, ensure_ascii=False, indent=2)
            print(f"\n✅ 已保存到 {output_file}")
        
        return paper

    except Exception as e:
        print(f"❌ 获取失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def test_search(query: str, headless: bool = False):
    """直接测试搜索功能（不通过 FastAPI）"""
    print(f"🔍 正在搜索: {query}")
    
    try:
        driver = init_browser(headless=headless)
        driver.get("https://www.cnki.net/")
        print("✅ 已打开 CNKI 网站")
        time.sleep(random.uniform(1, 2))

        # 搜索框输入
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "txt_SearchText"))
        )
        search_box.clear()
        for char in query:
            search_box.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
        print(f"✅ 已输入关键词: {query}")
        
        driver.find_element(By.CLASS_NAME, "search-btn").click()
        print("✅ 已点击搜索按钮")

        time.sleep(random.uniform(3, 4))

        # 获取搜索结果的完整信息
        papers = []
        try:
            # 获取所有结果行
            rows = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.XPATH, '//table[@class="result-table-list"]//tbody//tr'))
            )
            print(f"✅ 找到 {len(rows)} 条结果")
            
            for row in rows:
                paper = parse_paper_info(row)
                if paper["title"]:
                    papers.append(paper)
                    
        except Exception as e:
            print(f"⚠️ 未找到结果: {e}")

        # 显示结果
        if papers:
            print("\n📚 搜索结果:")
            for i, paper in enumerate(papers[:10], 1):
                print(f"\n  [{i}] {paper['title']}")
                if paper['authors']:
                    print(f"      👤 作者: {', '.join(paper['authors'])}")
                if paper['source']:
                    print(f"      📖 来源: {paper['source']}")
                if paper['date']:
                    print(f"      📅 日期: {paper['date']}")
                if paper['cited_count'] != "0":
                    print(f"      📊 被引: {paper['cited_count']}")
                if paper['download_count'] != "0":
                    print(f"      ⬇️ 下载: {paper['download_count']}")
            
            # 找最佳匹配
            result_titles = [p["title"] for p in papers]
            idx = find_closest_title(query, result_titles)
            print(f"\n🎯 最佳匹配: {papers[idx]['title']}")
        else:
            print("❌ 未找到任何结果")

        driver.quit()
        
        return {
            "query": query,
            "results": papers,
            "total": len(papers)
        }

    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def search_multi_pages(query: str, pages: int = 5, search_type: str = "主题", 
                       headless: bool = True, output_file: str = None):
    """
    搜索多页结果并保存为 JSONL 文件
    
    Args:
        query: 搜索关键词
        pages: 搜索页数，默认5页
        search_type: 搜索类型，支持中文或英文
            中文: 主题、关键词、作者、篇名、作者单位、全文、DOI 等
            英文: subject, keyword, author, title, affiliation, fulltext, doi 等
        headless: 是否使用无头模式
        output_file: 输出文件路径，默认为 cnki_search_{query}.jsonl
    
    Returns:
        dict: 包含搜索结果的字典
    """
    import json
    
    # 解析搜索类型
    resolved_type = resolve_search_type(search_type)
    
    if output_file is None:
        output_file = f"cnki_search_{query}.jsonl"
    
    print(f"🔍 正在搜索: {query}")
    print(f"   📋 搜索类型: {resolved_type}")
    print(f"   📄 页数: {pages}")
    
    all_papers = []
    
    try:
        driver = init_browser(headless=headless)
        driver.get("https://www.cnki.net/")
        print("✅ 已打开 CNKI 网站")
        time.sleep(random.uniform(1, 2))
        
        # 选择搜索类型（如果不是默认的主题搜索）
        if resolved_type != "主题":
            select_search_type(driver, resolved_type)

        # 搜索框输入
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "txt_SearchText"))
        )
        search_box.clear()
        for char in query:
            search_box.send_keys(char)
            time.sleep(random.uniform(0.05, 0.15))
        print(f"✅ 已输入关键词: {query}")
        
        driver.find_element(By.CLASS_NAME, "search-btn").click()
        print("✅ 已点击搜索按钮")

        time.sleep(random.uniform(3, 4))
        
        # 遍历每一页
        for page_num in range(1, pages + 1):
            print(f"\n📄 正在获取第 {page_num} 页...")
            
            # 获取当前页的结果
            try:
                rows = WebDriverWait(driver, 15).until(
                    EC.presence_of_all_elements_located((By.XPATH, '//table[@class="result-table-list"]//tbody//tr'))
                )
                
                page_papers = []
                for row in rows:
                    paper = parse_paper_info(row)
                    if paper["title"]:
                        paper["page"] = page_num  # 添加页码信息
                        page_papers.append(paper)
                
                all_papers.extend(page_papers)
                print(f"   ✅ 第 {page_num} 页获取 {len(page_papers)} 条结果")
                
            except Exception as e:
                print(f"   ⚠️ 第 {page_num} 页获取失败: {e}")
            
            # 如果不是最后一页，点击下一页
            if page_num < pages:
                try:
                    # 点击下一页按钮
                    next_btn = driver.find_element(By.ID, "PageNext")
                    if next_btn.is_enabled():
                        next_btn.click()
                        time.sleep(random.uniform(2, 3))
                    else:
                        print(f"   ⚠️ 已到达最后一页")
                        break
                except Exception as e:
                    print(f"   ⚠️ 无法翻页: {e}")
                    break
        
        driver.quit()
        
        # 保存到 JSONL 文件
        with open(output_file, 'w', encoding='utf-8') as f:
            for paper in all_papers:
                f.write(json.dumps(paper, ensure_ascii=False) + '\n')
        
        print(f"\n✅ 共获取 {len(all_papers)} 条结果，已保存到 {output_file}")
        
        return {
            "query": query,
            "total_pages": pages,
            "total_papers": len(all_papers),
            "output_file": output_file
        }

    except Exception as e:
        print(f"❌ 搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


if __name__ == "__main__":
    import sys
    
    # 帮助信息
    if len(sys.argv) > 1 and sys.argv[1] in ["-h", "--help"]:
        print("""
CNKI 论文搜索工具

使用方法:
    python cnki-search-api.py <关键词> [页数] [搜索类型]

参数:
    关键词      搜索的关键词（必填）
    页数        搜索页数，默认5页
    搜索类型    搜索类型，支持中文或英文，默认"主题"

支持的搜索类型:
    中文: 主题、关键词、作者、篇名、作者单位、全文、DOI、基金、摘要、第一作者、通讯作者
    英文: subject, keyword, author, title, affiliation, fulltext, doi, fund, abstract

示例:
    python cnki-search-api.py llm                    # 按主题搜索 "llm"
    python cnki-search-api.py llm 3                  # 按主题搜索 "llm"，获取3页
    python cnki-search-api.py llm 5 关键词           # 按关键词搜索 "llm"
    python cnki-search-api.py 张三 5 author          # 按作者搜索 "张三"
    python cnki-search-api.py 北京大学 3 作者单位    # 按机构搜索
        """)
        sys.exit(0)
    
    if len(sys.argv) > 1:
        keyword = sys.argv[1]
    else:
        keyword = "llm"
    
    # 获取页数参数，默认5页
    pages = 5
    if len(sys.argv) > 2:
        try:
            pages = int(sys.argv[2])
        except:
            pass
    
    # 获取搜索类型参数，默认"主题"
    search_type = "主题"
    if len(sys.argv) > 3:
        search_type = sys.argv[3]
    
    # 使用多页搜索
    search_multi_pages(keyword, pages=pages, search_type=search_type, headless=True)