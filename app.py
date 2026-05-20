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
# 1. 初始化動態狀態（餐點食譜、售價、原料成本資料庫）
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

# =========================================================
# 2. Google Sheets 連線與快取機制
# =========================================================
@st.cache_resource
def connect_spreadsheet():
    creds_dict = json.loads(
        st.secrets["gcp_service_account"]["credentials"]
    )
    scope = [
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict,
        scope
    )
    client = gspread.authorize(creds)
    return client.open('智慧庫存系統')
    
@st.cache_data(ttl=60)
def fetch_sheet_data_cached(sheet_name):
    doc = connect_spreadsheet()
    return doc.worksheet(sheet_name).get_all_records()

# =========================================================
# 3. 工具函式
# =========================================================
def show_kpi_dashboard():
    df_stock = pd.DataFrame(fetch_sheet_data_cached('工作表1'))
    df_in = pd.DataFrame(fetch_sheet_data_cached('進貨紀錄'))
    df_waste = pd.DataFrame(fetch_sheet_data_cached('報廢紀錄'))

    today = datetime.now().strftime('%Y-%m-%d')
    today_in = 0
    today_waste = 0

    if not df_in.empty:
        today_in = len(df_in[df_in['日期'].astype(str).str.contains(today)])

    if not df_waste.empty:
        today_waste = len(df_waste[df_waste['日期'].astype(str).str.contains(today)])

    low_stock = 0
    expiry_count = 0
    
    # 建立用來存放大力告急商品的清單
    low_stock_list = []
    expiry_list = []

    for _, row in df_stock.iterrows():
        product = row.get('商品名稱', '')
        stock = extract_number(row.get('庫存數量', 0))
        unit = extract_unit(str(row.get('庫存數量', '')))

        # 統計低庫存
        if stock <= SAFE_STOCK_LEVEL:
            low_stock += 1
            low_stock_list.append(f"🚨 【{product}】庫存吃緊！目前僅剩 {stock} {unit} (安全水位: {SAFE_STOCK_LEVEL})")

        # 統計即期品
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
            except:
                pass

    # 依照到期天數排序，最急的排最上面
    expiry_list.sort(key=lambda x: x[0])

    # 1. 畫出第一層：4 個 KPI 數據大方塊
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📦 今日進貨", today_in)
    col2.metric("🗑️ 今日報廢", today_waste)
    col3.metric("⚠️ 即期商品", expiry_count)
    col4.metric("🚨 低庫存", low_stock)

    # 2. 畫出第二層：專屬下拉式警報面板 (直接連動上方數據)
    exp_col1, exp_col2 = st.columns(2)
    
    with exp_col1:
        if expiry_count > 0:
            with st.expander(f"🔍 點擊查看 {expiry_count} 筆即期商品詳細清單", expanded=False):
                for _, msg in expiry_list:
                    st.markdown(msg)
        else:
            st.caption("🟢 目前無即期商品")

    with exp_col2:
        if low_stock > 0:
            with st.expander(f"🔍 點擊查看 {low_stock} 筆低庫存詳細清單", expanded=False):
                for msg in low_stock_list:
                    st.markdown(msg)
        else:
            st.caption("🟢 目前庫存皆在安全水位")

def ai_chat_mode():
    st.subheader("🤖 AI 倉儲助理")
    user_question = st.chat_input("請詢問庫存問題...")

    if user_question:
        doc = connect_spreadsheet()
        df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
        df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())

        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
你是餐廳智慧倉儲 AI。
目前庫存：
{df_stock.to_string()}
出庫紀錄：
{df_out.to_string()}
使用者問題：
{user_question}
請使用繁體中文回答。
"""
        response = model.generate_content(prompt)
        st.chat_message("user").write(user_question)
        st.chat_message("assistant").write(response.text)

def ai_purchase_suggestion():
    st.subheader("🧠 AI 採購建議")
    doc = connect_spreadsheet()
    df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
    df_out = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())

    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = f"""
