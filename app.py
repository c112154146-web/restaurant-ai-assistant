import streamlit as st
import pandas as pd
import gspread
import json
import re
import uuid
import cn2an
import google.generativeai as genai
import tempfile  # 用來處理暫存錄音檔
import os        # 用來刪除暫存檔
import time
import plotly.express as px
from google.oauth2.service_account import Credentials
from datetime import datetime
from PIL import Image
from rapidfuzz import process, fuzz
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(
    page_title="AI 智慧倉儲系統",
    page_icon="📦",
    layout="wide"
)

# 配置 Gemini API Key
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("請在 st.secrets 中配置 GEMINI_API_KEY")

SAFE_STOCK_LEVEL = 5

# =========================================================
# 1. 初始化動態狀態（確保開機時所有抽屜都安全建立）
# =========================================================
if "menu_recipes" not in st.session_state:
    st.session_state.menu_recipes = {
        "🍔 經典牛肉漢堡": {"漢堡麵包": 1.0, "牛肉排": 1.0, "美生菜": 0.1, "番茄切片": 0.1, "番茄醬": 0.05},
        "🥪 總匯三明治": {"吐司": 3.0, "雞蛋": 1.0, "火腿片": 1.0, "小黃瓜": 0.05, "美乃滋": 0.05},
        "🍳 起司蛋餅": {"蛋餅皮": 1.0, "雞蛋": 1.0, "起司片": 1.0},
        "🥓 培根蛋吐司": {"吐司": 2.0, "雞蛋": 1.0, "培根": 2.0, "美乃滋": 0.02},
        "🍗 卡拉雞腿堡": {"漢堡麵包": 1.0, "卡拉雞腿排": 1.0, "美生菜": 0.1, "美乃滋": 0.05},
        "🍟 歡樂炸物拼盤": {"薯條": 0.5, "雞塊": 5.0, "熱狗": 2.0, "番茄醬": 0.1},
        "🥛 經典鮮奶茶": {"紅茶葉": 0.1, "鮮奶": 0.2, "砂糖": 0.02, "冰塊": 0.5},
        "☕ 美式黑咖啡": {"咖啡豆": 0.05, "冰塊": 0.5}
    }

if "meal_prices" not in st.session_state:
    st.session_state.meal_prices = {
        "🍔 經典牛肉漢堡": 120.0, "🥪 總匯三明治": 85.0, "🍳 起司蛋餅": 55.0,
        "🥓 培根蛋吐司": 65.0, "🍗 卡拉雞腿堡": 95.0, "🍟 歡樂炸物拼盤": 100.0,
        "🥛 經典鮮奶茶": 50.0, "☕ 美式黑咖啡": 60.0
    }

if "ingredient_costs" not in st.session_state:
    st.session_state.ingredient_costs = {
        "漢堡麵包": 15.0, "牛肉排": 45.0, "美生菜": 20.0, "番茄切片": 25.0, "番茄醬": 5.0,
        "吐司": 5.0, "雞蛋": 7.0, "火腿片": 12.0, "小黃瓜": 10.0, "美乃滋": 8.0,
        "蛋餅皮": 10.0, "起司片": 8.0, "培根": 15.0, "卡拉雞腿排": 35.0,
        "薯條": 20.0, "雞塊": 4.0, "熱狗": 8.0,
        "紅茶葉": 30.0, "鮮奶": 60.0, "砂糖": 10.0, "冰塊": 2.0, "咖啡豆": 80.0,
        "綠茶葉": 30.0, "豆漿": 25.0, "巧克力醬": 40.0, "花生醬": 45.0, "黑胡椒醬": 35.0,
        "奶油": 50.0, "洋蔥": 15.0, "高麗菜": 20.0, "鍋貼": 3.5
    }

if "last_transaction" not in st.session_state:
    st.session_state.last_transaction = None

if "last_processed_audio" not in st.session_state:
    st.session_state.last_processed_audio = None

# =========================================================
# 2. Google Sheets 連線 (完全恢復你當初最原始的實體檔案讀取法)
# =========================================================
@st.cache_resource
def connect_spreadsheet():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        from google.oauth2.service_account import Credentials
        
        # 🟢 直接去抓專案目錄下的憑證檔案，不依賴任何 Streamlit 後台 secrets 的 Key 名稱
        creds = Credentials.from_service_account_file('google_key.json', scopes=scope)
        client = gspread.authorize(creds)
        doc = client.open("餐廳倉儲助手")
        return doc
    except Exception as e:
        st.error(f"連線失敗：{e}")
        return None

@st.cache_data(ttl=60)
def fetch_sheet_data_cached(sheet_name):
    doc = connect_spreadsheet()
    if doc:
        return doc.worksheet(sheet_name).get_all_records()
    return []

