"""
CNKI MCP Server - 中国知网论文检索 MCP 服务

基于 FastMCP 框架，为 Cursor/Claude 等 AI Agent 提供 CNKI 论文搜索能力。

优化特性:
- 浏览器复用：首次调用时启动 Chrome，后续复用同一实例
- 超时刷新：浏览器 10 分钟无活动后自动关闭，下次调用重新启动
- 线程安全：使用锁保证并发安全

工具列表:
- search_cnki: 搜索 CNKI 论文（支持多页、多种搜索类型）
- get_paper_detail: 获取论文详情页完整信息
- find_best_match: 快速查找与输入标题最匹配的论文

使用方法:
    # 安装依赖
    pip install fastmcp selenium webdriver-manager asyncer
    
    # 运行服务器 (stdio 模式，供 Cursor/Claude Desktop 使用)
    python cnki_mcp_server.py
    
    # 或运行为 HTTP 服务器
    fastmcp run cnki_mcp_server.py --transport http --port 8000
"""

from fastmcp import FastMCP
from fastmcp import Context
from fastmcp.dependencies import Depends, CurrentContext
from typing import List, Optional, Annotated
from pydantic import Field
import time
import random
import json
import functools
import threading
import atexit
import asyncio
import asyncer
from typing import Callable, ParamSpec, TypeVar, Awaitable
from dataclasses import dataclass
from contextlib import asynccontextmanager

# Selenium 相关导入
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    StaleElementReferenceException
)
from webdriver_manager.chrome import ChromeDriverManager

# =================== 自定义异常 ===================

class CNKIError(Exception):
    """CNKI 服务基础异常"""
    pass


class BrowserError(CNKIError):
    """浏览器相关错误"""
    pass


class SearchError(CNKIError):
    """搜索相关错误"""
    pass


class ValidationError(CNKIError):
    """参数验证错误"""
    pass


# =================== 浏览器池管理 ===================

class BrowserPool:
    """
    浏览器池 - 管理 Chrome 实例的复用
    
    特性:
    - 延迟初始化：首次调用时才启动浏览器
    - 实例复用：多次调用共享同一浏览器
    - 超时关闭：10 分钟无活动自动关闭，节省资源
    - 线程安全：使用锁保证并发访问安全
    """
    
    IDLE_TIMEOUT = 600  # 10 分钟无活动后关闭浏览器
    
    def __init__(self):
        self._driver: Optional[webdriver.Chrome] = None
        self._last_used: float = 0
        self._lock = threading.Lock()
        self._user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/535.11 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ]
    
    def _create_driver(self) -> webdriver.Chrome:
        """创建新的 Chrome 实例"""
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-extensions")
        options.add_argument(f"user-agent={random.choice(self._user_agents)}")
        options.add_argument("--disable-gpu")
        options.add_argument("--log-level=3")
        options.add_argument("--headless=new")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
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
    
    def _is_driver_alive(self) -> bool:
        """检查浏览器是否仍然可用"""
        if self._driver is None:
            return False
        try:
            # 尝试获取当前 URL，如果失败说明浏览器已关闭
            _ = self._driver.current_url
            return True
        except Exception:
            return False
    
    def get_driver(self) -> webdriver.Chrome:
        """
        获取浏览器实例（线程安全）
        
        - 如果没有实例或实例已失效，创建新实例
        - 如果超时未使用，先关闭旧实例再创建新实例
        - 更新最后使用时间
        """
        with self._lock:
            current_time = time.time()
            
            # 检查是否需要关闭超时的浏览器
            if self._driver is not None:
                if current_time - self._last_used > self.IDLE_TIMEOUT:
                    self._close_driver_internal()
                elif not self._is_driver_alive():
                    self._driver = None
            
            # 如果没有可用的浏览器，创建新实例
            if self._driver is None:
                self._driver = self._create_driver()
            
            self._last_used = current_time
            return self._driver
    
    def _close_driver_internal(self):
        """内部关闭方法（不加锁）"""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
    
    def close(self):
        """关闭浏览器（线程安全）"""
        with self._lock:
            self._close_driver_internal()
    
    def navigate_to_cnki(self) -> webdriver.Chrome:
        """获取浏览器并导航到 CNKI 首页"""
        driver = self.get_driver()
        current_url = driver.current_url
        
        # 如果不在 CNKI 首页，则导航过去
        if "cnki.net" not in current_url or "kns.cnki.net" in current_url:
            driver.get("https://www.cnki.net/")
            time.sleep(random.uniform(1, 2))
        
        return driver

