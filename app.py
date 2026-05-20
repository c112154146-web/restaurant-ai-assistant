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

from datetime import datetime
from PIL import Image
from rapidfuzz import process, fuzz
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(
    page_title="AI 智慧倉儲系統",
    page_icon="📦",
    layout="wide"
)

genai.configure(
    api_key=st.secrets["GEMINI_API_KEY"]
)

SAFE_STOCK_LEVEL = 5

# =========================================================
# 1. 初始化動態狀態
# =========================================================
if "menu_recipes" not in st.session_state:
    st.session_state.menu_recipes = {
        "🍔 經典牛肉漢堡": {"漢堡麵包": 1.0, "牛肉串": 1.0, "高麗菜": 0.1},
        "🥪 總匯三明治": {"吐司": 3.0, "雞蛋": 1.0, "火腿": 1.0},
        "🍳 起司蛋餅": {"蛋餅皮": 1.0, "雞蛋": 1.0, "起司片": 1.0}
    }

if "meal_prices" not in st.session_state:
    st.session_state.meal_prices = {
        "🍔 經典牛肉漢堡": 120.0,
        "🥪 總匯三明治": 85.0,
        "🍳 起司蛋餅": 55.0
    }

if "ingredient_costs" not in st.session_state:
    st.session_state.ingredient_costs = {
        "漢堡麵包": 15.0,
        "牛肉串": 45.0,
        "高麗菜": 20.0,
        "吐司": 5.0,
        "雞蛋": 7.0,
        "火腿": 12.0,
        "蛋餅皮": 10.0,
        "起司片": 8.0
    }

if "last_transaction" not in st.session_state:
    st.session_state.last_transaction = None

if "last_processed_audio" not in st.session_state:
    st.session_state.last_processed_audio = None

# =========================================================
# 2. Google Sheets 連線
# =========================================================
@st.cache_resource
# ✅ 正確的安全連線版本
@st.cache_resource
def connect_spreadsheet():
    creds_dict = json.loads(st.secrets["gcp_service_account"]["credentials"])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 🌟 核心關鍵：必須完完整整地 return 這行，doc 才有辦法讀取工作表！
    return client.open('智慧庫存系統')
    
@st.cache_data(ttl=60)
def fetch_sheet_data_cached(sheet_name):
    doc = connect_spreadsheet()
    return doc.worksheet(sheet_name).get_all_records()

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
    df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
    df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"你是餐廳採購 AI。目前庫存：\n{df_stock.to_string()}\n出庫紀錄：\n{df_out.to_string()}\n請分析哪些商品快缺貨與建議補貨量，繁體中文條列。"
    st.info(model.generate_content(prompt).text)

def extract_number(val):
    if pd.isna(val): return 0.0
    match = re.search(r'[\d\.]+', str(val))
    return float(match.group()) if match else 0.0

def extract_unit(val):
    if pd.isna(val): return ""
    match = re.search(r'[^\d\.\s]+', str(val))
    return match.group() if match else ""

def get_all_products():
    try:
        sheet = connect_spreadsheet().worksheet('工作表1')
        return [str(r.get('商品名稱', '')).strip() for r in sheet.get_all_records() if str(r.get('商品名稱', '')).strip()]
    except: return []

def log_transaction(sheet_name, product_name, quantity, detail):
    try:
        sheet = connect_spreadsheet().worksheet(sheet_name)
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
        sheet = doc.worksheet('工作表1')
        headers = sheet.row_values(1)
        records = sheet.get_all_records()
        quantity = float(quantity)

        if action == 'IN':
            new_row = [""] * len(headers)
            if '商品名稱' in headers: new_row[headers.index('商品名稱')] = product_name
            if '庫存數量' in headers: new_row[headers.index('庫存數量')] = quantity
            if '有效期限' in headers: new_row[headers.index('有效期限')] = expiry or ""
            if '最後更新時間' in headers: new_row[headers.index('最後更新時間')] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if 'ID' in headers: new_row[headers.index('ID')] = str(uuid.uuid4())[:8]

            sheet.append_row(new_row)
            log_transaction('進貨紀錄', product_name, quantity, detail_info)
            if not is_undo:
                st.success(f"進貨成功：{product_name} +{quantity}")
                st.session_state.last_transaction = {"action": "IN", "product": product_name, "quantity": quantity, "expiry": expiry}

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

