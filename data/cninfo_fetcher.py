"""
拾米交易工作室 - 巨潮资讯公告数据获取层
cninfo.com.cn — A股法定信息披露平台

支持催化剂类型:
  C3 重大合同/中标
  C5 资产重组/并购
  C7 业绩预告/快报/超预期
"""
import time
import requests
from datetime import datetime, timedelta
from cache import cache_or_fetch, cache_set


CNINFO_SEARCH_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

# 催化剂搜索关键词配置
CATALYST_SEARCHES = {
    "C3_重大合同": {
        "keywords": [
            "重大合同", "中标", "签订合同", "经营合同",
            "重大销售合同", "项目合同", "战略合作框架协议",
            "收到中标通知书",
        ],
        "score_base": 14,  # D7 基础分
    },
    "C5_资产重组": {
        "keywords": [
            "重大资产重组", "发行股份购买资产", "收购",
            "并购", "资产注入", "借壳", "要约收购",
            "重大资产出售",
        ],
        "score_base": 16,
    },
    "C7_业绩超预期": {
        "keywords": [
            "业绩预告", "业绩快报", "业绩大幅上升",
            "业绩预增", "净利润增长", "业绩修正",
            "扭亏为盈",
        ],
        "score_base": 12,
    },
}


def _search_announcements(keyword, start_date, end_date, page=1, size=50):
    """搜索公告"""
    data = {
        "pageNum": str(page),
        "pageSize": str(size),
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": keyword,
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
    }
    try:
        r = requests.post(CNINFO_SEARCH_URL, data=data, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "utf-8"
        return r.json()
    except Exception as e:
        print(f"[cninfo] search error for '{keyword}': {e}")
        return None


def fetch_catalyst_announcements(start_date=None, end_date=None):
    """
    批量获取催化剂公告

    Args:
        start_date: YYYY-MM-DD, 默认30天前
        end_date: YYYY-MM-DD, 默认今天

    Returns:
        dict: {
            "events": [{
                "type": "C3_重大合同"|"C5_资产重组"|"C7_业绩超预期",
                "title": str,
                "company": str,
                "sec_code": str,  # 6位代码
                "date": str,       # YYYY-MM-DD
                "url": str,
                "keyword": str,
                "score_base": int,
            }],
            "total": int,
            "fetch_time": str,
        }
    """
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    all_events = []
    seen = set()  # dedup by (sec_code, title)

    for cat_type, config in CATALYST_SEARCHES.items():
        for keyword in config["keywords"]:
            result = _search_announcements(keyword, start_date, end_date, page=1, size=30)
            if result is None:
                continue

            announcements = result.get("announcements") or []
            for ann in announcements:
                sec_name = ann.get("secName", "")
                sec_code = ann.get("secCode", "")
                title = ann.get("announcementTitle", "")
                ann_time = ann.get("announcementTime", 0)
                url = ann.get("adjunctUrl", "")

                if not sec_code or not title:
                    continue

                # 去重
                dedup_key = f"{sec_code}|{title[:40]}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                # 转换时间戳
                try:
                    ann_date = datetime.fromtimestamp(ann_time / 1000).strftime("%Y-%m-%d")
                except Exception:
                    ann_date = start_date

                all_events.append({
                    "type": cat_type,
                    "title": title,
                    "company": sec_name,
                    "sec_code": sec_code,
                    "date": ann_date,
                    "url": f"http://www.cninfo.com.cn/new/disclosure/detail?stockCode={sec_code}&announcementId={ann.get('announcementId','')}&announcementTime={ann_time}",
                    "keyword": keyword,
                    "score_base": config["score_base"],
                })

            time.sleep(0.3)  # 避免请求过快

    all_events.sort(key=lambda x: x["date"], reverse=True)

    return {
        "events": all_events,
        "total": len(all_events),
        "period": f"{start_date}~{end_date}",
        "fetch_time": datetime.now().isoformat(),
    }


def fetch_recent_catalysts(days=30):
    """获取最近N天的催化剂（便捷函数，带缓存）"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cache_key = f"cninfo_catalysts_{start}_{end}"
    return cache_or_fetch(cache_key,
                         lambda: fetch_catalyst_announcements(start, end),
                         3600)


def fetch_monthly_catalysts(year_month):
    """获取某月的催化剂公告

    Args:
        year_month: str, e.g. "202503"

    Returns:
        dict: 同 fetch_catalyst_announcements
    """
    y = year_month[:4]
    m = year_month[4:6]
    start = f"{y}-{m}-01"
    # 月末
    if m == "12":
        end = f"{y}-12-31"
    else:
        end = f"{y}-{int(m)+1:02d}-01"
        # 减一天得到上月末
        from datetime import datetime as _dt, timedelta as _td
        end_dt = _dt.strptime(end, "%Y-%m-%d") - _td(days=1)
        end = end_dt.strftime("%Y-%m-%d")

    return fetch_catalyst_announcements(start, end)