你是餐廳採購 AI。
目前庫存：
{df_stock.to_string()}
出庫紀錄：
{df_out.to_string()}
請分析：
1. 哪些商品快缺貨
2. 哪些消耗最快
3. 建議補貨量
請使用條列式繁體中文。
"""
    response = model.generate_content(prompt)
    st.info(response.text)

def extract_number(val):
    if pd.isna(val):
        return 0.0
    match = re.search(r'[\d\.]+', str(val))
    return float(match.group()) if match else 0.0

def extract_unit(val):
    if pd.isna(val):
        return ""
    match = re.search(r'[^\d\.\s]+', str(val))
    return match.group() if match else ""

def get_all_products():
    try:
        sheet = connect_spreadsheet().worksheet('工作表1')
        records = sheet.get_all_records()
        return [
            str(r.get('商品名稱', '')).strip()
            for r in records
            if str(r.get('商品名稱', '')).strip()
        ]
    except:
        return []

def log_transaction(sheet_name, product_name, quantity, detail):
    try:
        sheet = connect_spreadsheet().worksheet(sheet_name)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sheet.append_row([now, product_name, quantity, detail])
    except Exception as e:
        st.error(f"紀錄失敗：{e}")

# =========================================================
# 4. 先進先出 (FIFO) 核心演算法
# =========================================================
def process_fifo_outbound(product_name, out_qty, sheet, headers, records):
    batches = []
    for i, rec in enumerate(records):
        if str(rec.get('商品名稱')) == product_name:
            stock = extract_number(rec.get('庫存數量', 0))
            if stock > 0:
                expiry_str = str(rec.get('有效期限', '')).strip()
                if not expiry_str:
                    expiry_str = '2099-12-31' 
                batches.append({
                    'row_idx': i + 2, 
                    'stock': float(stock),
                    'expiry': expiry_str,
                    'unit': extract_unit(str(rec.get('庫存數量', '')))
                })

    batches.sort(key=lambda x: x['expiry'])
    total_stock = sum(b['stock'] for b in batches)
    if total_stock < out_qty:
        return False, f"庫存不足！(目前總計只剩 {total_stock})", []

    updates = []
    remaining_to_deduct = float(out_qty)

    for batch in batches:
        if remaining_to_deduct <= 0:
            break

        deduct_amount = min(batch['stock'], remaining_to_deduct)
        new_stock = batch['stock'] - deduct_amount
        remaining_to_deduct -= deduct_amount

        stock_col = headers.index('庫存數量') + 1
        if new_stock.is_integer():
            new_stock = int(new_stock)
        final_stock_str = f"{new_stock} {batch['unit']}".strip()

        updates.append({
            "range": f"{gspread.utils.rowcol_to_a1(batch['row_idx'], stock_col)}",
            "values": [[final_stock_str]]
        })

        if '最後更新時間' in headers:
            time_col = headers.index('最後更新時間') + 1
            updates.append({
                "range": f"{gspread.utils.rowcol_to_a1(batch['row_idx'], time_col)}",
                "values": [[datetime.now().strftime('%Y-%m-%d %H:%M:%S')]]
            })

    return True, "成功", updates

def update_sheet_stock(product_name, quantity, action, expiry=None, detail_info="一般"):
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
            st.success(f"進貨成功：{product_name} +{quantity} (已建立新批次)")

        elif action in ['OUT', 'WASTE']:
            success, msg, updates = process_fifo_outbound(product_name, quantity, sheet, headers, records)
            if not success:
                st.error(f"{product_name} 扣帳失敗：{msg}")
                return

            sheet.batch_update(updates)
            if action == 'OUT':
                log_transaction('出庫紀錄', product_name, quantity, detail_info)
                st.warning(f"出庫成功：{product_name} -{quantity} (依 FIFO 原則扣除)")
            else:
                log_transaction('報廢紀錄', product_name, quantity, detail_info)
                st.error(f"報廢成功：{product_name} -{quantity} (依 FIFO 原則扣除)")
        else:
            st.error("未知的操作指令")
    except Exception as e:
        st.error(f"系統更新失敗：{e}")

# =========================================================
# 5. AI 自然語言指令解析 (Gemini 結構化輸出版本)
# =========================================================
def smart_parse_and_execute(text):
    st.info(f"🧠 正在委託 Gemini 進行語意大腦分析：『{text}』")
    
    all_products = get_all_products()
    
    prompt = f"""