# =========================================================
# 5. ⭐ 經典常規關鍵字解析演算法 (Regex & 繁體中文優化)
# =========================================================
def smart_parse_and_execute(text):
    text = text.strip()
    action = None

    in_kw = ['進貨', '新增', '補貨', '入庫', '買了']
    out_kw = ['使用', '用了', '消耗', '出餐', '銷貨', '賣出', '賣了', '扣掉']
    waste_kw = ['報廢', '壞掉', '過期', '爛掉', '丟掉', '破掉']

    for k in in_kw:
        if k in text: action = 'IN'; text = text.replace(k, '', 1); break
    if not action:
        for k in out_kw:
            if k in text: action = 'OUT'; text = text.replace(k, '', 1); break
    if not action:
        for k in waste_kw:
            if k in text: action = 'WASTE'; text = text.replace(k, '', 1); break

    # 💡 廚房防呆核心規則：完全沒有提到明確動詞，100% 預設為進貨行為
    if not action:
        action = 'IN'

    # 提取數量
    qty = 1.0
    qty_match = re.search(r'([0-9一二三四五六七八九十百千兩\.]+)', text)
    if qty_match:
        num_str = qty_match.group(1)
        try:
            if num_str.replace('.', '', 1).isdigit(): qty = float(num_str)
            else: qty = float(cn2an.cn2an(num_str))
            text = text.replace(num_str, '', 1)
        except: qty = 1.0

    # 清理常用單位
    product = re.sub(r'[個包箱公斤斤克瓶顆件把台條乘加片]', '', text).strip()
    
    # 模糊品項比對
    all_products = get_all_products()
    if all_products:
        best_match = process.extractOne(product, all_products, scorer=fuzz.partial_ratio)
        if best_match and best_match[1] >= 75:
            if best_match[0] != product:
                st.info(f"🔍 智慧模糊修正：{product} ➡️ {best_match[0]}")
            product = best_match[0]

    if product:
        update_sheet_stock(product_name=product, quantity=qty, action=action, detail_info=f"語音解析：{text}")
    else:
        st.error("無法正確提取到商品名稱，請再試一次。")

# =========================================================
# 6. 前端介面佈局
# =========================================================
st.title("📦 AI 智慧倉儲系統")

if st.session_state.last_transaction:
    st.info(f"💡 上一筆更動：【{st.session_state.last_transaction['action']}】 {st.session_state.last_transaction['product']} {st.session_state.last_transaction['quantity']} 個")
    if st.button("↩️ 點我一鍵撤回還原庫存", type="primary", use_container_width=True):
        undo_last_transaction()

show_kpi_dashboard()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 AI 分析", "📸 OCR", "🎙️ 語音", "🕒 紀錄", "🍔 POS出餐"])

# --- TAB1 (AI 分析) ---
with tab1:
    if st.button("🧠 產出 AI 採購建議"): ai_purchase_suggestion()
    st.markdown("---")
    ai_chat_mode()
    st.markdown("---")
    st.header("📊 歷史趨勢與耗速統計")
    if st.button("開始分析趨勢"):
        try:
            doc = connect_spreadsheet()
            df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
            df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
            if not df_stock.empty: df_stock['庫存數量'] = df_stock['庫存數量'].apply(extract_number)
            if not df_out.empty:
                df_out['數量'] = df_out['數量'].apply(extract_number)
                df_out['日期'] = pd.to_datetime(df_out['日期'], format='mixed', errors='coerce')
            report = []
            today = datetime.now()
            for _, row in df_stock.iterrows():
                product = row['商品名稱']
                current_stock = row['庫存數量']
                product_out = df_out[df_out['商品名稱'] == product] if not df_out.empty else pd.DataFrame()
                burn_rate = product_out['數量'].sum() / max(1, (today - product_out['日期'].min()).days) if not product_out.empty else 0
                days_remaining = current_stock / burn_rate if burn_rate > 0 else 999
                suggestion = "立即補貨" if current_stock <= SAFE_STOCK_LEVEL else ("即將缺貨" if days_remaining <= 3 else "安全")
                report.append({"商品": product, "庫存": current_stock, "日耗": round(burn_rate, 2), "剩餘天數": int(days_remaining) if days_remaining != 999 else "-", "建議": suggestion})
            df_report = pd.DataFrame(report)
            st.dataframe(df_report, use_container_width=True)
            st.bar_chart(df_report.set_index("商品")[["庫存"]])
        except Exception as e: st.error(e)