# =================== 应用上下文和生命周期 ===================

@dataclass
class AppContext:
    """应用上下文 - 持有共享资源"""
    browser_pool: BrowserPool


@asynccontextmanager
async def lifespan(server: FastMCP):
    """
    MCP 服务器生命周期管理
    
    - 启动时创建浏览器池
    - 关闭时自动清理资源
    """
    pool = BrowserPool()
    try:
        yield AppContext(browser_pool=pool)
    finally:
        pool.close()


# =================== MCP 服务器配置 ===================
mcp = FastMCP(
    "CNKI 论文检索服务",
    lifespan=lifespan,
    instructions="""
    CNKI (中国知网) 论文检索 MCP 服务器。
    
    ## 可用工具
    
    ### search_cnki
    搜索 CNKI 论文，返回论文列表。
    - query: 搜索关键词（必填）
    - search_type: 搜索类型（可选，默认"主题"）
      - 支持: 主题、关键词、作者、篇名、作者单位、全文、DOI 等
      - 英文别名: subject, keyword, author, title, affiliation, fulltext, doi
    - pages: 搜索页数（可选，默认1页，每页约20条）
    - sort: 排序方式（可选，默认"相关度"）
      - 支持: 相关度、发表时间、被引、下载、综合
      - 英文别名: relevance, date, cited, download, composite
    
    ### get_paper_detail
    获取论文详情页的完整信息。
    - url: CNKI 论文详情页 URL（必填）
    
    ### find_best_match
    快速查找与输入标题最匹配的论文。
    - query: 论文标题（必填）
    
    ## 可用资源
    
    ### cnki://search-types
    返回支持的搜索类型列表。
    
    ### cnki://status
    返回服务器状态信息。
    
    ## 使用建议
    1. 先用 search_cnki 搜索论文列表
    2. 从结果中选择目标论文的 URL
    3. 用 get_paper_detail 获取完整详情
    4. 使用 sort="被引" 查找高被引论文
    5. 使用 sort="发表时间" 查找最新论文
    
    ## 注意事项
    - 每次搜索建议 1-3 页，避免过多请求
    - 搜索间隔建议 2-3 秒，避免触发反爬
    - 浏览器实例会在首次调用时启动，后续复用（更快）
    """
)

# =================== 配置参数 ===================
# CNKI 搜索类型映射
SEARCH_TYPES = {
    "主题": "SU", "篇关摘": "TKA", "关键词": "KY", "篇名": "TI",
    "全文": "FT", "作者": "AU", "第一作者": "FI", "通讯作者": "RP",
    "作者单位": "AF", "基金": "FU", "摘要": "AB", "参考文献": "RF",
    "分类号": "CLC", "文献来源": "LY", "DOI": "DOI",
}

SEARCH_TYPE_VALUES = {
    "主题": "SU$%=|", "篇关摘": "TKA$%=|", "关键词": "KY$=|",
    "篇名": "TI$%=|", "全文": "FT$%=|", "作者": "AU$=|",
    "第一作者": "FI$=|", "通讯作者": "RP$%=|", "作者单位": "AF$%",
    "基金": "FU$%|", "摘要": "AB$%=|", "参考文献": "RF$%=|",
    "分类号": "CLC$=|??", "文献来源": "LY$%=|", "DOI": "DOI$=|?",
}

SEARCH_TYPE_ALIASES = {
    "subject": "主题", "theme": "主题", "keyword": "关键词",
    "keywords": "关键词", "title": "篇名", "author": "作者",
    "first_author": "第一作者", "corresponding_author": "通讯作者",
    "affiliation": "作者单位", "institution": "作者单位",
    "fund": "基金", "abstract": "摘要", "fulltext": "全文",
    "reference": "参考文献", "source": "文献来源", "doi": "DOI",
}

