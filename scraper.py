import os
import time
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

TARGET_CATEGORIES = [
    "[國內成分證券ETF]-新台幣交易",
    "[標的指數或投資範圍位於亞洲時區之ETF]-新台幣交易",
    "[標的指數或投資範圍位於亞洲時區之ETF]-外幣交易",
    "[標的指數或投資範圍位於歐美時區之ETF]-新台幣交易",
    "[標的指數或投資範圍位於歐美時區之ETF]-外幣交易",
    "[全球時區ETF]-新台幣交易",
    "[全球時區ETF]-外幣交易"
]

MARKETS_CONFIG = [
    {
        "name": "集中市場", 
        "url": "https://mis.twse.com.tw/stock/various-areas/etf-price/indicator-disclosure-etf?lang=zhHant"
    },
    {
        "name": "櫃買市場", 
        "url": "https://mis.twse.com.tw/stock/various-areas/etf-price/value-disclosure-etf?lang=zhHant"
    }
]

def parse_market_html(html, market_name):
    soup = BeautifulSoup(html, "html.parser")
    market_data = {}
    
    for category in TARGET_CATEGORIES:
        text_elements = soup.find_all(string=lambda text: text and category in text)
        
        table = None
        for el in text_elements:
            parent_section = el.find_parent("section")
            if parent_section:
                table = parent_section.find("table")
                if table:
                    break 
                    
        if not table:
            continue
            
        headers = [th.text.strip() for th in table.find_all("th")]
        code_idx, premium_idx, time_idx = -1, -1, -1
        
        for i, h in enumerate(headers):
            if "代號" in h or "名稱" in h:
                code_idx = i
            elif "折溢價" in h:
                premium_idx = i
            elif "時間" in h: 
                time_idx = i
                
        if code_idx == -1 or premium_idx == -1 or time_idx == -1:
            continue
            
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]
        
        threshold = 5.0 if "歐美時區" in category or "全球時區" in category else 2.0
        
        category_data = []
        for row in rows:
            cols = row.find_all("td")
            max_idx = max(code_idx, premium_idx, time_idx)
            
            if len(cols) > max_idx:
                etf_name = cols[code_idx].text.strip()
                premium_str = cols[premium_idx].text.strip()
                data_time = cols[time_idx].text.strip() 
                
                if etf_name and premium_str:
                    try:
                        clean_val = premium_str.replace('%', '').replace(',', '').strip()
                        premium_float = float(clean_val)
                        
                        if abs(premium_float) >= threshold:
                            category_data.append({
                                "市場": market_name,
                                "ETF代號/名稱": etf_name, 
                                "預估折溢價幅度": premium_str,
                                "資料時間": data_time 
                            })
                    except ValueError:
                        pass
                        
        if category_data:
            market_data[category] = category_data
            
    return market_data

def scrape_all_markets():
    """主程序：負責啟動瀏覽器並依序爬取所有設定的市場，加入自動重試機制"""
    aggregated_data = {cat: [] for cat in TARGET_CATEGORIES}
    errors = []  # 🌟 用來記錄連線失敗的錯誤訊息
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        for market in MARKETS_CONFIG:
            max_retries = 3  # 🌟 設定最多重試次數為 3 次
            
            for attempt in range(1, max_retries + 1):
                print(f"正在連線至 {market['name']} 網頁... (第 {attempt} 次嘗試)")
                try:
                    # 🌟 寬容機制 1：設定網頁載入的最長等待時間為 30000 毫秒 (30 秒)
                    page.goto(market['url'], timeout=30000)
                    
                    # 🌟 寬容機制 2：等待表格出現也給予 30 秒的耐心
                    page.wait_for_selector("table", timeout=30000)
                    
                    # 🌟 稍微多等 3 秒，確保 JavaScript 完全把表格數字填入
                    time.sleep(3) 
                    html = page.content()
                    
                    parsed_data = parse_market_html(html, market['name'])
                    
                    for cat, items in parsed_data.items():
                        aggregated_data[cat].extend(items)
                        
                    print(f"✅ {market['name']} 爬取與篩選完成！")
                    break  # 🌟 成功抓到資料，跳出重試迴圈，進行下一個市場
                    
                except Exception as e:
                    print(f"⚠️ 第 {attempt} 次連線 {market['name']} 失敗: {type(e).__name__}")
                    
                    if attempt == max_retries:
                        # 🌟 真的重試了 3 次都不行，才記錄到最終錯誤清單，發送 TG 錯誤通知
                        error_msg = f"連線 {market['name']} 失敗 ({type(e).__name__})"
                        print(f"❌ {error_msg} (已放棄)")
                        errors.append(error_msg)
                    else:
                        # 🌟 自動重試機制：失敗後等 5 秒，再給它一次機會（就像你手動重跑一樣）
                        print("⏳ 伺服器可能正在忙碌，等待 5 秒後進行重試...")
                        time.sleep(5)
                
        browser.close()
        
    final_data = {k: v for k, v in aggregated_data.items() if v}
    return final_data, errors

def send_telegram_message(msg_text):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    
    if not bot_token or not chat_id:
        print("❌ 未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg_text
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"❌ Telegram 發送失敗，狀態碼: {response.status_code}")
            print(f"🔍 詳細錯誤原因: {response.text}")
        else:
            print("✅ Telegram 發送成功！")
    except Exception as e:
        print(f"❌ Telegram 網路連線錯誤: {e}")

if __name__ == "__main__":
    data, errors = scrape_all_markets()
    full_message = ""
    
    # === 情境判斷開始 ===
    if errors and not data:
        # 情境一：發生錯誤，且完全沒有抓到任何資料
        full_message = "❌ [系統異常通知]\n證交所網頁連線失敗或發生技術性問題，本次無法抓取折溢價資料。\n\n詳細狀況：\n- " + "\n- ".join(errors)
        
    elif not data:
        # 情境二：連線成功，但沒有任何一檔 ETF 超過門檻
        full_message = "🟢 [盤面穩定]\n目前市場連線正常，閾值內無資料。"
        
    else:
        # 情境三：連線成功，且有抓到超過門檻的 ETF
        message_lines = []
        
        # 如果有部分市場失敗，但還是有抓到一些資料，加註警告
        if errors:
            message_lines.append("⚠️ [部分連線失敗]\n- " + "\n- ".join(errors) + "\n")
            
        message_lines.append("📈 折溢價異常監控清單：")

        for cat, items in data.items():
            # 保持原本的純文字分類標題
            message_lines.append(f"\n📍 {cat}")
            for item in items: 
                # 保持原本的半形括號排版
                etf_block = (
                    f"[{item['市場']}] \n"
                    f"{item['ETF代號/名稱']} \n"
                    f"折溢價: {item['預估折溢價幅度']} \n"
                    f"(資料時間: {item['資料時間']})\n"
                )
                message_lines.append(etf_block)
                
        full_message = "\n".join(message_lines)
    # === 情境判斷結束 ===
    
    print("\n" + "="*60)
    print(full_message)
    
    send_telegram_message(full_message)