你現在是餐廳倉儲系統的核心解析器。請將人類說的語音文字，精準拆解為結構化的倉儲指令。

目前系統內現有的官方商品品項清單如下：
{', '.join(all_products)}

你的任務：
1. 判斷動作(action)：'IN'(進貨/補貨/買了)、'OUT'(出庫/使用/消耗)、'WASTE'(報廢/壞掉/過期)。
2. 提取商品名稱(product)：請與官方商品清單比對，找出最吻合的商品名稱。如果清單內沒有，則保留原本提取的名字。
3. 提取數量(quantity)：必須是純數字(int 或 float)。如果對方說中文數字（如五、兩、十），請幫我換算成阿拉伯數字。

請絕對只輸出一個標準的 JSON 物件，不要任何 markdown 標籤(如 ```json)，不要多做解釋。
格式範例：
{{"action": "IN", "product": "高麗菜", "quantity": 5.0}}
"""
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content([prompt, text])
        
        clean_json_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(clean_json_text)
        
        ai_action = data.get("action")
        ai_product = data.get("product")
        ai_quantity = float(data.get("quantity", 1))
        
        st.success(f"🤖 AI 解析成功 ➡️ 動作：{ai_action} | 品項：{ai_product} | 數量：{ai_quantity}")
        
        update_sheet_stock(
            product_name=ai_product,
            quantity=ai_quantity,
            action=ai_action,
            detail_info=f"語音智慧助理：{text}"
        )
    except Exception as e:
        st.error(f"AI 語意解析失敗，切換回常規輸入：{e}")

# =========================================================
# 6. 前端介面排版佈局
# =========================================================
st.title("📦 AI 智慧倉儲系統")
show_kpi_dashboard()

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 AI 分析",
    "📸 OCR",
    "🎙️ 語音",
    "🕒 紀錄",
    "🍔 POS出餐"
])

# --- TAB1 (AI 分析頁面) ---
# --- TAB1 (AI 分析頁面 - 第二階段 AI 強化合體版) ---
with tab1:
    st.header("🧠 AI 智慧經營與預測中心")
    st.write("利用 Google Gemini 2.5 Flash 核心大腦，對餐廳營運數據進行深度機器學習與智囊決策：")
    
    # 建立三個專業的 AI 功能按鈕
    ai_col1, ai_col2, ai_col3 = st.columns(3)
    
    with ai_col1:
        run_prediction = st.button("📈 啟動 AI 銷售與需求預測 (未來7天)", use_container_width=True)
    with ai_col2:
        run_anomaly = st.button("🔍 執行 AI 倉儲異常行為偵測", use_container_width=True)
    with ai_col3:
        run_consultant = st.button("📊 生成 AI 智慧經營決策週報", use_container_width=True)

    # 讀取共用資料庫數據
    try:
        doc = connect_spreadsheet()
        df_stock_raw = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
        df_in_raw = pd.DataFrame(doc.worksheet('進貨紀錄').get_all_records())
        df_out_raw = pd.DataFrame(doc.worksheet('出庫紀錄').get_all_records())
        df_waste_raw = pd.DataFrame(doc.worksheet('報廢紀錄').get_all_records())
    except Exception as db_err:
        st.error(f"資料庫讀取失敗：{db_err}")
        df_stock_raw = pd.DataFrame()

    # =========================================================
    # 功能一：📈 AI 銷售與需求預測
    # =========================================================
    if run_prediction:
        st.markdown("---")
        st.subheader("🔮 未来 7 天物料需求預測與自動採購單")
        if df_out_raw.empty:
            st.info("目前尚無出庫紀錄，無法進行預測。")
        else:
            with st.spinner("AI 正在分析歷史銷售趨勢與耗速模型..."):
                model = genai.GenerativeModel('gemini-2.5-flash')
                prompt = f"""
                你是餐廳供應鏈專家。請依據以下歷史出庫（銷售）紀錄，結合目前的庫存總表，利用機器學習與語意分析思維，預測未來 7 天的需求。
                
                目前庫存總表：
                {df_stock_raw.to_string()}
                
                歷史出庫紀錄：
                {df_out_raw.tail(100).to_string()}
                
                請幫我分析並用繁體中文條列輸出：
                1. 【未來7天需求預測】：預測哪些原物料在未來一週內消耗量最大（估算具體數字）。
                2. 【建議採購清單】：對比目前庫存，直接列出未來 7 天建議補貨的「商品名稱」與「建議補貨數量」。
                3. 請用標準 Markdown 表格呈現預測數據，顯得更加專業。
                """
                response = model.generate_content(prompt)
                st.markdown(response.text)

    # =========================================================
    # 功能二：🔍 AI 倉儲異常行為偵測
    # =========================================================
    if run_anomaly:
        st.markdown("---")
        st.subheader("🕵️‍♂️ 系統自動化稽核與異常偵測告警")
        with st.spinner("安全稽核大腦掃描中，正在偵測異常浪費或數據吻合度..."):
            model = genai.GenerativeModel('gemini-2.5-flash')
            prompt = f"""
            你是餐廳資深防弊與內控審計專家。請仔細比對以下「目前庫存」、「出庫紀錄」與「報廢紀錄」，找出潛在的營運異常點。
            
            目前庫存狀態：
            {df_stock_raw.to_string()}
            
            進貨紀錄：
            {df_in_raw.tail(50).to_string()}
            
            報廢紀錄：
            {df_waste_raw.tail(50).to_string()}
            
            請幫我掃描並抓出任何異常（例如：某些高價值食材報廢率高得不合理、庫存扣除速度與進貨週期嚴重脫鉤、是否有疑似偷料或管理疏失的黑洞）。
            如果數據一切正常，也請給予肯定的安全評估。請用繁體中文條列式回答。
            """
            response = model.generate_content(prompt)
            st.warning(response.text)

    # =========================================================
    # 功能三：📊 生成 AI 智慧經營決策週報
    # =========================================================
    if run_consultant:
        st.markdown("---")
        st.subheader("🏦 鼎極餐廳 智慧商務經營決策報告")
        with st.spinner("正在結算經營毛利結構並撰寫決策週報..."):
            # 打包當前系統的定價與成本狀態
            recipe_summary = str(st.session_state.menu_recipes)
            price_summary = str(st.session_state.meal_prices)
            cost_summary = str(st.session_state.ingredient_costs)
            
            model = genai.GenerativeModel('gemini-2.5-flash')
            prompt = f"""
            你是擁有 MBA 學位的頂級餐飲業財務顧問。請結合以下餐廳的「產品定價」、「原物料成本」、「食譜配方(BOM)」以及「歷史報廢與出庫數據」，為老闆寫一份高階經營診斷週報。
            
            餐點食譜(BOM)：{recipe_summary}
            餐點販售售價：{price_summary}
            原料進貨成本：{cost_summary}
            近期出庫紀錄：{df_out_raw.tail(30).to_string()}
            近期報廢損失：{df_waste_raw.tail(30).to_string()}
            
            請從高階管理者的角度出發，提供以下繁體中文分析：
            1. 【財務診斷】：哪道餐點的利潤結構（毛利率）最高？哪道最低？報廢損失對目前的利潤造成多大的衝擊？
            2. 【具體經營建議】：老闆下一步應該調整哪道菜的售價？或是該如何優化供應鏈採購成本？
            語氣請保持絕對專業、嚴謹且具備商業指導價值。
            """
            response = model.generate_content(prompt)
            st.info(response.text)

    # =========================================================
    # 底部保留原有的：常規趨勢耗速統計與常規對話聊天室
    # =========================================================
    st.markdown("---")
    ai_chat_mode()
    
    st.markdown("---")
    st.header("📊 常規歷史耗速統計（基礎演算法）")
    if st.button("開始分析常規趨勢"):
        try:
            if not df_stock_raw.empty:
                df_stock_copy = df_stock_raw.copy()
                df_stock_copy['庫存數量'] = df_stock_copy['庫存數量'].apply(extract_number)
            if not df_out_raw.empty:
                df_out_copy = df_out_raw.copy()
                df_out_copy['數量'] = df_out_copy['數量'].apply(extract_number)
                df_out_copy['日期'] = pd.to_datetime(df_out_copy['日期'], format='mixed', errors='coerce')

            report = []
            today = datetime.now()

            for _, row in df_stock_copy.iterrows():
                product = row['商品名稱']
                current_stock = row['庫存數量']
                product_out = df_out_copy[df_out_copy['商品名稱'] == product]

                if not product_out.empty:
                    days = max(1, (today - product_out['日期'].min()).days)
                    consumed = product_out['數量'].sum()
                    burn_rate = consumed / days
                else:
                    burn_rate = 0

                days_remaining = current_stock / burn_rate if burn_rate > 0 else 999
                suggestion = "安全"
                if current_stock <= SAFE_STOCK_LEVEL:
                    suggestion = "立即補貨"
                elif days_remaining <= 3:
                    suggestion = "即將缺貨"

                report.append({
                    "商品": product,
                    "庫存": current_stock,
                    "日耗": round(burn_rate, 2),
                    "剩餘天數": int(days_remaining) if days_remaining != 999 else "-",
                    "建議": suggestion
                })

            df_report = pd.DataFrame(report)
            st.dataframe(df_report, use_container_width=True)
            st.bar_chart(df_report.set_index("商品")[["庫存"]])
        except Exception as e:
            st.error(e)
# --- TAB2 (AI OCR 單據辨識頁面) ---
with tab2:
    st.header("📸 AI OCR 單據辨識")
    uploaded = st.file_uploader("上傳單據", type=['jpg', 'jpeg', 'png'])
    if uploaded:
        st.image(uploaded)
        if st.button("開始辨識"):
            try:
                img = Image.open(uploaded)
                model = genai.GenerativeModel('gemini-2.5-flash')
                prompt = """
你是餐廳 OCR 倉儲系統。
請辨識：1. 商品名稱 2. 數量
規則：僅輸出 JSON array，不要 markdown，不要解釋。quantity 必須是數字。
格式：[{"product":"高麗菜", "quantity":3}]
"""
                response = model.generate_content([img, prompt])
                json_match = re.search(r'\[.*\]', response.text, re.S)
                if not json_match:
                    st.error("無法解析 JSON")
                    st.stop()

                items = json.loads(json_match.group())
                for item in items:
                    update_sheet_stock(item['product'], item['quantity'], 'IN', detail_info='AI OCR')
                st.balloons()
            except Exception as e:
                st.error(e)

# --- TAB3 (AI 語音助理頁面) ---
with tab3:
    st.header("🎙️ AI 語音助理")
    audio_file = st.audio_input("錄音")
    if audio_file:
        if st.button("開始分析語音"):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                    tmp.write(audio_file.getvalue())
                    tmp_path = tmp.name

                audio_upload = genai.upload_file(path=tmp_path)
                model = genai.GenerativeModel('gemini-2.5-flash')
                all_products = get_all_products()
                prompt = f"請轉成繁體中文。商品可能包含：{','.join(all_products)}。請修正發音錯誤。只輸出最終文字。"
                
                response = model.generate_content([audio_upload, prompt])
                spoken = response.text.strip()
                st.success(f"辨識結果：{spoken}")
                
                smart_parse_and_execute(spoken)
                os.remove(tmp_path)
            except Exception as e:
                st.error(e)

# --- TAB4 (歷史紀錄與報表匯出頁面) ---
with tab4:
    st.header("🕒 最新紀錄")
    try:
        doc = connect_spreadsheet()
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
            except:
                continue

        if dfs:
            df_history_wall = pd.concat(dfs)
            time_col = '日期' if '日期' in df_history_wall.columns else ('最後更新時間' if '最後更新時間' in df_history_wall.columns else None)
            if time_col:
                df_history_wall[time_col] = pd.to_datetime(df_history_wall[time_col], errors='coerce')
                df_history_wall = df_history_wall.sort_values(by=time_col, ascending=False)
            st.dataframe(df_history_wall.head(20), use_container_width=True)
        else:
            st.info("尚無歷史紀錄")

        st.markdown("---")
        st.subheader("📥 倉儲資料匯出")
        df_stock_final = pd.DataFrame(fetch_sheet_data_cached('工作表1'))
        if not df_stock_final.empty:
            csv_data = df_stock_final.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="下載最新庫存總表 (CSV檔)",
                data=csv_data,
                file_name=f"餐廳_庫存總表_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("目前庫存資料庫中還沒有資料可以匯出喔！")
    except Exception as e:
        st.error(f"紀錄分頁載入失敗：{e}")

# --- TAB5 (POS 前台出餐與動態食譜後台) ---
with tab5:
    st.header("🍔 POS 前台出餐與動態後台管理")
    setup_col, pos_col = st.columns([1, 1.2])
    
    with setup_col:
        st.subheader("⚙️ 系統後台管理中心")
        manage_tab1, manage_tab2, manage_tab3 = st.tabs(["➕ 新增餐點", "✏️ 編輯/刪除餐點", "💰 原料成本管理"])
        
        raw_ingredients = get_all_products()
        available_ingredients = sorted(list(set(raw_ingredients)))
        available_ingredients = [item for item in available_ingredients if item.strip() != ""]
        
        # 1. 新增餐點 (含定價)
        with manage_tab1:
            new_meal_name = st.text_input("1. 輸入新餐點名稱", placeholder="例如：培根蛋吐司", key="add_meal_name")
            new_meal_price = st.number_input("2. 設定此餐點販售售價 (元)", min_value=0.0, value=100.0, step=5.0, key="add_meal_price")
            selected_ings = st.multiselect("3. 選擇這道餐點會消耗哪些原料", options=available_ingredients, key="add_meal_ings")
            
            new_recipe = {}
            if selected_ings:
                st.markdown("##### 4. 設定原料消耗量：")
                for ing in selected_ings:
                    qty = st.number_input(f"每份消耗【{ing}】數量：", min_value=0.01, value=1.0, step=0.1, key=f"add_qty_{ing}")
                    new_recipe[ing] = qty
            
            if st.button("💾 儲存新餐點配方與售價", use_container_width=True, key="save_new_btn"):
                if not new_meal_name.strip():
                    st.error("請輸入餐點名稱！")
                elif not new_recipe:
                    st.error("請至少選擇一種原料並設定數量！")
                else:
                    meal_key = new_meal_name.strip()
                    st.session_state.menu_recipes[meal_key] = new_recipe
                    st.session_state.meal_prices[meal_key] = float(new_meal_price)
                    st.success(f"🎉 成功新增餐點：{meal_key}，定價 ${new_meal_price} 元！")
                    st.rerun()

        # 2. 編輯 / 刪除餐點 (含定價修改)
        with manage_tab2:
            if st.session_state.menu_recipes:
                edit_meal_target = st.selectbox("選擇要管理的餐點", options=list(st.session_state.menu_recipes.keys()), key="edit_meal_select")
                current_recipe = st.session_state.menu_recipes[edit_meal_target]
                current_price = st.session_state.meal_prices.get(edit_meal_target, 0.0)
                
                st.markdown(f"##### ✏️ 更改【{edit_meal_target}】的設定")
                edit_meal_price = st.number_input("調整販售售價 (元)", min_value=0.0, value=float(current_price), step=5.0, key=f"edit_price_{edit_meal_target}")
                
                safe_options = list(set(available_ingredients + list(current_recipe.keys())))
                safe_options = sorted([item for item in safe_options if item.strip() != ""])
                
                edit_selected_ings = st.multiselect("調整原料品項", options=safe_options, default=list(current_recipe.keys()), key=f"edit_ings_select_{edit_meal_target}")
                
                updated_recipe = {}
                if edit_selected_ings:
                    for ing in edit_selected_ings:
                        default_qty = current_recipe.get(ing, 1.0)
                        qty = st.number_input(f"每份消耗【{ing}】數量：", min_value=0.01, value=float(default_qty), step=0.1, key=f"edit_qty_{edit_meal_target}_{ing}")
                        updated_recipe[ing] = qty
                
                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    if st.button("💾 更新配方與售價", use_container_width=True, type="primary", key="update_recipe_btn"):
                        if not updated_recipe:
                            st.error("配方不能完全沒有原料！")
                        else:
                            st.session_state.menu_recipes[edit_meal_target] = updated_recipe
                            st.session_state.meal_prices[edit_meal_target] = float(edit_meal_price)
                            st.success(f"⚙️ {edit_meal_target} 配方與售價修改成功！")
                            st.rerun()
                            
                with btn_col2:
                    if st.button("❌ 刪除此餐點", use_container_width=True, key="delete_recipe_btn"):
                        if edit_meal_target in st.session_state.menu_recipes:
                            del st.session_state.menu_recipes[edit_meal_target]
                        if edit_meal_target in st.session_state.meal_prices:
                            del st.session_state.meal_prices[edit_meal_target]
                        st.warning(f"🗑️ 已將【{edit_meal_target}】從菜單移除")
                        st.rerun()
            else:
                st.info("目前菜單內沒有任何自訂餐點。")

        # 3. 動態原料成本管理 (完全可自訂新增、修改、刪除單價)
        with manage_tab3:
            st.markdown("##### ✏️ 調整 / 新增原料進貨成本")
            
            # 使用下拉選單讓使用者可以選取現有原料，或是自訂輸入新原料
            cost_mode = st.radio("操作模式", ["修改現有原料單價", "新增未列出原料之單價"], horizontal=True)
            
            if cost_mode == "修改現有原料單價":
                if st.session_state.ingredient_costs:
                    target_ing = st.selectbox("選擇原料品項", options=list(st.session_state.ingredient_costs.keys()))
                    current_ing_cost = st.session_state.ingredient_costs[target_ing]
                    new_ing_cost = st.number_input(f"設定【{target_ing}】單位進貨成本 (元)", min_value=0.0, value=float(current_ing_cost), step=1.0)
                    
                    c_btn1, c_btn2 = st.columns(2)
                    with c_btn1:
                        if st.button("💾 儲存單價修改", use_container_width=True, type="primary"):
                            st.session_state.ingredient_costs[target_ing] = float(new_ing_cost)
                            st.success(f"✅ 已將 【{target_ing}】 的單位成本更新為 ${new_ing_cost} 元！")
                            st.rerun()
                    with c_btn2:
                        if st.button("🗑️ 刪除此原料成本紀錄", use_container_width=True):
                            del st.session_state.ingredient_costs[target_ing]
                            st.warning(f"🗑️ 已刪除 【{target_ing}】 的成本單價紀錄。")
                            st.rerun()
                else:
                    st.info("目前成本資料庫是空的。")
            else:
                custom_ing_name = st.text_input("輸入新原料名稱 (例: 培根)", placeholder="請輸入商品代碼或名稱")
                custom_ing_cost = st.number_input("設定單位進貨成本 (元)", min_value=0.0, value=10.0, step=1.0)
                if st.button("➕ 新增此原料成本單價", use_container_width=True):
                    if not custom_ing_name.strip():
                        st.error("請填寫原料名稱！")
                    else:
                        st.session_state.ingredient_costs[custom_ing_name.strip()] = float(custom_ing_cost)
                        st.success(f"🎉 成功登記新物料：{custom_ing_name.strip()} 成本為 ${custom_ing_cost} 元！")
                        st.rerun()
            
            st.markdown("---")
            st.caption("📋 當前原料進貨成本價目表清單：")
            st.json(st.session_state.ingredient_costs)

    with pos_col:
        st.subheader("🛒 前台一鍵出餐 (自動連動 FIFO)")
        st.write("點擊餐點按鈕，系統會自動拆解食譜、核算即時毛利並扣除庫存：")
        st.markdown("---")
        
        current_menu = st.session_state.menu_recipes
        grid_cols = st.columns(2)
        for idx, (meal_name, ingredients) in enumerate(current_menu.items()):
            with grid_cols[idx % 2]:
                meal_price_show = st.session_state.meal_prices.get(meal_name, 0.0)
                st.markdown(f"**{meal_name}** — 💰售價: `${meal_price_show}` 元")
                recipe_text = " / ".join([f"{k}:{v}" for k, v in ingredients.items()])
                st.caption(f"配方：{recipe_text}")
                
                if st.button("🛒 賣出一份", key=f"pos_btn_{meal_name}", use_container_width=True):
                    st.toast(f"正在檢查 {meal_name} 的原料庫存...")
                    try:
                        records = fetch_sheet_data_cached('工作表1')
                        total_stock_map = {}
                        for rec in records:
                            p_name = str(rec.get('商品名稱'))
                            try:
                                stock_val = float(extract_number(rec.get('庫存數量', 0)))
                            except:
                                stock_val = 0.0
                            if p_name not in total_stock_map:
                                total_stock_map[p_name] = 0.0
                            total_stock_map[p_name] += stock_val
                    except Exception as err:
                        st.error(f"無法讀取庫存進行預檢：{err}")
                        continue

                    all_ingredients_sufficient = True
                    insufficient_details = []

                    for item_name, required_qty in ingredients.items():
                        current_available = total_stock_map.get(item_name, 0.0)
                        if current_available < required_qty:
                            all_ingredients_sufficient = False
                            insufficient_details.append(f"❌ 【{item_name}】還差 {required_qty - current_available} 個 (目前剩 {current_available})")

                    if not all_ingredients_sufficient:
                        st.error(f"🚨 {meal_name} 出餐失敗！原料庫存不足：")
                        for msg in insufficient_details:
                            st.write(msg)
                    else:
                        st.info(f"庫存檢查通過！開始製作 {meal_name}...")
                        for item_name, qty in ingredients.items():
                            update_sheet_stock(product_name=item_name, quantity=qty, action='OUT', detail_info=f"POS出餐：{meal_name}")
                        
                        # 🧮 財務面：動態計算這份餐點的即時利潤與成本
                        price = st.session_state.meal_prices.get(meal_name, 0.0)
                        cost = 0.0
                        for ing_name, required_qty in ingredients.items():
                            cost += required_qty * st.session_state.ingredient_costs.get(ing_name, 0.0)
                        
                        margin = price - cost
                        margin_rate = (margin / price) * 100 if price > 0 else 0
                        
                        st.success(f"✅ {meal_name} 出餐成功！已依 FIFO 扣除原料。")
                        
                        st.markdown("---")
                        st.markdown(f"##### 📈 本筆交易即時財務分析 ({meal_name})")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("餐點售價", f"${price} 元")
                        c2.metric("物料成本", f"${round(cost, 1)} 元")
                        c3.metric("本單毛利", f"${round(margin, 1)} 元")
                        c4.metric("毛利率", f"{round(margin_rate, 1)}%")
                        st.balloons()