# CNKI 排序类型映射（中文名 -> DOM ID）
SORT_TYPES = {
    "相关度": "FFD",
    "发表时间": "PT",
    "被引": "CF",
    "下载": "DFR",
    "综合": "ZH",
}

# 排序类型英文别名
SORT_TYPE_ALIASES = {
    "relevance": "相关度",
    "date": "发表时间",
    "publish_time": "发表时间",
    "time": "发表时间",
    "cited": "被引",
    "citation": "被引",
    "citations": "被引",
    "download": "下载",
    "downloads": "下载",
    "composite": "综合",
    "general": "综合",
}


# =================== 工具函数 ===================

def resolve_search_type(search_type: str) -> str:
    """解析搜索类型，支持中文或英文别名"""
    if not search_type:
        return "主题"
    search_type_lower = search_type.lower().strip()
    if search_type_lower in SEARCH_TYPE_ALIASES:
        return SEARCH_TYPE_ALIASES[search_type_lower]
    if search_type in SEARCH_TYPES:
        return search_type
    return "主题"


def resolve_sort_type(sort_type: str) -> str:
    """解析排序类型，支持中文或英文别名"""
    if not sort_type:
        return "相关度"
    sort_type_lower = sort_type.lower().strip()
    if sort_type_lower in SORT_TYPE_ALIASES:
        return SORT_TYPE_ALIASES[sort_type_lower]
    if sort_type in SORT_TYPES:
        return sort_type
    return "相关度"


def select_search_type(driver, search_type: str) -> bool:
    """在页面上选择搜索类型"""
    try:
        value = SEARCH_TYPE_VALUES.get(search_type)
        if not value:
            return False
        dropdown = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, "DBFieldBox"))
        )
        dropdown.click()
        time.sleep(0.8)
        option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, f'#DBFieldList a[value="{value}"]'))
        )
        option.click()
        time.sleep(0.5)
        return True
    except Exception:
        return False


def apply_sort(driver, sort_type: str) -> bool:
    """
    在搜索结果页面应用排序
    
    Args:
        driver: Selenium WebDriver
        sort_type: 中文排序类型名称（相关度、发表时间、被引、下载、综合）
    
    Returns:
        bool: 是否成功应用排序
    """
    try:
        sort_id = SORT_TYPES.get(sort_type)
        if not sort_id:
            return False
        
        # 点击排序按钮
        sort_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.ID, sort_id))
        )
        sort_btn.click()
        time.sleep(random.uniform(1.5, 2.5))
        
        # 等待结果表格重新加载
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located(
                (By.XPATH, '//table[@class="result-table-list"]//tbody//tr')
            )
        )
        return True
    except Exception:
        return False


def parse_paper_info(row_element) -> dict:
    """从搜索结果行中提取论文信息"""
    paper = {}
    try:
        title_elem = row_element.find_element(By.XPATH, './/a[@class="fz14"]')
        paper["title"] = title_elem.text.strip()
        paper["url"] = title_elem.get_attribute("href")
    except (NoSuchElementException, StaleElementReferenceException):
        paper["title"] = ""
        paper["url"] = ""
    try:
        authors = row_element.find_elements(By.XPATH, './/td[@class="author"]//a')
        paper["authors"] = [a.text.strip() for a in authors if a.text.strip()]
    except (NoSuchElementException, StaleElementReferenceException):
        paper["authors"] = []
    try:
        source_elem = row_element.find_element(By.XPATH, './/td[@class="source"]//a')
        paper["source"] = source_elem.text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        paper["source"] = ""
    try:
        date_elem = row_element.find_element(By.XPATH, './/td[@class="date"]')
        paper["date"] = date_elem.text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        paper["date"] = ""
    try:
        cite_elem = row_element.find_element(By.XPATH, './/td[@class="quote"]//a')
        paper["cited_count"] = cite_elem.text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        paper["cited_count"] = "0"
    try:
        download_elem = row_element.find_element(By.XPATH, './/td[@class="download"]//a')
        paper["download_count"] = download_elem.text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        paper["download_count"] = "0"
    return paper