# --- TAB2 (AI OCR) ---
with tab2:
    st.header("📸 AI OCR 單據辨識")
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

# --- TAB3 (🎙️ 語音助理 - 結束錄音即自動執行) ---
with tab3:
    st.header("🎙️ AI 語音助理")
    st.write("💡 錄音完成並按下停止後，系統將採用穩定的常規比對演算法進行自動扣帳與進貨。")
    
    audio_file = st.audio_input("錄音控制台")

    if audio_file:
        current_audio_bytes = audio_file.getvalue()
        
        # 🔒 防重複消耗免費額度
        if st.session_state.last_processed_audio == current_audio_bytes:
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
                    # 💡 這邊只讓 Gemini 單純做語音轉文字（STT），絕不進行任何邏輯腦補與擴充！
                    prompt = f"你現在是語音轉文字機器。請將這段錄音原封不動地轉錄為繁體中文，修正錯別字。商品可能包含：{','.join(all_products)}。絕對不要自己加上額外的商品提示或說明，只輸出轉錄後的最終純文字。"
                    
                    response = model.generate_content([audio_upload, prompt])
                    spoken_text = response.text.strip()
                
                if spoken_text:
                    st.success(f"🎙️ 語音轉錄結果：{spoken_text}")
                    # 🚀 將乾淨的文字丟給我們最穩定的常規 Regex 演算法去跑
                    smart_parse_and_execute(spoken_text)
                    st.session_state.last_processed_audio = current_audio_bytes
                    
            except Exception as e:
                st.error(f"語音處理失敗: {e}")
            finally:
                try: os.remove(tmp_path)
                except: pass

