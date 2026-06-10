# update_dashboard.py
# 每天自動抓最新 SAP 庫存 .txt，更新 HTML 並 push 到 GitHub Pages
#
# 使用方式：
#   python update_dashboard.py
#
# Windows 工作排程器設定：
#   程式：python
#   引數："C:\Users\K1080310\OneDrive - 康軒文教事業股份有限公司\桌面\每日庫存\reprint-alert-dashboard\update_dashboard.py"

import os
import re
import glob
import json
import subprocess
from pathlib import Path
from datetime import datetime

# ── 路徑設定 ──────────────────────────────────────────────
# SAP 每日下載的 .txt 存放資料夾（Power Automate 存到這裡）
INV_FOLDER = r"C:\Users\K1080310\OneDrive - 康軒文教事業股份有限公司\桌面\每日庫存"

# repo 資料夾（clone 下來的位置）
REPO_DIR   = Path(r"C:\Users\K1080310\OneDrive - 康軒文教事業股份有限公司\桌面\每日庫存\reprint-alert-dashboard")

# HTML 儀表板在 repo 裡的路徑
DASHBOARD  = REPO_DIR / "inventory_dashboard_v5.html"
# ─────────────────────────────────────────────────────────


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def find_latest_txt(folder):
    """找資料夾裡最新的 SAP 庫存 .txt"""
    files = glob.glob(os.path.join(folder, "*.txt"))
    if not files:
        return None
    def sort_key(f):
        m = re.search(r'(\d{8})', Path(f).name)
        return m.group(1) if m else "00000000"
    return Path(sorted(files, key=sort_key, reverse=True)[0])


def parse_sap_txt(filepath):
    """解析 SAP Big5/UTF-16 TAB 分隔 .txt，回傳 {sku: {ver: avail}}"""
    with open(filepath, 'rb') as f:
        raw = f.read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw.decode('utf-16')
    else:
        text = raw.decode('big5', errors='replace')

    inv = {}
    for line in text.splitlines():
        cols = line.split('\t')
        if len(cols) < 13:
            continue
        sku = cols[0].replace('\ufeff', '').strip()
        if not sku or not re.match(r'^\d{10}$', sku):
            continue
        ver = cols[7].strip() if len(cols) > 7 else ''
        try:
            avail = max(0, int(cols[12].strip().replace(',', '')))
        except ValueError:
            avail = 0
        if sku not in inv:
            inv[sku] = {}
        inv[sku][ver] = inv[sku].get(ver, 0) + avail
    return inv


def inject_inventory(html, inv, file_date):
    """把最新庫存數字和日期注入 HTML"""
    match = re.search(r'const PRODS = (\[.*?\]);', html, re.DOTALL)
    if not match:
        raise ValueError("找不到 PRODS 資料，請確認 HTML 格式")

    prods = json.loads(match.group(1))
    updated = 0
    for p in prods:
        sku_inv = inv.get(p['sku'])
        if not sku_inv:
            continue
        p['versions'] = [
            {'ver': v['ver'], 'avail': sku_inv.get(str(v['ver']), v['avail'])}
            for v in p.get('versions', [])
        ]
        updated += 1

    new_json = json.dumps(prods, ensure_ascii=False, separators=(',', ':'))
    html = html[:match.start(1)] + new_json + html[match.end(1):]
    html = re.sub(r"let invDate = '[^']*';", f"let invDate = '{file_date}';", html)
    return html, updated


def git(args, cwd):
    """執行 git 指令，回傳輸出"""
    result = subprocess.run(
        ['git'] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    if result.returncode != 0:
        # commit 時沒有變更不算錯誤，直接回傳空字串
        if 'nothing to commit' in result.stdout + result.stderr:
            return ''
        raise RuntimeError(f"git {' '.join(args)} 失敗：\n{result.stderr}\n{result.stdout}")
    return result.stdout.strip()


def main():
    log("===== 開始更新儀表板 =====")

    # 1. 找最新 .txt
    txt_path = find_latest_txt(INV_FOLDER)
    if not txt_path:
        log(f"⚠ 找不到 .txt，請確認資料夾：{INV_FOLDER}")
        return
    m = re.search(r'(\d{8})', txt_path.name)
    file_date = f"{m.group(1)[:4]}.{m.group(1)[4:6]}.{m.group(1)[6:]}" if m else datetime.now().strftime('%Y.%m.%d')
    log(f"庫存檔：{txt_path.name}（製表日：{file_date}）")

    # 2. 解析庫存
    inv = parse_sap_txt(txt_path)
    log(f"解析完成：{len(inv)} 個料號")

    # 3. 讀 HTML
    if not DASHBOARD.exists():
        log(f"⚠ 找不到 HTML：{DASHBOARD}")
        return
    html = DASHBOARD.read_text(encoding='utf-8')

    # 4. 注入庫存
    html, updated = inject_inventory(html, inv, file_date)
    log(f"更新了 {updated} 個料號的庫存")

    # 5. 寫回 HTML
    DASHBOARD.write_text(html, encoding='utf-8')
    log(f"HTML 已更新：{DASHBOARD.name}")

    # 6. git pull（先同步，避免 push 衝突）
    log("git pull...")
    try:
        out = git(['pull', '--no-rebase'], cwd=REPO_DIR)
        log(f"  {out or 'Already up to date'}")
    except RuntimeError as e:
        log(f"⚠ pull 失敗（繼續嘗試）：{e}")

    # 7. 再次注入庫存（pull 可能覆蓋剛才的更新）
    log("重新注入庫存（pull 後）...")
    html2 = DASHBOARD.read_text(encoding='utf-8')
    html2, updated2 = inject_inventory(html2, inv, file_date)
    DASHBOARD.write_text(html2, encoding='utf-8')
    log(f"重新更新了 {updated2} 個料號")

    # 8. git add + commit + push
    log("git add...")
    git(['add', 'inventory_dashboard_v5.html'], cwd=REPO_DIR)

    # 確認有變更才 commit
    status = git(['status', '--porcelain'], cwd=REPO_DIR)
    log(f"git status：{'有變更' if status else '無變更（庫存數字與上次相同）'}")

    if status:
        commit_msg = f"自動更新庫存 {file_date}"
        log(f"git commit：{commit_msg}")
        git(['commit', '-m', commit_msg], cwd=REPO_DIR)

        log("git push...")
        git(['push'], cwd=REPO_DIR)
        log(f"✅ 完成！網頁約 1 分鐘後更新：https://summer1719-coder.github.io/reprint-alert-dashboard/")
    else:
        log("ℹ 今日庫存數字與已發布版本相同，無需更新網頁")
        log(f"✅ 完成（無變更）")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f"❌ 錯誤：{e}")
        raise