# =========================================================
# 3. 工具函式與 KPI
# =========================================================
def show_kpi_dashboard():
    df_stock = pd.DataFrame(fetch_sheet_data_cached('工作表1'))
    df_in = pd.DataFrame(fetch_sheet_data_cached('進貨紀錄'))
    df_waste = pd.DataFrame(fetch_sheet_data_cached('報廢紀錄'))

    today = datetime.now().strftime('%Y-%m-%d')
    today_in = len(df_in[df_in['日期'].astype(str).str.contains(today)]) if not df_in.empty else 0
    today_waste = len(df_waste[df_waste['日期'].astype(str).str.contains(today)]) if not df_waste.empty else 0

    low_stock = 0
    expiry_count = 0
    low_stock_list = []
    expiry_list = []

    if not df_stock.empty:
        for _, row in df_stock.iterrows():
            product = row.get('商品名稱', '')
            stock = extract_number(row.get('庫存數量', 0))
            unit = extract_unit(str(row.get('庫存數量', '')))

            if stock <= SAFE_STOCK_LEVEL:
                low_stock += 1
                low_stock_list.append(f"🚨 【{product}】庫存吃緊！目前僅剩 {stock} {unit} (安全水位: {SAFE_STOCK_LEVEL})")

            expiry = str(row.get('有效期限', '')).strip()
            if expiry and stock > 0:
                try:
                    days = (pd.to_datetime(expiry) - datetime.now()).days
                    if days <= 3:
                        expiry_count += 1
                        if days <= 1:
                            expiry_list.append((days, f"🔴 【{product}】明天到期！剩餘庫存：{stock} {unit} (效期: {expiry})"))
                        else:
                            expiry_list.append((days, f"🟡 【{product}】即將到期（剩 {days} 天）！剩餘庫存：{stock} {unit} (效期: {expiry})"))
                except: pass

    expiry_list.sort(key=lambda x: x[0])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 今日進貨", today_in)
    col2.metric("🗑️ 今日報廢", today_waste)
    col3.metric("⚠️ 即期商品", expiry_count)
    col4.metric("🚨 低庫存", low_stock)

    exp_col1, exp_col2 = st.columns(2)
    with exp_col1:
        if expiry_count > 0:
            with st.expander(f"🔍 點擊查看 {expiry_count} 筆即期商品詳細清單", expanded=False):
                for _, msg in expiry_list: st.markdown(msg)
        else: st.caption("🟢 目前無即期商品")
    with exp_col2:
        if low_stock > 0:
            with st.expander(f"🔍 點擊查看 {low_stock} 筆低庫存詳細清單", expanded=False):
                for msg in low_stock_list: st.markdown(msg)
        else: st.caption("🟢 目前庫存皆在安全水位")

def ai_chat_mode():
    st.subheader("🤖 AI 倉儲助理")
    user_question = st.chat_input("請詢問庫存問題...")
    if user_question:
        doc = connect_spreadsheet()
        if doc:
            df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
            df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
            model = genai.GenerativeModel('gemini-2.5-flash')
            prompt = f"你是餐廳智慧倉儲 AI。目前庫存：\n{df_stock.to_string()}\n出庫紀錄：\n{df_out.to_string()}\n使用者問題：\n{user_question}\n請使用繁體中文回答。"
            response = model.generate_content(prompt)
            st.chat_message("user").write(user_question)
            st.chat_message("assistant").write(response.text)

def ai_purchase_suggestion():
    st.subheader("🧠 AI 採購建議")
    doc = connect_spreadsheet()
    if doc:
        df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
        df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"你是餐廳採購 AI。目前庫存：\n{df_stock.to_string()}\n出庫紀錄：\n{df_out.to_string()}\n請分析哪些商品快缺貨與建議補貨量，繁體中文條列。"
        st.info(model.generate_content(prompt).text)

def extract_number(val):
    if pd.isna(val): return 0.0
    match = re.search(r'[\d.]+', str(val))
    return float(match.group()) if match else 0.0

def extract_unit(val):
    if pd.isna(val): return ""
    match = re.search(r'[^\d.\s]+', str(val))
    return match.group() if match else ""

def get_all_products():
    try:
        doc = connect_spreadsheet()
        if doc:
            sheet = doc.worksheet('工作表1')
            a_column = sheet.col_values(1)
            if len(a_column) > 1:
                return [str(p).strip() for p in a_column[1:] if p and str(p).strip() != ""]
        return []
    except: 
        return []

def log_transaction(sheet_name, product_name, quantity, detail):
    try:
        doc = connect_spreadsheet()
        if doc:
            sheet = doc.worksheet(sheet_name)
            sheet.append_row([datetime.now().strftime('%Y-%m-%d %H:%M:%S'), product_name, quantity, detail])
    except Exception as e: st.error(f"紀錄失敗：{e}")

# =========================================================
# 4. 先進先出 (FIFO) 核心演算法 與 撤回機制
# =========================================================
def process_fifo_outbound(product_name, out_qty, sheet, headers, records):
    batches = []
    for i, rec in enumerate(records):
        if str(rec.get('商品名稱')) == product_name:
            stock = extract_number(rec.get('庫存數量', 0))
            if stock > 0:
                expiry_str = str(rec.get('有效期限', '')).strip() or '2099-12-31'
                batches.append({'row_idx': i + 2, 'stock': float(stock), 'expiry': expiry_str, 'unit': extract_unit(str(rec.get('庫存數量', '')))})

    batches.sort(key=lambda x: x['expiry'])
    if sum(b['stock'] for b in batches) < out_qty:
        return False, f"庫存不足！", []

    updates = []
    remaining = float(out_qty)
    for batch in batches:
        if remaining <= 0: break
        deduct = min(batch['stock'], remaining)
        new_stock = batch['stock'] - deduct
        remaining -= deduct

        stock_str = f"{int(new_stock) if new_stock.is_integer() else new_stock} {batch['unit']}".strip()
        updates.append({"range": f"{gspread.utils.rowcol_to_a1(batch['row_idx'], headers.index('庫存數量') + 1)}", "values": [[stock_str]]})
        if '最後更新時間' in headers:
            updates.append({"range": f"{gspread.utils.rowcol_to_a1(batch['row_idx'], headers.index('最後更新時間') + 1)}", "values": [[datetime.now().strftime('%Y-%m-%d %H:%M:%S')]]})

    return True, "成功", updates