# --- TAB4 (歷史紀錄) ---
with tab4:
    st.header("🕒 最新紀錄")
    try:
        dfs = []
        mapping = {'進貨紀錄': '📦 進貨', '出庫紀錄': '📤 出庫', '報廢紀錄': '🗑️ 報廢'}
        for sheet_name, action_name in mapping.items():
            try:
                data_records = fetch_sheet_data_cached(sheet_name)
                if data_records:
                    df_single = pd.DataFrame(data_records)
                    if not df_single.empty:
                        df_single['動作'] = action_name
                        dfs.append(df_single)
            except: continue
        if dfs:
            df_history_wall = pd.concat(dfs)
            time_col = '日期' if '日期' in df_history_wall.columns else ('最後更新時間' if '最後更新時間' in df_history_wall.columns else None)
            if time_col:
                df_history_wall[time_col] = pd.to_datetime(df_history_wall[time_col], errors='coerce')
                df_history_wall = df_history_wall.sort_values(by=time_col, ascending=False)
            st.dataframe(df_history_wall.head(20), use_container_width=True)
        else: st.info("尚無歷史紀錄")
        st.markdown("---")
        df_stock_final = pd.DataFrame(fetch_sheet_data_cached('工作表1'))
        if not df_stock_final.empty:
            csv_data = df_stock_final.to_csv(index=False).encode('utf-8-sig')
            st.download_button(label="下載最新庫存總表 (CSV檔)", data=csv_data, file_name=f"餐廳_庫存總表_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
    except Exception as e: st.error(f"紀錄分頁載入失敗：{e}")

# --- TAB5 (POS 出餐) ---
with tab5:
    st.header("🍔 POS 前台出餐與動態後台管理")
    setup_col, pos_col = st.columns([1, 1.2])
    with setup_col:
        st.subheader("⚙️ 系統後台管理中心")
        manage_tab1, manage_tab2, manage_tab3 = st.tabs(["➕ 新增餐點", "✏️ 編輯/刪除餐點", "💰 原料成本管理"])
        with manage_tab1:
            new_meal_name = st.text_input("1. 輸入新餐點名稱", placeholder="例如：培根蛋吐司")
            new_meal_price = st.number_input("2. 設定此餐點販售售價 (元)", min_value=0.0, value=100.0, step=5.0)
            selected_ings = st.multiselect("3. 選擇這道餐點會消耗哪些原料", options=available_ingredients if 'available_ingredients' in locals() else sorted(list(set(get_all_products()))))
            new_recipe = {}
            if selected_ings:
                for ing in selected_ings: new_recipe[ing] = st.number_input(f"每份消耗【{ing}】數量：", min_value=0.01, value=1.0, step=0.1, key=f"add_qty_{ing}")
            if st.button("💾 儲存新餐點配方與售價", use_container_width=True):
                if new_meal_name.strip() and new_recipe:
                    st.session_state.menu_recipes[new_meal_name.strip()] = new_recipe
                    st.session_state.meal_prices[new_meal_name.strip()] = float(new_meal_price)
                    st.success("🎉 成功新增餐點！")
                    st.rerun()
        with manage_tab2:
            if st.session_state.menu_recipes:
                edit_meal_target = st.selectbox("選擇要管理的餐點", options=list(st.session_state.menu_recipes.keys()))
                current_recipe = st.session_state.menu_recipes[edit_meal_target]
                edit_meal_price = st.number_input("調整販售售價 (元)", min_value=0.0, value=float(st.session_state.meal_prices.get(edit_meal_target, 0.0)))
                safe_options = sorted(list(set(get_all_products() + list(current_recipe.keys()))))
                edit_selected_ings = st.multiselect("調整原料品項", options=safe_options, default=list(current_recipe.keys()), key=f"edit_ings_{edit_meal_target}")
                updated_recipe = {}
                if edit_selected_ings:
                    for ing in edit_selected_ings: updated_recipe[ing] = st.number_input(f"每份消耗【{ing}】數量：", min_value=0.01, value=float(current_recipe.get(ing, 1.0)), key=f"edit_qty_{edit_meal_target}_{ing}")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("💾 更新配方與售價", use_container_width=True, type="primary"):
                        st.session_state.menu_recipes[edit_meal_target] = updated_recipe
                        st.session_state.meal_prices[edit_meal_target] = float(edit_meal_price)
                        st.success("⚙️ 修改成功！"); st.rerun()
                with c2:
                    if st.button("❌ 刪除此餐點", use_container_width=True):
                        del st.session_state.menu_recipes[edit_meal_target]; del st.session_state.meal_prices[edit_meal_target]; st.rerun()
        with manage_tab3:
            cost_mode = st.radio("操作模式", ["修改現有原料單價", "新增未列出原料之單價"], horizontal=True)
            if cost_mode == "修改現有原料單價" and st.session_state.ingredient_costs:
                target_ing = st.selectbox("選擇原料品項", options=list(st.session_state.ingredient_costs.keys()))
                new_ing_cost = st.number_input("單位進貨成本 (元)", min_value=0.0, value=float(st.session_state.ingredient_costs[target_ing]))
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("💾 儲存單價修改"): st.session_state.ingredient_costs[target_ing] = float(new_ing_cost); st.rerun()
                with c2:
                    if st.button("🗑️ 刪除成本紀錄"): del st.session_state.ingredient_costs[target_ing]; st.rerun()
            else:
                custom_ing_name = st.text_input("輸入新原料名稱")
                custom_ing_cost = st.number_input("設定單位進貨成本 (元)", min_value=0.0, value=10.0)
                if st.button("➕ 新增原料成本單價"):
                    if custom_ing_name.strip(): st.session_state.ingredient_costs[custom_ing_name.strip()] = float(custom_ing_cost); st.rerun()
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
                        for rec in records: total_stock_map[str(rec.get('商品名稱'))] = total_stock_map.get(str(rec.get('商品名稱')), 0.0) + float(extract_number(rec.get('庫存數量', 0)))
                    except: continue
                    insufficient = [f"❌ 【{k}】還差 {v - total_stock_map.get(k, 0.0)} 個" for k, v in ingredients.items() if total_stock_map.get(k, 0.0) < v]
                    if insufficient: st.error(f"🚨 原料不足：{', '.join(insufficient)}")
                    else:
                        for item_name, qty in ingredients.items(): update_sheet_stock(item_name, qty, 'OUT', detail_info=f"POS出餐：{meal_name}")
                        price = st.session_state.meal_prices.get(meal_name, 0.0)
                        cost = sum(qty * st.session_state.ingredient_costs.get(ing_name, 0.0) for ing_name, qty in ingredients.items())
                        st.success(f"✅ {meal_name} 出餐成功！")
                        c1, c2, c3 = st.columns(3)
                        c1.metric("售價", f"${price}")
                        c2.metric("成本", f"${round(cost, 1)}")
                        c3.metric("本單毛利", f"${round(price - cost, 1)}")
                        st.balloons()