def find_closest_title(title: str, result_titles: List[str]) -> int:
    """根据字符匹配度选择最接近的搜索结果"""
    max_similar = 0
    best_index = 0
    for i, t in enumerate(result_titles):
        common_chars = sum(c in t for c in title)
        if common_chars > max_similar:
            max_similar = common_chars
            best_index = i
    return best_index


# =================== 同步核心函数（使用浏览器池）===================

def _dismiss_cnki_popups(driver) -> bool:
    """尝试关闭 CNKI 首页的弹窗/遮罩（如有）"""
    popup_selectors = [
        (By.ID, "close"),
        (By.CLASS_NAME, "close"),
        (By.XPATH, '//div[contains(@class,"popup")]//a[contains(text(),"关闭")]'),
        (By.XPATH, '//div[contains(@class,"modal")]//button[contains(text(),"关闭")]'),
        (By.XPATH, '//div[contains(@class,"layui-layer")]//a[contains(@class,"layui-layer-close")]'),
    ]
    for by, selector in popup_selectors:
        try:
            elem = driver.find_element(by, selector)
            driver.execute_script("arguments[0].click();", elem)
            time.sleep(0.5)
            return True
        except Exception:
            continue
    return False


def _submit_search(driver, search_box) -> None:
    """稳健的搜索提交：先尝试关闭弹窗，再通过 JS 点击或回车提交"""
    _dismiss_cnki_popups(driver)
    time.sleep(0.3)

    # 方法1: JS 点击搜索按钮（可绕过 Selenium 的 click intercepted 检查）
    try:
        search_btn = driver.find_element(By.CLASS_NAME, "search-btn")
        driver.execute_script("arguments[0].click();", search_btn)
        return
    except Exception:
        pass

    # 方法2: 回车提交
    try:
        search_box.send_keys(Keys.RETURN)
    except Exception:
        pass


def _search_cnki_sync(browser_pool: BrowserPool, query: str, search_type: str = "主题", pages: int = 1, sort: str = "相关度") -> dict:
    """同步版本的 CNKI 搜索（使用浏览器池）"""
    resolved_type = resolve_search_type(search_type)
    resolved_sort = resolve_sort_type(sort)
    all_papers = []
    
    try:
        # 使用浏览器池，导航到 CNKI 首页
        driver = browser_pool.navigate_to_cnki()
        
        # 选择搜索类型
        if resolved_type != "主题":
            select_search_type(driver, resolved_type)
        
        # 输入搜索关键词
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "txt_SearchText"))
        )
        search_box.clear()
        for char in query:
            search_box.send_keys(char)
            time.sleep(random.uniform(0.03, 0.08))

        _submit_search(driver, search_box)
        time.sleep(random.uniform(2, 3))

        # 应用排序（如果不是默认的相关度）
        if resolved_sort != "相关度":
            apply_sort(driver, resolved_sort)

        # 遍历每一页
        for page_num in range(1, pages + 1):
            try:
                rows = WebDriverWait(driver, 15).until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, '//table[@class="result-table-list"]//tbody//tr')
                    )
                )
                for row in rows:
                    paper = parse_paper_info(row)
                    if paper["title"]:
                        paper["page"] = page_num
                        all_papers.append(paper)
            except (TimeoutException, NoSuchElementException):
                pass
            
            # 翻页
            if page_num < pages:
                try:
                    next_btn = driver.find_element(By.ID, "PageNext")
                    if next_btn.is_enabled():
                        next_btn.click()
                        time.sleep(random.uniform(1.5, 2.5))
                    else:
                        break
                except (NoSuchElementException, WebDriverException):
                    break
        
        return {
            "query": query,
            "search_type": resolved_type,
            "sort": resolved_sort,
            "total_pages": pages,
            "total_papers": len(all_papers),
            "papers": all_papers
        }
    
    except WebDriverException as e:
        return {"isError": True, "error": str(e), "error_type": "BrowserError", "papers": []}
    except Exception as e:
        return {"isError": True, "error": str(e), "error_type": "SearchError", "papers": []}