def update_sheet_stock(product_name, quantity, action, expiry=None, detail_info="一般", is_undo=False):
    try:
        doc = connect_spreadsheet()
        if not doc: return
        sheet = doc.worksheet('工作表1')
        headers = sheet.row_values(1)
        records = sheet.get_all_records()
        quantity = float(quantity)

        if action == 'IN':
            import datetime as dt
            today = dt.date.today()
            
            # 定義各類食材的標準保鮮天數 (備用填補機制)
            shelf_life_rules = {
                "鮮奶": 7, "豆漿": 5, "吐司": 5, "漢堡麵包": 7, "蛋餅皮": 20,
                "牛肉排": 30, "火腿片": 14, "培根": 14, "卡拉雞腿排": 30, "熱狗": 30, "雞塊": 30,
                "高麗菜": 5, "洋蔥": 10, "番茄切片": 3, "美生菜": 4, "冰塊": 3, 
                "紅茶葉": 180, "綠茶葉": 180, "咖啡豆": 180, "砂糖": 365, 
                "番茄醬": 90, "美乃滋": 60, "黑胡椒醬": 90, "巧克力醬": 90, "花生醬": 90
            }
            
            # 🚀 修正：如果 expiry 有傳入有效的 YYYY-MM-DD 格式則優先使用；若是 None 或 'None'，才啟動自動填補天數
            if expiry and expiry != 'None' and re.match(r'^\d{4}-\d{2}-\d{2}$', str(expiry).strip()):
                final_expiry = str(expiry).strip()
            else:
                default_days = shelf_life_rules.get(product_name, 7)
                final_expiry = (today + dt.timedelta(days=default_days)).strftime("%Y-%m-%d")
            
            new_row = [""] * len(headers)
            if '商品名稱' in headers: new_row[headers.index('商品名稱')] = product_name
            if '庫存數量' in headers: new_row[headers.index('庫存數量')] = quantity
            if '有效期限' in headers: 
                new_row[headers.index('有效期限')] = final_expiry
            if '最後更新時間' in headers: 
                new_row[headers.index('最後更新時間')] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if 'ID' in headers: 
                new_row[headers.index('ID')] = str(uuid.uuid4())[:8]

            sheet.append_row(new_row)
            log_transaction('進貨紀錄', product_name, quantity, detail_info)
            
            if not is_undo:
                st.success(f"進貨成功：{product_name} +{quantity} (有效期限: {final_expiry})")
                st.session_state.last_transaction = {
                    "action": "IN", "product": product_name, "quantity": quantity, "expiry": final_expiry
                }
                
        elif action in ['OUT', 'WASTE']:
            success, msg, updates = process_fifo_outbound(product_name, quantity, sheet, headers, records)
            if not success:
                st.error(f"{product_name} 扣帳失敗：{msg}")
                return

            sheet.batch_update(updates)
            if action == 'OUT':
                log_transaction('出庫紀錄', product_name, quantity, detail_info)
                if not is_undo:
                    st.warning(f"出庫成功：{product_name} -{quantity}")
                    st.session_state.last_transaction = {"action": "OUT", "product": product_name, "quantity": quantity}
            else:
                log_transaction('報廢紀錄', product_name, quantity, detail_info)
                if not is_undo:
                    st.error(f"報廢成功：{product_name} -{quantity}")
                    st.session_state.last_transaction = {"action": "WASTE", "product": product_name, "quantity": quantity}
        st.cache_data.clear()
    except Exception as e: st.error(f"系統更新失敗：{e}")

def undo_last_transaction():
    if not st.session_state.last_transaction: return
    last = st.session_state.last_transaction
    st.info(f"🔄 正在撤銷：【{last['action']}】 {last['product']} {last['quantity']} 個...")
    if last['action'] == "IN":
        update_sheet_stock(product_name=last['product'], quantity=last['quantity'], action='OUT', detail_info="操作撤回：扣除錯誤進貨", is_undo=True)
    elif last['action'] in ["OUT", "WASTE"]:
        update_sheet_stock(product_name=last['product'], quantity=last['quantity'], action='IN', detail_info="操作撤回：補回錯誤扣帳", is_undo=True)
    st.success("🎉 已成功還原庫存！")
    st.session_state.last_transaction = None
    st.cache_data.clear()
    st.rerun()

