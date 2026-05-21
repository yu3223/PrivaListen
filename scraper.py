import os
import time
import requests  # 🎯 記得在環境中 pip install requests
from datetime import datetime
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

# 集中管理市場名稱與對應網址
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

def get_taiwan_time_str():
    """產生執行當下的 [民國年/月/日][時:分] 格式字串"""
    now = datetime.now()
    tw_year = now.year - 1911
    return f"[{tw_year:03d}/{now.strftime('%m/%d')}][{now.strftime('%H:%M')}]"

def parse_market_html(html, market_name):
    """專門處理單一網頁的 HTML 解析、欄位搜尋與折溢價篩選"""
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
            
        # 動態尋找欄位索引 (代號名稱、折溢價、資料時間)
        headers = [th.text.strip() for th in table.find_all("th")]
        code_idx, premium_idx, time_idx = -1, -1, -1
        
        for i, h in enumerate(headers):
            if "代號" in h or "名稱" in h:
                code_idx = i
            elif "折溢價" in h:
                premium_idx = i
            elif "時間" in h: # 🎯 自動定位最右側的「資料時間」欄位
                time_idx = i
                
        if code_idx == -1 or premium_idx == -1 or time_idx == -1:
            continue
            
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]
        
        # 篩選門檻 (歐美時區 5%，其他 2%)
        threshold = 5.0 if "歐美時區" in category else 2.0
        
        category_data = []
        for row in rows:
            cols = row.find_all("td")
            max_idx = max(code_idx, premium_idx, time_idx)
            
            if len(cols) > max_idx:
                etf_name = cols[code_idx].text.strip()
                premium_str = cols[premium_idx].text.strip()
                data_time = cols[time_idx].text.strip() # 抓取資料時間
                
                if etf_name and premium_str:
                    try:
                        clean_val = premium_str.replace('%', '').replace(',', '').strip()
                        premium_float = float(clean_val)
                        
                        if abs(premium_float) >= threshold:
                            category_data.append({
                                "市場": market_name,
                                "ETF代號/名稱": etf_name, 
                                "預估折溢價幅度": premium_str,
                                "資料時間": data_time # 儲存至資料字典
                            })
                    except ValueError:
                        pass
                        
        if category_data:
            market_data[category] = category_data
            
    return market_data

def scrape_all_markets():
    """主程序：負責啟動瀏覽器並依序爬取所有設定的市場"""
    aggregated_data = {cat: [] for cat in TARGET_CATEGORIES}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        for market in MARKETS_CONFIG:
            print(f"正在連線至 {market['name']} 網頁...")
            page.goto(market['url'])
            
            try:
                page.wait_for_selector("table", timeout=15000)
                time.sleep(2) 
                html = page.content()
                
                parsed_data = parse_market_html(html, market['name'])
                
                for cat, items in parsed_data.items():
                    aggregated_data[cat].extend(items)
                    
                print(f"✅ {market['name']} 爬取與篩選完成！")
            except Exception as e:
                print(f"❌ {market['name']} 發生錯誤: {e}")
                
        browser.close()
        
    final_data = {k: v for k, v in aggregated_data.items() if v}
    return final_data

def send_line_message(msg_text):
    """透過 LINE Messaging API 發送 Push Message"""
    # 從環境變數讀取密鑰，確保安全性
    access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    to_id = os.getenv("LINE_TO_ID")   # 可以是 User ID 或 Group ID
    
    if not access_token or not to_id:
        print("❌ 找不到 LINE 憑證環境變數，取消發送發送。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    payload = {
        "to": to_id,
        "messages": [
            {
                "type": "text",
                "text": msg_text
            }
        ]
    }
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        print("🚀 LINE 訊息發送成功！")
    else:
        print(f"❌ LINE 發送失敗，狀態碼: {response.status_code}, 回傳內容: {response.text}")

if __name__ == "__main__":
    data = scrape_all_markets()
    run_timestamp = get_taiwan_time_str()
    
    # 🎯 開始組裝 LINE 訊息字串
    message_lines = [f"{run_timestamp} 📈 折溢價異常監控清單："]

    for cat, items in data.items():
        message_lines.append(f"\n📍 {cat}")
        for item in items: 
            message_lines.append(f"   [{item['市場']}] {item['ETF代號/名稱']} | 折溢價: {item['預估折溢價幅度']} (資料時間: {item['資料時間']})")
            
    full_message = "\n".join(message_lines)
    
    # 印在終端機看排版
    print("\n" + "="*60)
    print(full_message)
    
    # 正式發送給 LINE
    send_line_message(full_message)