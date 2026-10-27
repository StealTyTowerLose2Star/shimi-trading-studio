"""
拾米交易工作室 - 市场新闻快讯获取层 (免 key 真实来源)

移植自 a-stock-data V3.10.3 (https://github.com/simonlin1212/a-stock-data)
许可证: Apache-2.0 (原项目); 本文件保留来源声明, 仅抽取新闻层两个函数并做本地化适配:
  - §5.2 财联社电报 cls_telegraph  — cls.cn/v1/roll/get_roll_list, sign 纯本地计算零 key
  - §5.3 东财全球资讯 eastmoney_global_news — np-weblist.eastmoney.com 7×24 快讯
本地化差异:
  - 原项目 em_get 限流包装 → 改为 requests 直发 + 固定 UA (出站由 local_run 白名单管控)
  - 失败契约: 聚合场景下返回 [] 并 print 留痕 (与 data/market_events 的 _safe_fetch 约定一致)
  - 2026-10-27: 新增 *_detailed 变体携带四态源状态 (ok/empty/unavailable),
    区分「请求失败」与「真实空结果」; 原函数签名与 [] 契约不变

两个来源互为独立备份 (不同站点不同风控面)。
"""
import hashlib
import uuid
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _cls_telegraph_core(page_size: int) -> list:
    """财联社电报抓取核心 (失败抛异常, 供状态判定)"""
    from datetime import datetime
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    # 签名：md5(sha1(按 key 字典序拼接的 query 串))，纯本地算、无需 key
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    url = f"https://www.cls.cn/v1/roll/get_roll_list?{qs}&sign={sign}"
    headers = {"User-Agent": UA, "Referer": "https://www.cls.cn/"}
    r = requests.get(url, headers=headers, timeout=10)
    d = r.json()

    rows = []
    for item in (d.get("data") or {}).get("roll_data") or []:
        ts = item.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        rows.append({
            "title": item.get("title", "") or item.get("brief", ""),
            "content": item.get("content", "") or item.get("brief", ""),
            "time": t,
            "source": "cls",
        })
    return rows


def cls_telegraph_detailed(page_size: int = 50) -> tuple:
    """返回 (rows, status): status ∈ ok(有数据)/empty(真实空)/unavailable(请求失败)"""
    try:
        rows = _cls_telegraph_core(page_size)
        return rows, ("ok" if rows else "empty")
    except Exception as e:
        print(f"[news_fetcher] 财联社电报失败: {e}")
        return [], "unavailable"


def cls_telegraph(page_size: int = 50) -> list:
    """财联社电报（全市场实时快讯）。v1 API + 本地签名，零 key。

    来源: a-stock-data §5.2 (2026-07 实测 errno=0)。
    返回: [{title, content, time, source}]  time 为 'YYYY-MM-DD HH:MM:SS'
    失败契约: 返回 [] (不抛); 需区分失败/真实空用 cls_telegraph_detailed
    """
    rows, _ = cls_telegraph_detailed(page_size)
    return rows


def _eastmoney_global_news_core(page_size: int) -> list:
    """东财7×24快讯抓取核心 (失败抛异常, 供状态判定)"""
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web", "biz": "web_724",
        "fastColumn": "102", "sortEnd": "",
        "pageSize": str(page_size),
        "req_trace": str(uuid.uuid4()),
    }
    headers = {"User-Agent": UA, "Referer": "https://kuaixun.eastmoney.com/"}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    d = r.json()

    rows = []
    for item in (d.get("data") or {}).get("fastNewsList") or []:
        rows.append({
            "title": item.get("title", ""),
            "content": (item.get("summary", "") or "")[:200],
            "time": item.get("showTime", ""),
            "source": "eastmoney724",
        })
    return rows


def eastmoney_global_news_detailed(page_size: int = 50) -> tuple:
    """返回 (rows, status): status ∈ ok(有数据)/empty(真实空)/unavailable(请求失败)"""
    try:
        rows = _eastmoney_global_news_core(page_size)
        return rows, ("ok" if rows else "empty")
    except Exception as e:
        print(f"[news_fetcher] 东财7x24快讯失败: {e}")
        return [], "unavailable"


def eastmoney_global_news(page_size: int = 50) -> list:
    """东方财富全球财经资讯（7×24 滚动）。

    来源: a-stock-data §5.3。
    返回: [{title, content, time, source}]
    失败契约: 返回 [] (不抛); 需区分失败/真实空用 eastmoney_global_news_detailed
    """
    rows, _ = eastmoney_global_news_detailed(page_size)
    return rows


def fetch_market_news_detailed(page_size: int = 40) -> tuple:
    """汇聚两路快讯, 返回 (rows, {"cls": status, "eastmoney724": status})

    单源失败不影响另一源; status ∈ ok/empty/unavailable (四态中本层无 disabled —
    两源均免 key 不存在凭据缺失; 禁用语义由 market_events 层判定)。
    """
    crows, cst = cls_telegraph_detailed(page_size)
    erows, est = eastmoney_global_news_detailed(page_size)
    rows = crows + erows
    rows.sort(key=lambda x: x.get("time", ""), reverse=True)
    return rows, {"cls": cst, "eastmoney724": est}


def fetch_market_news(page_size: int = 40) -> list:
    """汇聚两路免 key 新闻快讯 (财联社 + 东财7×24)。

    单源失败不影响另一源; 全部失败返回 [] (调用方按 sources_status 显式呈现)。
    返回: [{title, content, time, source}] 按时间倒序。
    需区分失败/真实空时用 fetch_market_news_detailed (逐源状态)。
    """
    rows, _ = fetch_market_news_detailed(page_size)
    return rows