def delete_and_undo_specific_record(sheet_name, row_index, product_name, quantity):
    try:
        doc = connect_spreadsheet()
        if not doc: return False

        if sheet_name == '進貨紀錄':
            rollback_action = 'OUT'
            action_text = "撤回進貨"
        elif sheet_name in ['出庫紀錄', '報廢紀錄']:
            rollback_action = 'IN'
            action_text = "撤回消耗"
        else:
            st.error("不支援的撤回工作表類型")
            return False

        sheet_main = doc.worksheet('工作表1')
        headers_main = sheet_main.row_values(1)
        records_main = sheet_main.get_all_records()
        
        if rollback_action == 'IN':
            import datetime as dt
            new_row = [""] * len(headers_main)
            if '商品名稱' in headers_main: new_row[headers_main.index('商品名稱')] = product_name
            if '庫存數量' in headers_main: new_row[headers_main.index('庫存數量')] = float(quantity)
            if '有效期限' in headers_main: new_row[headers_main.index('有效期限')] = (dt.date.today() + dt.timedelta(days=7)).strftime("%Y-%m-%d")
            if 'ID' in headers_main: new_row[headers_main.index('ID')] = f"UD-{str(uuid.uuid4())[:5]}"
            sheet_main.append_row(new_row)
        else:
            success, msg, updates = process_fifo_outbound(product_name, float(quantity), sheet_main, headers_main, records_main)
            if success:
                sheet_main.batch_update(updates)
            else:
                st.error(f"庫存反向扣除失敗（可能庫存已被其他餐點扣光）：{msg}")
                return False

        log_sheet = doc.worksheet(sheet_name)
        log_sheet.delete_rows(row_index)
        
        st.cache_data.clear()
        st.success(f"🎉 成功同步撤回！已從【{sheet_name}】刪除該紀錄，並完成庫存【{action_text}】修正。")
        return True
    except Exception as e:
        st.error(f"精準撤回失敗：{e}")
        return False

# =========================================================
# 5. ⭐ 雙層大腦語意降噪解析演算法 (包含智慧日期相對推算)
# =========================================================
def smart_parse_and_execute(text):
    st.info(f"🧠 語意大腦正在自動過濾環境噪聲與口誤...")
    all_products = get_all_products()
    
    # 🌟 核心升級：在 NLP 提示詞中，加入當前日期，並訓練 AI 認得「相對效期」口訣！
    today_str = datetime.now().strftime("%Y-%m-%d")
    weekday_str = datetime.now().strftime("%A") # 取得今天是星期幾
    
    nlp_prompt = f"""
    You are an advanced voice command assistant for a restaurant kitchen storage. 
    The input text comes from a microphone and may contain speech corrections or environment noise.
    
    Current Date Today is: {today_str} ({weekday_str})

    Valid product list in our database:
    {', '.join(all_products)}

    Your mission:
    1. Identify the core action intent: '進貨', '出庫', or '報廢'. (Default to '進貨' if unclear).
    2. Identify the product name. Correct it to match the closest item in the "Valid product list".
    3. Identify the final corrected numeric quantity.
    4. 🌟 Smart Expiry Calculation: Look at the text to see if the user mentioned any expiry info (e.g., "5天", "保鮮一週", "禮拜五過期", "下週二過期"). 
       Based on Today's date ({today_str}), calculate the EXACT target expiry date in YYYY-MM-DD format.
       - If they say "5天" or "5天過期", add 5 days to today.
       - If they say "禮拜五過期", calculate the date of the UPCOMING Friday.
       - If they say "下週二過期", calculate the date of Tuesday next week.
       - If they do NOT mention any date or expiry info, output 'None'.

    Output ONLY the cleaned standard command in this exact format: [動作] [商品名稱] [最終純數字] [計算出的YYYY-MM-DD日期或None]
    Do NOT output markdown, quotes, formatting or any explanations.

    Example Input 1: "進貨 鮮奶 10瓶 5天"
    Example Output 1: 進貨 鮮奶 10 2026-05-29 (Note: assuming today is 2026-05-24)

    Example Input 2: "請胡 卡拉雞腿排25塊 不對，是45塊"
    Example Output 2: 進貨 卡拉雞腿排 45 None
    """
    
    cleaned_command = ""
    for attempt in range(3):
        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content([nlp_prompt, text])
            cleaned_command = response.text.strip()
            break
        except Exception as e:
            if "429" in str(e): time.sleep(2); continue
            else: st.error(f"AI 降噪失敗: {e}"); return

    if cleaned_command:
        st.success(f"✨ 語意大腦過濾成功 ➡️ 精準還原指令：『{cleaned_command}』")
        
        action = 'IN'
        if '進貨' in cleaned_command: action = 'IN'
        elif '出庫' in cleaned_command: action = 'OUT'
        elif '報廢' in cleaned_command: action = 'WASTE'
        
        try:
            parts = cleaned_command.split()
            if len(parts) >= 3:
                product_name = parts[1].strip()
                quantity = float(parts[2])
                
                # 🌟 讀取 AI 算好的日期（如果有喊的話）
                expiry_val = parts[3].strip() if len(parts) >= 4 else "None"
                
                best_match = process.extractOne(product_name, all_products, scorer=fuzz.ratio)
                if best_match and best_match[1] >= 80:
                    product_name = best_match[0]
                    update_sheet_stock(
                        product_name=product_name, 
                        quantity=quantity, 
                        action=action, 
                        expiry=expiry_val, # 🚀 丟給後台處理
                        detail_info=f"雙層語意助理: {text}"
                    )
                else:
                    st.error(f"🚨 語音輸入失敗：食材【{product_name}】在系統後台完全找不到極度接近的品項！請店長先至 Tab 5 登記新食材與進貨成本。")
            else:
                st.error("系統大腦分析後發現語意結構不完整，請重新宣讀。")
        except Exception as parse_err:
            st.error(f"指令實體解碼失敗: {parse_err}")

# =========================================================
# 6. 前端介面佈局
# =========================================================
st.title("📦 AI 智慧倉儲助手")

