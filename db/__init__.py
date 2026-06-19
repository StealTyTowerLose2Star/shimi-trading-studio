"""
拾米交易工作室 - 数据库统一入口
将拆分的子模块重新导出，保持所有 `from db import X` 向后兼容
"""
from db.core import get_db, init_db, _row_to_dict, _rows_to_list, _ph, _last_id_suffix
from db.users_db import hash_password, register_user, login_user, verify_token, list_users
from db.trades import add_trade, update_trade, delete_trade, get_trades, get_trade_summary
from db.recommendations import (
    save_recommendations, get_recommendations, get_all_recommendations,
    save_review_report, get_latest_review, get_review_history,
)
from db.migrate import migrate_from_sqlite

# 初始化数据库（模块加载时自动执行）
init_db()
