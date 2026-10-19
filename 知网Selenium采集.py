"""用 Selenium 从知网检索并采集文献元数据、摘要和关键词。

运行方式：
    python 知网Selenium采集.py

程序会从知网检索页开始，运行时输入关键词和采集数量（默认 10）。
如果出现知网安全验证或登录页面，程序会暂停；请在它打开的 Edge 窗口中
手动完成验证/登录，再回到终端按回车继续。程序不尝试绕过验证码或权限。
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


CNKI_HOME = "https://www.cnki.net/"
RESULT_URL = "https://kns.cnki.net/kns8s/defaultresult/index?kw={}"
DEFAULT_LIMIT = 10
DETAIL_DELAY_SECONDS = 2.5
PAGE_TIMEOUT_SECONDS = 25

FIELDNAMES = [
    "题名",
    "作者",
    "来源",
    "发表时间",
    "文献类型",
    "摘要",
    "关键词",
    "详情页链接",
    "摘要/关键词状态",
    "采集时间",
]


def clean_text(value: str | None) -> str:
    """压缩网页中的空白，但保留中文文本内容。"""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def make_driver() -> webdriver.Edge:
    """创建可见 Edge；未指定 driver 路径时由 Selenium Manager 自动管理。"""
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    # 当前任务需要用户在浏览器中处理登录/安全验证，因此不使用 headless。
    return webdriver.Edge(options=options)


def wait_for_page(driver: webdriver.Edge, timeout: int = PAGE_TIMEOUT_SECONDS) -> None:
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}
    )


def body_text(driver: webdriver.Edge) -> str:
    try:
        return clean_text(driver.find_element(By.TAG_NAME, "body").text)
    except NoSuchElementException:
        return ""


def challenge_present(driver: webdriver.Edge) -> bool:
    text = body_text(driver)
    markers = ("安全验证", "请完成安全验证", "请依次点击", "验证码", "访问过于频繁")
    return any(marker in text for marker in markers)


def pause_for_manual_action(driver: webdriver.Edge, reason: str) -> bool:
    """暂停给用户处理安全验证/登录；返回 False 表示用户要求退出。"""
    print(f"\n检测到{reason}。")
    print("请在脚本打开的 Edge 窗口中手动完成操作。不要关闭该窗口。")
    answer = input("完成后按回车继续；输入 q 并回车退出：").strip().lower()
    return answer != "q"


def open_search_page(driver: webdriver.Edge, keyword: str) -> str:
    """从知网首页输入关键词；若首页控件变化则使用同一检索条件的结果页。"""
    driver.get(CNKI_HOME)
    wait_for_page(driver)
    try:
        search_box = WebDriverWait(driver, 12).until(
            EC.presence_of_element_located(
                (
                    By.CSS_SELECTOR,
                    "#txt_search, input[placeholder*='检索'], input[placeholder*='搜索']",
                )
            )
        )
        search_box.clear()
        search_box.send_keys(keyword)
        search_box.send_keys(Keys.ENTER)
        wait_for_page(driver)
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "table"))
            )
        except TimeoutException:
            # 首页控件可能只更新了输入框而没有提交，改用同一关键词的结果页。
            driver.get(RESULT_URL.format(quote(keyword)))
            wait_for_page(driver)
    except (TimeoutException, WebDriverException):
        # 仅作为页面改版时的兼容入口，仍然是由 keyword 生成 URL，用户不需手改链接。
        driver.get(RESULT_URL.format(quote(keyword)))
        wait_for_page(driver)

    if challenge_present(driver) and not pause_for_manual_action(driver, "知网安全验证"):
        raise KeyboardInterrupt
    WebDriverWait(driver, PAGE_TIMEOUT_SECONDS).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "table"))
    )
    return driver.current_url


def extract_result_rows(driver: webdriver.Edge, limit: int) -> list[dict[str, str]]:
    """读取当前结果页前 limit 条文献的列表字段和详情页 URL。"""
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    records: list[dict[str, str]] = []
    for row in rows:
        if len(records) >= limit:
            break
        try:
            title_links = row.find_elements(By.CSS_SELECTOR, "a[href*='/kcms2/article/abstract']")
            if not title_links:
                continue
            title_link = title_links[0]
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 5:
                continue
            title = clean_text(title_link.text)
            authors = clean_text(cells[2].text)
            authors = re.sub(r"\s*;\s*", "; ", authors)
            records.append(
                {
                    "题名": title,
                    "作者": authors,
                    "来源": clean_text(cells[3].text),
                    "发表时间": clean_text(cells[4].text),
                    "文献类型": clean_text(cells[5].text) if len(cells) > 5 else "",
                    "摘要": "",
                    "关键词": "",
                    "详情页链接": title_link.get_attribute("href") or "",
                    "摘要/关键词状态": "待读取",
                    "采集时间": datetime.now().isoformat(timespec="seconds"),
                }
            )
        except (StaleElementReferenceException, NoSuchElementException):
            continue
    return records


def current_page_signature(driver: webdriver.Edge) -> tuple[str, ...]:
    """返回当前结果页的详情链接，用于判断翻页是否成功及识别重复页。"""
    signature: list[str] = []
    for row in driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
        try:
            links = row.find_elements(
                By.CSS_SELECTOR, "a[href*='/kcms2/article/abstract']"
            )
            if not links:
                continue
            link = links[0]
            signature.append(
                link.get_attribute("href") or clean_text(link.text)
            )
        except (StaleElementReferenceException, NoSuchElementException):
            continue
    return tuple(value for value in signature if value)


def find_next_page_control(driver: webdriver.Edge):
    """查找知网结果页可用的“下一页”控件，兼容常见分页标记。"""
    selectors = (
        "#PageNext",
        "[id*='PageNext']",
        "[rel='next']",
        "[aria-label*='下一页']",
        "[title*='下一页']",
        ".pages .next",
        ".pagination .next",
        "a.next",
        "button.next",
    )
    candidates = []
    for selector in selectors:
        candidates.extend(driver.find_elements(By.CSS_SELECTOR, selector))
    candidates.extend(
        driver.find_elements(
            By.XPATH,
            "//a[contains(normalize-space(.), '下一页') or normalize-space(.)='下页' "
            "or normalize-space(.)='Next' or normalize-space(.)='>' "
            "or normalize-space(.)='›' or normalize-space(.)='»'] "
            "| //button[contains(normalize-space(.), '下一页') "
            "or normalize-space(.)='下页' or normalize-space(.)='Next' "
            "or normalize-space(.)='>' or normalize-space(.)='›' "
            "or normalize-space(.)='»']",
        )
    )

    visited: set[int] = set()
    for element in candidates:
        if id(element) in visited:
            continue
        visited.add(id(element))
        try:
            element_id = (element.get_attribute("id") or "").casefold()
            class_name = (element.get_attribute("class") or "").casefold()
            rel = (element.get_attribute("rel") or "").casefold()
            title = (element.get_attribute("title") or "").casefold()
            aria_label = (element.get_attribute("aria-label") or "").casefold()
            text = clean_text(element.text).casefold()
            is_next_control = (
                "pagenext" in element_id
                or rel == "next"
                or re.search(r"(?:^|[-_ ])next(?:$|[-_ ])", class_name)
                is not None
                or "下一页" in title
                or "下一页" in aria_label
                or title == "next"
                or aria_label == "next"
                or "下一页" in text
                or text in {"下页", "next", ">", "›", "»"}
            )
            if not is_next_control or not element.is_displayed() or not element.is_enabled():
                continue
            if (element.get_attribute("aria-disabled") or "").casefold() == "true":
                continue
            if element.get_attribute("disabled") is not None:
                continue
            if re.search(r"(?:^|\s)disabled(?:$|\s)", class_name):
                continue
            disabled_ancestors = element.find_elements(
                By.XPATH,
                "./ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' disabled ')]",
            )
            if disabled_ancestors:
                continue
            return element
        except (StaleElementReferenceException, NoSuchElementException):
            continue
    return None


def advance_to_next_result_page(
    driver: webdriver.Edge, previous_signature: tuple[str, ...]
) -> bool:
    """点击下一页，并确认结果列表确实已更新。"""
    next_control = find_next_page_control(driver)
    if next_control is None:
        print("没有找到可用的下一页控件，停止翻页。")
        return False

    try:
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(next_control))
        next_control.click()
    except (StaleElementReferenceException, TimeoutException, WebDriverException) as exc:
        print(f"无法点击下一页（{type(exc).__name__}），停止翻页。")
        return False

    try:
        WebDriverWait(driver, PAGE_TIMEOUT_SECONDS).until(
            lambda d: challenge_present(d)
            or (
                bool(current_page_signature(d))
                and current_page_signature(d) != previous_signature
            )
        )
    except TimeoutException:
        print("点击下一页后结果列表没有确认变化，停止翻页以避免重复采集。")
        return False

    if challenge_present(driver):
        if not pause_for_manual_action(driver, "知网安全验证"):
            raise KeyboardInterrupt
        try:
            WebDriverWait(driver, PAGE_TIMEOUT_SECONDS).until(
                lambda d: bool(current_page_signature(d))
                and current_page_signature(d) != previous_signature
            )
        except TimeoutException:
            print("验证后结果列表仍未变化，停止翻页。")
            return False
    return True


def collect_result_rows(driver: webdriver.Edge, limit: int) -> list[dict[str, str]]:
    """逐页收集列表信息，按详情链接去重，直到达到数量或没有下一页。"""
    records: list[dict[str, str]] = []
    seen_records: set[str] = set()
    seen_pages: set[tuple[str, ...]] = set()
    page_number = 1

    while len(records) < limit:
        page_signature = current_page_signature(driver)
        if not page_signature:
            print("当前结果页没有检测到文献详情链接，停止采集。")
            break
        if page_signature in seen_pages:
            print("检测到重复的结果页，停止翻页以避免重复采集。")
            break
        seen_pages.add(page_signature)

        page_records = extract_result_rows(driver, limit)
        before_count = len(records)
        for record in page_records:
            record_key = record["详情页链接"] or "\x1f".join(
                record[field] for field in ("题名", "作者", "来源", "发表时间")
            )
            if record_key in seen_records:
                continue
            seen_records.add(record_key)
            records.append(record)
            if len(records) >= limit:
                break

        print(
            f"第 {page_number} 页新增 {len(records) - before_count} 条，"
            f"累计 {len(records)}/{limit} 条。"
        )
        if len(records) >= limit:
            break
        if not advance_to_next_result_page(driver, page_signature):
            break
        page_number += 1

    return records


def first_visible_text(driver: webdriver.Edge, selectors: Iterable[str]) -> str:
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                if element.is_displayed():
                    value = clean_text(element.text)
                    if value:
                        return value
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return ""


def strip_label(value: str, labels: Iterable[str]) -> str:
    for label in labels:
        value = re.sub(rf"^{re.escape(label)}\s*[:：]?\s*", "", value, flags=re.IGNORECASE)
    return clean_text(value)


def extract_detail(driver: webdriver.Edge) -> tuple[str, str, str]:
    """读取详情页摘要和关键词，返回 (摘要, 关键词, 状态)。"""
    if challenge_present(driver):
        if not pause_for_manual_action(driver, "知网安全验证"):
            return "", "", "用户退出"
        wait_for_page(driver)

    abstract = first_visible_text(
        driver,
        (
            "#ChDivSummary",
            "#abstract",
            ".abstract-text",
            ".summary",
            "[class*='abstract']",
            "[id*='Summary']",
        ),
    )
    keywords = first_visible_text(
        driver,
        (
            "#ChDivKeyWord",
            "#keywords",
            ".keywords",
            ".keyword",
            "[class*='keyword']",
            "[id*='KeyWord']",
        ),
    )
    abstract = strip_label(abstract, ("摘要", "Abstract"))
    keywords = strip_label(keywords, ("关键词", "关键字", "Keywords", "Key words"))
    if abstract and keywords:
        status = "已读取"
    elif abstract:
        status = "摘要已读取，关键词未显示"
    elif keywords:
        status = "关键词已读取，摘要未显示"
    else:
        status = "页面未显示摘要/关键词（可能需要登录、权限或页面结构变化）"
    return abstract, keywords, status


def save_csv(records: list[dict[str, str]], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def save_checkpoint(records: list[dict[str, str]], checkpoint_path: Path) -> None:
    checkpoint_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    keyword = input("请输入检索关键词（默认：大数据）：").strip() or "大数据"
    raw_limit = input(f"请输入采集数量（默认：{DEFAULT_LIMIT}）：").strip()
    try:
        limit = max(1, int(raw_limit or DEFAULT_LIMIT))
    except ValueError:
        print("数量不是整数，使用默认数量 10。")
        limit = DEFAULT_LIMIT

    safe_keyword = re.sub(r"[<>:\"/\\|?*]", "_", keyword)[:50]
    output_path = Path(f"知网_{safe_keyword}_{limit}条.csv")
    checkpoint_path = Path(f"知网_{safe_keyword}_{limit}条_checkpoint.json")
    driver: webdriver.Edge | None = None

    try:
        driver = make_driver()
        result_url = open_search_page(driver, keyword)
        records = collect_result_rows(driver, limit)
        if not records:
            print("没有读取到结果行。可能是页面尚未加载、需要登录，或知网页面结构已变化。")
            return 1
        print(f"已读取 {len(records)} 条列表信息，开始逐条读取摘要和关键词。")

        for index, record in enumerate(records, start=1):
            print(f"[{index}/{len(records)}] {record['题名']}")
            if not record["详情页链接"]:
                record["摘要/关键词状态"] = "缺少详情页链接"
                continue
            try:
                driver.get(record["详情页链接"])
                wait_for_page(driver)
                WebDriverWait(driver, PAGE_TIMEOUT_SECONDS).until(
                    lambda d: challenge_present(d)
                    or d.find_elements(By.TAG_NAME, "body")
                )
                abstract, keywords, status = extract_detail(driver)
                record["摘要"] = abstract
                record["关键词"] = keywords
                record["摘要/关键词状态"] = status
            except (TimeoutException, WebDriverException) as exc:
                record["摘要/关键词状态"] = f"读取失败：{type(exc).__name__}"
            finally:
                save_checkpoint(records, checkpoint_path)
            time.sleep(DETAIL_DELAY_SECONDS)
            driver.get(result_url)
            wait_for_page(driver)

        save_csv(records, output_path)
        print(f"完成：{output_path.resolve()}")
        print(f"中途检查点：{checkpoint_path.resolve()}")
        return 0
    except KeyboardInterrupt:
        print("\n已停止采集；已保存的检查点可供后续恢复参考。")
        return 130
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    sys.exit(main())