if st.session_state.get("last_transaction") is not None:
    last = st.session_state["last_transaction"]
    st.info(f"💡 上一筆更動：【{last['action']}】 {last['product']} {last['quantity']} 個")
    if st.button("↩️ 點我一鍵撤回還原庫存", type="primary", use_container_width=True):
        undo_last_transaction()

show_kpi_dashboard()
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 AI 分析", "📸 單據辨識", "🎙️ 語音", "🕒 紀錄", "🍔 POS出餐"])

# --- TAB1 (AI 分析 - 3 大 AI 強化功能完美回歸版) ---
with tab1:
    st.header("🧠 AI 預測中心")
    ai_col1, ai_col2, ai_col3 = st.columns(3)

    with ai_col1:
        run_prediction = st.button("📈 啟動 AI 銷售與需求預測 (未來7天)", use_container_width=True)
    with ai_col2:
        run_anomaly = st.button("🔍 執行 AI 倉儲異常行為偵測", use_container_width=True)
    with ai_col3:
        run_consultant = st.button("📊 生成 AI 智慧經營決策週報", use_container_width=True)

    try:
        doc = connect_spreadsheet()
        if doc:
            df_stock_raw = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
            df_in_raw = pd.DataFrame(doc.worksheet('進貨紀錄').get_all_records())
            df_out_raw = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
            df_waste_raw = pd.DataFrame(doc.worksheet('報廢紀錄').get_all_records())
        else:
            df_stock_raw = pd.DataFrame()
    except Exception as db_err:
        st.error(f"資料庫讀取失敗：{db_err}")
        df_stock_raw = pd.DataFrame()

    # --- ⏰ 2026年時間定錨關鍵：取得當前最新日期 ---
    current_date_str = datetime.now().strftime("%Y-%m-%d")

    if run_prediction and not df_stock_raw.empty:
        st.markdown("---")
        st.subheader("🔮 未來 7 天物料需求預測與自動採購單")
        if df_out_raw.empty:
            st.info("目前尚無出庫紀錄，系統將自動模擬基本採購模型：")
        
        with st.spinner("AI 正在分析歷史銷售趨勢與耗速模型..."):
            model = genai.GenerativeModel('gemini-2.5-flash')
            # 注入當前正確時間，防止 AI 時空錯亂
            prompt = f"你是餐廳供應鏈專家。目前系統時間為：{current_date_str}。目前庫存：\n{df_stock_raw.to_string()}\n出庫紀錄：\n{df_out_raw.tail(100).to_string()}\n請分析未來7天需求並生成Markdown建議採購表格，繁體中文輸出。"
            try:
                prediction_text = model.generate_content(prompt).text
                st.markdown(prediction_text)
                
                import urllib.parse
                clean_text_for_line = f"【📦 AI 智慧倉儲系統：未來 7 天緊急採購建議單】\n\n{prediction_text[:300]}..."  # 避免網址過長
                encoded_text = urllib.parse.quote(clean_text_for_line)
                line_share_url = f"https://line.me/R/share?text={encoded_text}"
                
                st.markdown("### 📲 採購單外發確認")
                st.link_button("🟢 一鍵發送叫貨明細至 LINE", url=line_share_url, type="primary", use_container_width=True)
                
            except Exception as e: 
                st.error(f"預測生成失敗：{e}")

    if run_anomaly and not df_stock_raw.empty:
        st.markdown("---")
        st.subheader("🕵️‍♂️ 系統自動化稽核與異常偵測告警")
        with st.spinner("安全稽核大腦掃描中..."):
            model = genai.GenerativeModel('gemini-2.5-flash')
            prompt = f"你是餐廳內控專家。目前系統時間為：{current_date_str}。目前庫存：\n{df_stock_raw.to_string()}\n報廢紀錄：\n{df_waste_raw.tail(50).to_string()}\n請找出潛在異常黑洞，繁體中文回答。"
            try:
                st.warning(model.generate_content(prompt).text)
            except Exception as e: st.error(f"偵測失敗：{e}")

    if run_consultant and not df_stock_raw.empty:
        st.markdown("---")
        st.subheader("🏦 餐廳智慧商務經營決策報告")
        with st.spinner("正在結算經營毛利結構..."):
            model = genai.GenerativeModel('gemini-2.5-flash')
            # 注入當前正確時間，確保財務週報產出民國115年/西元2026年的報告
            prompt = f"你是餐飲業財務顧問。目前系統報告日期為：{current_date_str}。食譜：{st.session_state.menu_recipes}\n售價：{st.session_state.meal_prices}\n成本：{st.session_state.ingredient_costs}\n出庫：{df_out_raw.tail(30).to_string()}\n請撰寫高階財務診斷與經營調價建議，繁體中文報告。"
            try:
                st.info(model.generate_content(prompt).text)
            except Exception as e: st.error(f"決策報告生成失敗：{e}")

    st.markdown("---")
    ai_chat_mode()
    st.markdown("---")
    st.header("📊 耗速統計")
    if st.button("開始分析趨勢") and not df_stock_raw.empty:
        try:
            df_stock_copy = df_stock_raw.copy()
            df_stock_copy['庫存數量'] = df_stock_copy['庫存數量'].apply(extract_number)
            df_out_copy = df_out_raw.copy() if not df_out_raw.empty else pd.DataFrame(columns=['商品名稱', '數量', '日期'])
            
            if not df_out_copy.empty:
                df_out_copy['數量'] = df_out_copy['數量'].apply(extract_number)
                df_out_copy['日期'] = pd.to_datetime(df_out_copy['日期'], format='mixed', errors='coerce')

            report = []
            today = datetime.now()

            for _, row in df_stock_copy.iterrows():
                product = row['商品名稱']
                current_stock = row['庫存數量']
                product_out = df_out_copy[df_out_copy['商品名稱'] == product] if not df_out_copy.empty else pd.DataFrame()

                if not product_out.empty and not product_out['日期'].isna().all():
                    days = max(1, (today - product_out['日期'].min()).days)
                    burn_rate = product_out['數量'].sum() / days
                else:
                    burn_rate = 0

                days_remaining = current_stock / burn_rate if burn_rate > 0 else 999
                suggestion = "立即補貨" if current_stock <= SAFE_STOCK_LEVEL else ("即將缺貨" if days_remaining <= 3 else "安全")

                report.append({
                    "商品": product, "庫存": current_stock, "日耗": round(burn_rate, 2),
                    "剩餘天數": int(days_remaining) if days_remaining != 999 else "-", "建議": suggestion
                })

            df_report = pd.DataFrame(report)
            
            # 1. 顯示表格 (確保開啟自動適應寬度)
            st.dataframe(df_report, use_container_width=True)
            
