#!/usr/bin/env python3
"""密码重置工具 — 管理员专用"""
import sys, os, hashlib, secrets, getpass

sys.path.insert(0, os.path.dirname(__file__))
from db.core import get_db

def hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def reset_password(username: str, new_password: str = None) -> bool:
    """重置用户密码"""
    if not new_password:
        new_password = secrets.token_hex(6)  # 12位随机密码
    
    pw_hash = hash_pw(new_password)
    conn = get_db()
    try:
        cur = conn.execute("UPDATE users SET password=? WHERE username=?", (pw_hash, username))
        conn.commit()
        if cur.rowcount > 0:
            return new_password
        return None
    finally:
        conn.close()

def list_users():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute("SELECT id, username, display_name FROM users").fetchall()]
    finally:
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 reset_password.py <用户名>")
        print("\n现有用户:")
        for u in list_users():
            print(f"  {u['id']}. {u['username']} ({u.get('display_name','')})")
        sys.exit(1)
    
    username = sys.argv[1]
    new_pw = reset_password(username)
    if new_pw:
        print(f"✅ 密码已重置: {username}")
        print(f"   新密码: {new_pw}")
        print(f"   请登录后立即修改")
    else:
        print(f"❌ 用户不存在: {username}")