def _get_paper_detail_sync(browser_pool: BrowserPool, url: str) -> dict:
    """同步版本的获取论文详情（使用浏览器池）"""
    paper = {
        "url": url, "title": "", "title_en": "", "authors": [],
        "institutions": [], "abstract": "", "abstract_en": "",
        "keywords": [], "keywords_en": [], "source": "", "year": "",
        "volume": "", "issue": "", "pages": "", "doi": "",
        "cited_count": "", "download_count": "", "fund": "", "classification": "",
    }
    
    try:
        driver = browser_pool.get_driver()
        driver.get(url)
        time.sleep(random.uniform(1.5, 2.5))
        
        # 标题
        try:
            title_elem = driver.find_element(By.XPATH, '//div[@class="wx-tit"]/h1')
            paper["title"] = title_elem.text.strip()
        except NoSuchElementException:
            try:
                title_elem = driver.find_element(By.XPATH, '//h1')
                paper["title"] = title_elem.text.strip()
            except NoSuchElementException:
                pass
        
        # 英文标题
        try:
            title_en_elem = driver.find_element(By.XPATH, '//div[@class="wx-tit"]/h2')
            paper["title_en"] = title_en_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 作者
        try:
            author_elems = driver.find_elements(By.XPATH, '//h3[@class="author"]/span/a')
            paper["authors"] = [a.text.strip() for a in author_elems if a.text.strip()]
        except NoSuchElementException:
            pass
        
        # 机构
        try:
            org_elems = driver.find_elements(By.XPATH, '//h3[@class="orgn"]/span/a')
            paper["institutions"] = [o.text.strip() for o in org_elems if o.text.strip()]
        except NoSuchElementException:
            pass
        
        # 摘要
        try:
            abstract_elem = driver.find_element(By.XPATH, '//span[@id="ChDivSummary"]')
            paper["abstract"] = abstract_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 英文摘要
        try:
            abstract_en_elem = driver.find_element(By.XPATH, '//span[@id="EnChDivSummary"]')
            paper["abstract_en"] = abstract_en_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 关键词
        try:
            keyword_elems = driver.find_elements(By.XPATH, '//p[@class="keywords"]//a')
            paper["keywords"] = [k.text.strip().rstrip(';；') for k in keyword_elems if k.text.strip()]
        except NoSuchElementException:
            pass
        
        # 来源
        try:
            source_elem = driver.find_element(
                By.XPATH, '//div[@class="top-tip"]//a[contains(@href, "navi.cnki.net")]'
            )
            paper["source"] = source_elem.text.strip().rstrip(' .')
        except NoSuchElementException:
            pass
        
        # 年/卷/期/页
        try:
            info_elem = driver.find_element(By.XPATH, '//div[@class="top-tip"]//span')
            info_text = info_elem.text.strip()
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
        except NoSuchElementException:
            pass
        
        # DOI
        try:
            doi_elem = driver.find_element(
                By.XPATH, '//li[contains(@class, "top-space") and contains(., "DOI")]/p'
            )
            paper["doi"] = doi_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 被引次数
        try:
            cite_elem = driver.find_element(
                By.XPATH, '//span[@id="refs"]//a | //div[@class="total-inform"]//span[contains(text(),"被引")]/../em'
            )
            paper["cited_count"] = cite_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 下载次数
        try:
            download_elem = driver.find_element(
                By.XPATH, '//span[@id="DownLoadParts"]//a | //div[@class="total-inform"]//span[contains(text(),"下载")]/../em'
            )
            paper["download_count"] = download_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 基金
        try:
            fund_elem = driver.find_element(
                By.XPATH, '//li[contains(text(),"基金")]/p | //p[@class="funds"]/span'
            )
            paper["fund"] = fund_elem.text.strip()
        except NoSuchElementException:
            pass
        
        # 分类号
        try:
            class_elem = driver.find_element(By.XPATH, '//li[contains(text(),"分类号")]/p')
            paper["classification"] = class_elem.text.strip()
        except NoSuchElementException:
            pass
        
        return paper
    
    except WebDriverException as e:
        return {"isError": True, "error": str(e), "error_type": "BrowserError", "url": url}
    except Exception as e:
        return {"isError": True, "error": str(e), "error_type": "DetailError", "url": url}