# ==================== 🛠️ 核心優化：改用橫向 Plotly 繪製完美圖表 ====================
            import plotly.express as px
            
            # 💡 關鍵變革：將 x 設為"庫存"，y 設為"商品"，一鍵切換為最適合多品項閱讀的「橫向長條圖」！
            fig = px.bar(
                df_report, 
                x="庫存", 
                y="商品", 
                orientation="h",  # 👈 強制設定為橫向圖表
                title="📊 各品項當前庫存水位即時統計圖（由大至小排序）",
                labels={"庫存": "當前庫存數量", "商品": "食材物料名稱"},
                text="庫存"       # 在長條圖右側直接顯示數字
            )
            
            # 動態計算高度：根據你目前後台食材的總數量，自動調整圖表長度，防止39種食材擠在一起
            dynamic_height = max(500, len(df_report) * 25)
            
            # 優化圖表外觀設定
            fig.update_layout(
                yaxis={'categoryorder':'total ascending'},  # 💡 自動排序：庫存最多的排最上面，一目了然
                xaxis_title="當前庫存數量",
                yaxis_title="食材物料名稱",
                margin=dict(l=150, r=50, t=50, b=50),      # 💡 左側留白拉大到 150，確保「卡拉雞腿排」等長品名文字絕對不會被切到
                height=dynamic_height,                      # 注入動態自適應高度
                template="plotly_dark"                      # 完美融入你的黑色高質感 Streamlit 主題
            )
            
            # 調整長條圖數字顯示位置：放在柱子外側右方，字體變粗方便點收
            fig.update_traces(
                texttemplate='%{text}', 
                textposition='outside',
                marker_color='#2A9D8F' # 💡 改用專業的高質感莫蘭迪綠色，比原本的預設藍色更具商業系統架構感
            )
            
            # 將完美的圖表渲染至網頁，並啟用 100% 容器寬度自我調適，防止右側被切掉
            st.plotly_chart(fig, use_container_width=True)
            # ============================================================================
            
        except Exception as e: 
            st.error(e)
# --- TAB2 (AI OCR) ---
with tab2:
    st.header("📸 單據辨識")
    uploaded = st.file_uploader("上傳單據", type=['jpg', 'jpeg', 'png'])
    if uploaded:
        st.image(uploaded)
        if st.button("開始辨識"):
            try:
                img = Image.open(uploaded)
                model = genai.GenerativeModel('gemini-2.5-flash')
                prompt = '辨識商品與數量，僅輸出 JSON array 格式: [{"product":"高麗菜", "quantity":3}]'
                response = model.generate_content([img, prompt])
                json_match = re.search(r'\[.*\]', response.text, re.S)
                if json_match:
                    items = json.loads(json_match.group())
                    for item in items: update_sheet_stock(item['product'], item['quantity'], 'IN', detail_info='AI OCR')
                    st.balloons()
            except Exception as e: st.error(e)

# --- TAB3 (🎙️ 語音助理) ---
with tab3:
    st.header("🎙️ 語音輸入")
    st.write("💡 錄音完成並按下停止後，系統將自動進行雙層大腦校正並安全入庫。")
    st.write("💡 支援指定相對有效期限，例如：「進貨 鮮奶 10瓶 5天」或「進貨 吐司 3包 禮拜五過期」")
    audio_file = st.audio_input("錄音控制台")

    if audio_file:
        current_audio_bytes = audio_file.getvalue()
        if st.session_state.get("last_processed_audio") == current_audio_bytes:
            st.caption("✨ 本次語音已執行完畢。")
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(current_audio_bytes)
                tmp_path = tmp.name

            try:
                with st.spinner("🎵 語音傳輸中，正在進行精準 STT 字詞轉錄..."):
                    audio_upload = genai.upload_file(path=tmp_path)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    all_products = get_all_products()
                    prompt = "請將這段錄音原封不動地轉錄為繁體中文，修正明顯發音錯字即可。絕對不要自己加上額外的商品提示、說明或備註，只輸出轉錄後的最終純文字句子。"                    
                    response = model.generate_content([audio_upload, prompt])
                    spoken_text = response.text.strip()
                
                if spoken_text:
                    st.success(f"🎙️ 語音轉錄結果：{spoken_text}")
                    smart_parse_and_execute(spoken_text)
                    st.session_state.last_processed_audio = current_audio_bytes
            except Exception as e: st.error(f"語音處理失敗: {e}")
            finally:
                try: os.remove(tmp_path)
                except: pass

# --- TAB4 (歷史紀錄精準撤回版) ---
with tab4:
    st.header("🕒 歷史變更紀錄")
    record_type = st.selectbox("請選擇要管理的紀錄看板：", ["📥 進貨明細管理", "📤 出庫明細管理", "🗑️ 報廢明細管理"])
    
    target_sheet = "進貨紀錄"
    if "出庫" in record_type: target_sheet = "出庫紀錄"
    elif "報廢" in record_type: target_sheet = "報廢紀錄"

    try:
        doc = connect_spreadsheet()
        if doc:
            log_sheet = doc.worksheet(target_sheet)
            raw_data = log_sheet.get_all_records()
            
            if not raw_data:
                st.info(f"目前【{target_sheet}】尚無任何歷史數據。")
            else:
                h_col1, h_col2, h_col3, h_col4, h_col5 = st.columns([2, 2, 1, 3, 1.5])
                with h_col1: st.markdown("**變更日期**")
                with h_col2: st.markdown("**商品名稱**")
                with h_col3: st.markdown("**變更數量**")
                with h_col4: st.markdown("**備註說明**")
                with h_col5: st.markdown("**安全性操作**")
                st.markdown("---")
                
                for idx, row in reversed(list(enumerate(raw_data))):
                    actual_row_in_sheet = idx + 2
                    r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns([2, 2, 1, 3, 1.5])
                    
                    with r_col1: st.write(row.get("日期", row.get("時間", "-")))
                    with r_col2: st.write(f"**{row.get('商品名稱', '-')}**")
                    with r_col3: st.write(str(row.get("數量", row.get("數量(片/個)", "-"))))
                    with r_col4: st.write(row.get("備註", row.get("備註說明", "一般語音/系統")))
                    
                    with r_col5:
                        btn_key = f"undo_{target_sheet}_{actual_row_in_sheet}_{idx}"
                        if st.button("🗑️ 撤回", key=btn_key, use_container_width=True):
                            p_name = row.get('商品名稱')
                            try:
                                # 為了精準防禦，將數量轉為純數字計算
                                qty = float(extract_number(row.get('數量', row.get('數量(片/個)', 0))))
                            except:
                                qty = 0.0

                            with st.spinner("正在執行還原..."):
                                # 🚨 【高容錯撤回防禦機制】
                                is_safe_to_undo = True
                                
                                if target_sheet == "進貨紀錄":
                                    # 1. 即時重新撈取目前的最新庫存總表
                                    try:
                                        df_current_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
                                        df_current_stock['庫存數量'] = df_current_stock['庫存數量'].apply(extract_number)
                                        
                                        # 2. 抓出這個品項在後台的當前實體庫存總數
                                        match_stock = df_current_stock[df_current_stock['商品名稱'] == p_name]
                                        current_qty = float(match_stock['庫存數量'].sum()) if not match_stock.empty else 0.0
                                        
                                        # 3. 攔截：如果發現目前後台庫存不足以被倒扣（例如你手動去Sheets砍了資料，或被別的餐點FIFO吃光了）
                                        if current_qty < qty:
                                            st.warning(f"⚠️ 偵測到後台庫存已變動或被手動移除，目前【{p_name}】帳面剩餘 {current_qty}，不足以執行反向扣除。")
                                            st.info("🔄 系統啟動防禦機制：免除庫存反向追溯，直接強制抹除此筆歷史紀錄。")
                                            
                                            # 強制至後台刪除該行歷史紀錄，不呼叫會報錯的還原扣庫函式
                                            log_sheet.delete_rows(actual_row_in_sheet)
                                            is_safe_to_undo = False
                                            time.sleep(1)
                                            st.rerun()
                                    except Exception as check_err:
                                        st.error(f"安全性檢查失敗，維持原程序執行: {check_err}")

                                # 4. 數據無異常、或是加法回補（出庫/報廢），則安全呼叫原本的還原機制
                                if is_safe_to_undo:
                                    if delete_and_undo_specific_record(target_sheet, actual_row_in_sheet, p_name, row.get('數量', row.get('數量(片/個)', 0))):
                                        time.sleep(1)
                                        st.rerun()
                                        
                    st.markdown("<hr style='margin:2px 0px; opacity:0.3;'>", unsafe_allow_html=True)
    except Exception as log_err: st.error(f"讀取失敗：{log_err}")