def _find_best_match_sync(browser_pool: BrowserPool, query: str) -> dict:
    """同步版本的快速匹配（使用浏览器池）"""
    try:
        driver = browser_pool.navigate_to_cnki()
        
        search_box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "txt_SearchText"))
        )
        search_box.clear()
        for char in query:
            search_box.send_keys(char)
            time.sleep(random.uniform(0.03, 0.08))

        _submit_search(driver, search_box)
        time.sleep(random.uniform(2, 3))
        
        result_titles = []
        result_urls = []
        try:
            results = WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, '//div[@id="gridTable"]//a[@class="fz14"]')
                )
            )
            for r in results:
                result_titles.append(r.text.strip())
                result_urls.append(r.get_attribute("href"))
        except (TimeoutException, NoSuchElementException):
            pass
        
        if not result_titles:
            return {"query": query, "best_match": None, "message": "未找到结果"}
        
        idx = find_closest_title(query, result_titles)
        return {
            "query": query,
            "best_match": {
                "title": result_titles[idx],
                "url": result_urls[idx]
            },
            "total_results": len(result_titles)
        }
    
    except WebDriverException as e:
        return {"isError": True, "error": str(e), "error_type": "BrowserError"}
    except Exception as e:
        return {"isError": True, "error": str(e), "error_type": "MatchError"}


# =================== 依赖注入 ===================

def get_browser_pool(ctx: Context = CurrentContext()) -> BrowserPool:
    """依赖注入：获取浏览器池实例"""
    return ctx.request_context.lifespan_context.browser_pool


# =================== MCP 工具定义 ===================

@mcp.tool()
async def search_cnki(
    query: Annotated[str, Field(description="搜索关键词（必填）", min_length=1)],
    ctx: Context,
    search_type: Annotated[str, Field(
        description="搜索类型: 主题、关键词、作者、篇名、作者单位、全文、DOI、基金、摘要"
    )] = "主题",
    pages: Annotated[int, Field(
        description="搜索页数（每页约20条结果）",
        ge=1,
        le=10
    )] = 1,
    sort: Annotated[str, Field(
        description="排序方式: 相关度、发表时间、被引、下载、综合 (英文: relevance, date, cited, download, composite)"
    )] = "相关度",
    browser_pool: BrowserPool = Depends(get_browser_pool)
) -> dict:
    """
    搜索 CNKI 论文，返回论文列表。
    
    Args:
        query: 搜索关键词
        ctx: MCP 上下文（自动注入）
        search_type: 搜索类型，支持：
            - 中文：主题、关键词、作者、篇名、作者单位、全文、DOI、基金、摘要
            - 英文：subject, keyword, author, title, affiliation, fulltext, doi
        pages: 搜索页数（1-10），每页约20条结果
        sort: 排序方式，支持：
            - 中文：相关度、发表时间、被引、下载、综合
            - 英文：relevance, date, cited, download, composite
    
    Returns:
        包含论文列表的字典，每篇论文包含：title, url, authors, source, date, cited_count
    """
    await ctx.info(f"开始搜索 CNKI: query='{query}', type='{search_type}', sort='{sort}', pages={pages}")
    await ctx.report_progress(progress=0, total=100)
    
    # 使用 asyncer 在后台线程中执行同步操作
    result = await asyncer.asyncify(_search_cnki_sync)(
        browser_pool, query, search_type, pages, sort
    )
    
    await ctx.report_progress(progress=100, total=100)
    
    if result.get("isError"):
        await ctx.error(f"搜索失败: {result.get('error')}")
    else:
        await ctx.info(f"搜索完成，找到 {result.get('total_papers', 0)} 篇论文")
    
    return result