# --- TAB5 (POS 出餐) ---
with tab5:
    st.header("🍔 POS 前台出餐與後台管理")
    setup_col, pos_col = st.columns([1, 1.2])
    
    with setup_col:
        st.subheader("⚙️ 後台管理中心")
        manage_tab1, manage_tab2, manage_tab3 = st.tabs(["➕ 新增餐點", "✏️ 編輯/刪除", "💰 成本管理"])
        all_products_list = get_all_products()
        
        with manage_tab1:
            new_meal_name = st.text_input("餐點名稱", placeholder="培根蛋吐司")
            new_meal_price = st.number_input("販售售價 (元)", min_value=0.0, value=100.0, step=5.0)
            selected_ings = st.multiselect("消耗原料", options=all_products_list)
            new_recipe = {}
            if selected_ings:
                for ing in selected_ings: 
                    new_recipe[ing] = st.number_input(f"每份消耗【{ing}】數量：", min_value=0.01, value=1.0, step=0.1, key=f"add_{ing}")
            if st.button("💾 儲存餐點配方", use_container_width=True):
                if new_meal_name.strip() and new_recipe:
                    st.session_state.menu_recipes[new_meal_name.strip()] = new_recipe
                    st.session_state.meal_prices[new_meal_name.strip()] = float(new_meal_price)
                    st.success("🎉 成功新增餐點！")
                    st.rerun()
                    
        with manage_tab2:
            if st.session_state.menu_recipes:
                edit_meal_target = st.selectbox("選擇管理餐點", options=list(st.session_state.menu_recipes.keys()))
                current_recipe = st.session_state.menu_recipes[edit_meal_target]
                edit_meal_price = st.number_input("調整售價 (元)", min_value=0.0, value=float(st.session_state.meal_prices.get(edit_meal_target, 0.0)))
                safe_options = sorted(list(set(all_products_list + list(current_recipe.keys()))))
                edit_selected_ings = st.multiselect("調整原料", options=safe_options, default=list(current_recipe.keys()))
                updated_recipe = {}
                if edit_selected_ings:
                    for ing in edit_selected_ings: 
                        updated_recipe[ing] = st.number_input(f"消耗【{ing}】數量：", min_value=0.01, value=float(current_recipe.get(ing, 1.0)), key=f"edit_{ing}")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("💾 更新餐點", use_container_width=True, type="primary"):
                        st.session_state.menu_recipes[edit_meal_target] = updated_recipe
                        st.session_state.meal_prices[edit_meal_target] = float(edit_meal_price)
                        st.success("⚙️ 修改成功！")
                        st.rerun()
                with c2:
                    if st.button("❌ 刪除餐點", use_container_width=True):
                        del st.session_state.menu_recipes[edit_meal_target]
                        del st.session_state.meal_prices[edit_meal_target]
                        st.rerun()
                        
        with manage_tab3:
            cost_mode = st.radio("操作模式", ["修改現有原料單價", "新增原料單價"], horizontal=True)
            if cost_mode == "修改現有原料單價" and st.session_state.ingredient_costs:
                target_ing = st.selectbox("選擇原料", options=list(st.session_state.ingredient_costs.keys()))
                new_ing_cost = st.number_input("單位成本 (元)", min_value=0.0, value=float(st.session_state.ingredient_costs[target_ing]))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("💾 儲存修改"): 
                        st.session_state.ingredient_costs[target_ing] = float(new_ing_cost)
                        st.rerun()
                with c2:
                    if st.button("🗑️ 刪除紀錄"): 
                        del st.session_state.ingredient_costs[target_ing]
                        st.rerun()
            else:
                custom_ing_name = st.text_input("新原料名稱")
                custom_ing_cost = st.number_input("設定單位成本 (元)", min_value=0.0, value=10.0)
                if st.button("➕ 新增原料成本"):
                    if custom_ing_name.strip(): 
                        st.session_state.ingredient_costs[custom_ing_name.strip()] = float(custom_ing_cost)
                        st.rerun()
            st.json(st.session_state.ingredient_costs)

    with pos_col:
        st.subheader("🛒 前台一鍵出餐 (自動連動 FIFO)")
        current_menu = st.session_state.menu_recipes
        grid_cols = st.columns(2)
        for idx, (meal_name, ingredients) in enumerate(current_menu.items()):
            with grid_cols[idx % 2]:
                meal_price_show = st.session_state.meal_prices.get(meal_name, 0.0)
                st.markdown(f"**{meal_name}** — 💰售價: `${meal_price_show}` 元")
                st.caption(f"配方：" + " / ".join([f"{k}:{v}" for k, v in ingredients.items()]))
                if st.button("🛒 賣出一份", key=f"pos_btn_{meal_name}", use_container_width=True):
                    try:
                        records = fetch_sheet_data_cached('工作表1')
                        total_stock_map = {}
                        for rec in records: 
                            total_stock_map[str(rec.get('商品名稱'))] = total_stock_map.get(str(rec.get('商品名稱')), 0.0) + float(extract_number(rec.get('庫存數量', 0)))
                    except: continue
                    insufficient = [f"❌ 【{k}】還差 {v - total_stock_map.get(k, 0.0)} 個" for k, v in ingredients.items() if total_stock_map.get(k, 0.0) < v]
                    if insufficient: st.error(f"🚨 原料不足：{', '.join(insufficient)}")
                    else:
                        for item_name, qty in ingredients.items(): 
                            update_sheet_stock(item_name, qty, 'OUT', detail_info=f"POS出餐：{meal_name}")
                        price = st.session_state.meal_prices.get(meal_name, 0.0)
                        cost = sum(qty * st.session_state.ingredient_costs.get(ing_name, 0.0) for ing_name, qty in ingredients.items())
                        st.success(f"✅ {meal_name} 出餐成功！")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("售價", f"${price}")
                        c2.metric("成本", f"${round(cost, 1)}")
                        c3.metric("本單毛利", f"${round(price - cost, 1)}")
                        st.balloons()