@mcp.tool()
async def get_paper_detail(
    url: Annotated[str, Field(description="CNKI 论文详情页 URL（通常从 search_cnki 结果中获取）")],
    ctx: Context,
    browser_pool: BrowserPool = Depends(get_browser_pool)
) -> dict:
    """
    获取 CNKI 论文详情页的完整信息。
    
    Args:
        url: CNKI 论文详情页 URL（通常从 search_cnki 结果中获取）
        ctx: MCP 上下文（自动注入）
    
    Returns:
        包含论文完整信息的字典：title, authors, institutions, abstract, keywords, 
        source, year, volume, issue, pages, doi, cited_count, download_count, fund
    """
    # 参数验证
    if not url or not url.strip():
        return {"isError": True, "error": "URL 不能为空", "error_type": "ValidationError"}
    
    if "cnki" not in url.lower():
        return {"isError": True, "error": "URL 必须是 CNKI 链接", "error_type": "ValidationError"}
    
    await ctx.info(f"获取论文详情: {url[:80]}...")
    await ctx.report_progress(progress=0, total=100)
    
    # 使用 asyncer 在后台线程中执行同步操作
    result = await asyncer.asyncify(_get_paper_detail_sync)(browser_pool, url)
    
    await ctx.report_progress(progress=100, total=100)
    
    if result.get("isError"):
        await ctx.error(f"获取详情失败: {result.get('error')}")
    else:
        await ctx.info(f"获取详情成功: {result.get('title', '未知标题')[:50]}")
    
    return result


@mcp.tool()
async def find_best_match(
    query: Annotated[str, Field(description="论文标题或关键词", min_length=1)],
    ctx: Context,
    browser_pool: BrowserPool = Depends(get_browser_pool)
) -> dict:
    """
    快速查找与输入标题最匹配的 CNKI 论文。
    
    使用字符匹配算法，适合用于验证论文标题或快速定位特定论文。
    
    Args:
        query: 论文标题或关键词
        ctx: MCP 上下文（自动注入）
    
    Returns:
        最匹配论文的标题和 URL
    """
    await ctx.info(f"查找最佳匹配: '{query[:50]}...'")
    await ctx.report_progress(progress=0, total=100)
    
    # 使用 asyncer 在后台线程中执行同步操作
    result = await asyncer.asyncify(_find_best_match_sync)(browser_pool, query)
    
    await ctx.report_progress(progress=100, total=100)
    
    if result.get("isError"):
        await ctx.error(f"匹配失败: {result.get('error')}")
    elif result.get("best_match"):
        await ctx.info(f"找到最佳匹配: {result['best_match']['title'][:50]}")
    else:
        await ctx.info("未找到匹配结果")
    
    return result


# =================== MCP 资源定义 ===================

@mcp.resource("cnki://search-types")
async def get_search_types(ctx: Context) -> str:
    """返回支持的搜索类型列表"""
    return json.dumps({
        "description": "CNKI 支持的搜索类型",
        "chinese_types": list(SEARCH_TYPES.keys()),
        "english_aliases": list(SEARCH_TYPE_ALIASES.keys()),
        "default": "主题",
        "request_id": ctx.request_id
    }, ensure_ascii=False, indent=2)


@mcp.resource("cnki://status")
async def get_server_status(ctx: Context) -> str:
    """返回服务器状态信息"""
    return json.dumps({
        "server_name": "CNKI 论文检索服务",
        "version": "2.1.0",
        "features": [
            "浏览器池复用",
            "超时自动关闭（10分钟）",
            "线程安全",
            "Context 日志支持",
            "参数验证 (Annotated + Field)",
            "依赖注入 (Depends)",
            "进度报告 (report_progress)",
            "asyncer 异步执行"
        ],
        "tools": ["search_cnki", "get_paper_detail", "find_best_match"],
        "resources": ["cnki://search-types", "cnki://status"],
        "request_id": ctx.request_id
    }, ensure_ascii=False, indent=2)



# =================== 服务器入口 ===================

def main():
    """Entry point for the CLI."""
    # 默认使用 stdio 模式运行（供 Cursor/Claude Desktop 使用）
    mcp.run()

if __name__ == "__main__":
    main()
