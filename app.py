import streamlit as st
import pandas as pd
import gspread
import json
import re
import uuid
import cn2an
import google.generativeai as genai

import tempfile  # 👈 新增這行：用來處理暫存錄音檔
import os        # 👈 新增這行：用來刪除暫存檔

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

SAFE_STOCK_LEVEL = 5

# =========================================================
# ⭐ 新增：餐廳食譜/配方表 (BOM)
# =========================================================
SAFE_STOCK_LEVEL = 5

# =========================================================
# ⭐ 改為：動態餐廳食譜暫存（支援動態新增）
# =========================================================
if "menu_recipes" not in st.session_state:
    st.session_state.menu_recipes = {
        "🍔 經典牛肉漢堡": {"漢堡麵包": 1.0, "牛肉串": 1.0, "高麗菜": 0.1},
        "🥪 總匯三明治": {"吐司": 3.0, "雞蛋": 1.0, "火腿": 1.0},
        "🍳 起司蛋餅": {"蛋餅皮": 1.0, "雞蛋": 1.0, "起司片": 1.0}
    }

# =========================================================
# 2. Google Sheets 連線（加快速度）
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
        today_in = len(
            df_in[
                df_in['日期'].astype(str).str.contains(today)
            ]
        )

    if not df_waste.empty:
        today_waste = len(
            df_waste[
                df_waste['日期'].astype(str).str.contains(today)
            ]
        )

    low_stock = 0
    expiry_count = 0

    for _, row in df_stock.iterrows():

        stock = extract_number(
            row.get('庫存數量', 0)
        )

        if stock <= 5:
            low_stock += 1

        expiry = str(
            row.get('有效期限', '')
        ).strip()

        if expiry:

            try:

                days = (
                    pd.to_datetime(expiry)
                    - datetime.当前()
                ).days

                if days <= 3:
                    expiry_count += 1

            except:
                pass

    col1, col2, col3, col4 = st.columns(4)

    col1.metric(
        "📦 今日進貨",
        today_in
    )

    col2.metric(
        "🗑️ 今日報廢",
        today_waste
    )

    col3.metric(
        "⚠️ 即期商品",
        expiry_count
    )

    col4.metric(
        "🚨 低庫存",
        low_stock
    )

def ai_chat_mode():

    st.subheader("🤖 AI 倉儲助理")

    user_question = st.chat_input(
        "請詢問庫存問題..."
    )

    if user_question:

        doc = connect_spreadsheet()

        df_stock = pd.DataFrame(
            doc.worksheet('工作表1').get_all_records()
        )

        df_out = pd.DataFrame(
            doc.worksheet('出庫紀錄').get_all_records()
        )

        model = genai.GenerativeModel(
            'gemini-2.5-flash'
        )

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

        response = model.generate_content(
            prompt
        )

        st.chat_message("user").write(
            user_question
        )

        st.chat_message("assistant").write(
            response.text
        )

def ai_purchase_suggestion():

    st.subheader("🧠 AI 採購建議")

    doc = connect_spreadsheet()

    df_stock = pd.DataFrame(
        doc.worksheet('工作表1').get_all_records()
    )

    df_out = pd.DataFrame(
        doc.worksheet('出庫紀錄').get_all_records()
    )

    model = genai.GenerativeModel(
        'gemini-2.5-flash'
    )

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

    response = model.generate_content(
        prompt
    )

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

        sheet.append_row([
            now,
            product_name,
            quantity,
            detail
        ])

    except Exception as e:
        st.error(f"紀錄失敗：{e}")


# =========================================================
# 4. 更新庫存（最佳化版）
# =========================================================
def process_fifo_outbound(product_name, out_qty, sheet, headers, records):
    """
    先進先出 (FIFO) 核心演算法
    回傳: (布林值是否成功, 訊息字串, 實際更新的 batch_update 列表)
    """
    batches = []
    
    # 1. 找出該商品「所有大於0的庫存批次」
    for i, rec in enumerate(records):
        if str(rec.get('商品名稱')) == product_name:
            stock = extract_number(rec.get('庫存數量', 0))
            if stock > 0:
                # 如果沒有填有效期限，給一個極大值讓它排在最後面
                expiry_str = str(rec.get('有效期限', '')).strip()
                if not expiry_str:
                    expiry_str = '2099-12-31' 
                    
                batches.append({
                    'row_idx': i + 2, # Google Sheets 是從 1 開始算，且有標題列
                    'stock': float(stock),
                    'expiry': expiry_str,
                    'unit': extract_unit(str(rec.get('庫存數量', '')))
                })

    # 2. 依照「有效期限」由近到遠排序 (Sort) - 這是 FIFO 的靈魂！
    batches.sort(key=lambda x: x['expiry'])

    # 3. 檢查總庫存是否足夠
    total_stock = sum(b['stock'] for b in batches)
    if total_stock < out_qty:
        return False, f"庫存不足！(目前總計只剩 {total_stock})", []

    # 4. 開始執行 FIFO 逐批扣除
    updates = []
    remaining_to_deduct = float(out_qty)

    for batch in batches:
        if remaining_to_deduct <= 0:
            break # 已經扣完，跳出迴圈

        # 決定這批要扣多少 (取這批的庫存量 和 剩餘要扣的量 兩者間的最小值)
        deduct_amount = min(batch['stock'], remaining_to_deduct)
        new_stock = batch['stock'] - deduct_amount
        remaining_to_deduct -= deduct_amount

        # 準備 Google Sheets 的更新格式
        stock_col = headers.index('庫存數量') + 1
        
        # 整理數字格式 (如果是整數就不顯示小數點)
        if new_stock.is_integer():
            new_stock = int(new_stock)
        final_stock_str = f"{new_stock} {batch['unit']}".strip()

        # 將更新指令加入清單
        updates.append({
            "range": f"{gspread.utils.rowcol_to_a1(batch['row_idx'], stock_col)}",
            "values": [[final_stock_str]]
        })

        # 同步更新「最後更新時間」
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

        # =================================================
        # 進貨 (IN) - 改為永遠新增一個獨立批次
        # =================================================
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

        # =================================================
        # 出庫與報廢 (OUT / WASTE) - 啟動 FIFO 演算法
        # =================================================
        elif action in ['OUT', 'WASTE']:
            # 呼叫我們的 FIFO 演算法
            success, msg, updates = process_fifo_outbound(product_name, quantity, sheet, headers, records)

            if not success:
                st.error(f"{product_name} 扣帳失敗：{msg}")
                return

            # 一次性更新 Google Sheets
            sheet.batch_update(updates)

            # 寫入紀錄
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

        # =================================================
        # 新商品
        # =================================================

        if target_row is None:

            new_row = [""] * len(headers)

            if '商品名稱' in headers:
                new_row[headers.index('商品名稱')] = product_name

            if '庫存數量' in headers:
                new_row[headers.index('庫存數量')] = quantity

            if '有效期限' in headers:
                new_row[headers.index('有效期限')] = expiry or ""

            if '最後更新時間' in headers:
                new_row[headers.index('最後更新時間')] = \
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            if 'ID' in headers:
                new_row[headers.index('ID')] = str(uuid.uuid4())[:8]

            sheet.append_row(new_row)

            st.success(f"新增商品：{product_name}")

            log_transaction(
                '進貨紀錄',
                product_name,
                quantity,
                detail_info
            )

            return

        # =================================================
        # 計算新庫存
        # =================================================

        quantity = float(quantity)

        if action == 'IN':
            new_stock = current_stock + quantity

        elif action in ['OUT', 'WASTE']:

            if current_stock < quantity:
                st.error(
                    f"{product_name} 庫存不足 "
                    f"(目前 {current_stock})"
                )
                return

            new_stock = current_stock - quantity

        else:
            st.error("未知動作")
            return

        # =================================================
        # 格式化
        # =================================================

        if new_stock.is_integer():
            new_stock = int(new_stock)

        final_stock = f"{new_stock}"

        if current_unit:
            final_stock += f" {current_unit}"

        # =================================================
        # batch_update（比 update_cell 快）
        # =================================================

        updates = []

        updates.append({
            "range": f"{gspread.utils.rowcol_to_a1(target_row, stock_col)}",
            "values": [[final_stock]]
        })

        if expiry and '有效期限' in headers:

            expiry_col = headers.index('有效期限') + 1

            updates.append({
                "range":
                    f"{gspread.utils.rowcol_to_a1(target_row, expiry_col)}",
                "values": [[expiry]]
            })

        if '最後更新時間' in headers:

            time_col = headers.index('最後更新時間') + 1

            updates.append({
                "range":
                    f"{gspread.utils.rowcol_to_a1(target_row, time_col)}",
                "values": [[
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ]]
            })

        sheet.batch_update(updates)

        # =================================================
        # 寫入紀錄
        # =================================================

        if action == 'IN':

            log_transaction(
                '進貨紀錄',
                product_name,
                quantity,
                detail_info
            )

            st.success(
                f"進貨成功：{product_name} +{quantity}"
            )

        elif action == 'OUT':

            log_transaction(
                '出庫紀錄',
                product_name,
                quantity,
                detail_info
            )

            st.warning(
                f"出庫成功：{product_name} -{quantity}"
            )

        elif action == 'WASTE':

            log_transaction(
                '報廢紀錄',
                product_name,
                quantity,
                detail_info
            )

            st.error(
                f"報廢成功：{product_name} -{quantity}"
            )

    except Exception as e:
        st.error(f"更新失敗：{e}")


# =========================================================
# 5. AI 指令解析
# =========================================================

def smart_parse_and_execute(text):

    text = text.strip()

    action = None

    in_kw = ['進貨', '新增', '補貨', '入庫', '買了']
    out_kw = ['使用', '用了', '消耗', '出餐', '銷貨', '賣出', '賣了', '扣掉']
    waste_kw = ['報廢', '壞掉', '過期', '爛掉', '丟掉', '破掉']

    for k in in_kw:
        if k in text:
            action = 'IN'
            text = text.replace(k, '', 1)
            break

    if not action:
        for k in out_kw:
            if k in text:
                action = 'OUT'
                text = text.replace(k, '', 1)
                break

    if not action:
        for k in waste_kw:
            if k in text:
                action = 'WASTE'
                text = text.替换(k, '', 1)
                break

    if not action:
        st.error("找不到動作")
        return

    # =====================================================
    # 數量解析
    # =====================================================

    qty = 1

    qty_match = re.search(
        r'([0-9一二三四五六七八九十百千兩]+)',
        text
    )

    if qty_match:

        num_str = qty_match.group(1)

        try:

            if num_str.isdigit():
                qty = int(num_str)

            else:
                qty = cn2an.cn2an(num_str)

            text = text.replace(num_str, '', 1)

        except:
            qty = 1

    # =====================================================
    # 商品名稱清理
    # =====================================================

    product = re.sub(
        r'[個包箱公斤斤克瓶顆件把台條]',
        '',
        text
    ).strip()

    # =====================================================
    # 模糊比對
    # =====================================================

    all_products = get_all_products()

    if all_products:

        best_match = process.extractOne(
            product,
            all_products,
            scorer=fuzz.partial_ratio
        )

        if best_match:

            matched_name, score, _ = best_match

            if score >= 80:

                if matched_name != product:

                    st.info(
                        f"模糊比對："
                        f"{product} → {matched_name}"
                    )

                product = matched_name

    update_sheet_stock(
        product_name=product,
        quantity=qty,
        action=action
    )


# =========================================================
# 6. UI
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

# =========================================================
# TAB1 (AI 分析)
# =========================================================
with tab1:
    
    # ✅ 加上這兩行：用按鈕把功能包起來，避免它無限自動觸發
    if st.button("🧠 產出 AI 採購建議"):
        ai_purchase_suggestion()
    
    st.markdown("---") # 加一條分隔線讓畫面比較好看
    
    ai_chat_mode()
# =========================================================
# TAB1
# =========================================================

with tab1:

    st.header("📊 AI 庫存分析")

    if st.button("開始分析"):

        try:

            doc = connect_spreadsheet()

            df_stock = pd.DataFrame(
                doc.worksheet('工作表1').get_all_records()
            )

            df_in = pd.DataFrame(
                doc.worksheet('進貨紀錄').get_all_records()
            )

            df_out = pd.DataFrame(
                doc.worksheet('出庫紀錄').get_all_records()
            )

            df_waste = pd.DataFrame(
                doc.worksheet('報廢紀錄').get_all_records()
            )

            if not df_stock.empty:

                df_stock['庫存數量'] = \
                    df_stock['庫存數量'].apply(extract_number)

            if not df_out.empty:

                df_out['數量'] = \
                    df_out['數量'].apply(extract_number)

                df_out['日期'] = pd.to_datetime(
                    df_out['日期'],
                    format='mixed',
                    errors='coerce'
                )

            report = []

            today = datetime.当前()

            for _, row in df_stock.iterrows():

                product = row['商品名稱']
                current_stock = row['庫存數量']

                product_out = df_out[
                    df_out['商品名稱'] == product
                ]

                if not product_out.empty:

                    days = max(
                        1,
                        (
                            today -
                            product_out['日期'].min()
                        ).days
                    )

                    consumed = product_out['數量'].sum()

                    burn_rate = consumed / days

                else:

                    burn_rate = 0

                days_remaining = \
                    current_stock / burn_rate \
                    if burn_rate > 0 else 999

                suggestion = "安全"

                if current_stock <= SAFE_STOCK_LEVEL:
                    suggestion = "立即補貨"

                elif days_remaining <= 3:
                    suggestion = "即將缺貨"

                report.append({
                    "商品": product,
                    "庫存": current_stock,
                    "日耗": round(burn_rate, 2),
                    "剩餘天數":
                        int(days_remaining)
                        if days_remaining != 999
                        else "-",
                    "建議": suggestion
                })

            df_report = pd.DataFrame(report)

            st.dataframe(
                df_report,
                use_container_width=True
            )

            st.bar_chart(
                df_report.set_index("商品")[["庫存"]]
            )

        except Exception as e:
            st.error(e)


# =========================================================
# TAB2 OCR
# =========================================================

with tab2:

    st.header("📸 AI OCR 單據辨識")

    uploaded = st.file_uploader(
        "上傳單據",
        type=['jpg', 'jpeg', 'png']
    )

    if uploaded:

        st.image(uploaded)

        if st.button("開始辨識"):

            try:

                img = Image.open(uploaded)

                model = genai.GenerativeModel(
                    'gemini-2.5-flash'
                )

                prompt = """
你是餐廳 OCR 倉儲系統。

請辨識：
1. 商品名稱
2. 數量

規則：
- 僅輸出 JSON array
- 不要 markdown
- 不要解釋
- quantity 必須是數字
- 若無法辨識數量則填 1

格式：

[
 {
   "product":"高麗菜",
   "quantity":3
 }
]
"""

                response = model.generate_content(
                    [img, prompt]
                )

                result_text = response.text

                json_match = re.search(
                    r'\[.*\]',
                    result_text,
                    re.S
                )

                if not json_match:
                    st.error("無法解析 JSON")
                    st.stop()

                items = json.loads(
                    json_match.group()
                )

                for item in items:

                    update_sheet_stock(
                        item['product'],
                        item['quantity'],
                        'IN',
                        detail_info='AI OCR'
                    )

                st.balloons()

            except Exception as e:
                st.error(e)


# =========================================================
# TAB3 語音
# =========================================================

with tab3:

    st.header("🎙️ AI 語音助理")

    audio_file = st.audio_input("錄音")

    if audio_file:

        if st.button("開始分析語音"):

            try:

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".wav"
                ) as tmp:

                    tmp.write(audio_file.getvalue())

                    tmp_path = tmp.name

                audio_upload = genai.upload_file(
                    path=tmp_path
                )

                model = genai.GenerativeModel(
                    'gemini-2.5-flash'
                )

                all_products = get_all_products()

                prompt = f"""
請轉成繁體中文。

商品可能包含：
{",".join(all_products)}

請修正發音錯誤。

只輸出最終文字。
"""

                response = model.generate_content(
                    [audio_upload, prompt]
                )

                spoken = response.text.strip()

                st.success(f"辨識結果：{spoken}")

                smart_parse_and_execute(spoken)

                os.remove(tmp_path)

            except Exception as e:
                st.error(e)


# =========================================================
# TAB4 紀錄
# =========================================================

# =========================================================
# TAB4 (歷史紀錄與報表匯出)
# =========================================================
# =========================================================
# TAB4 (歷史紀錄與報表匯出)
# =========================================================
with tab4:
    st.header("🕒 最新紀錄")

    try:
        doc = connect_spreadsheet()
        dfs = []
        mapping = {
            '進貨紀錄': '📦 進貨',
            '出庫紀錄': '📤 出庫',
            '報廢紀錄': '🗑️ 報廢'
        }

        # 1. 讀取並整合所有紀錄分頁
        for sheet_name, action_name in mapping.items():
            try:
                # 使用快取函式撈取歷史紀錄
                data_records = fetch_sheet_data_cached(sheet_name)
                if data_records:
                    df_single = pd.DataFrame(data_records)
                    if not df_single.empty:
                        # 標註這個紀錄的動作類型
                        df_single['動作'] = action_name
                        dfs.append(df_single)
            except Exception as sheet_err:
                continue

        # 2. 顯示動態紀錄牆
        if dfs:
            df_history_wall = pd.concat(dfs)

            # 檢查並排序時間
            time_col = '日期' if '日期' in df_history_wall.columns else ('最後更新時間' if '最後更新時間' in df_history_wall.columns else None)
            
            if time_col:
                df_history_wall[time_col] = pd.to_datetime(df_history_wall[time_col], errors='coerce')
                df_history_wall = df_history_wall.sort_values(by=time_col, ascending=False)

            st.dataframe(
                df_history_wall.head(20),
                use_container_width=True
            )
        else:
            st.info("尚無歷史紀錄")

        # 3. 📥 倉儲資料匯出區塊（變數名稱完全獨立，絕不衝突）
        st.markdown("---")
        st.subheader("📥 倉儲資料匯出")
        
        # 100% 確保這裡撈出來的東西叫做 df_stock_final
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
# =========================================================
# TAB5 (POS 出餐與自動扣料)
# =========================================================
# =========================================================
# TAB5 (POS 出餐與自訂食譜)
# =========================================================
with tab5:
    st.header("🍔 POS 前台出餐與動態食譜設定")
    
    # 建立兩個區塊：左邊是新增菜色，右邊是點餐前台
    setup_col, pos_col = st.columns([1, 1.2])
    
    # -----------------------------------------------------
    # 【左半邊：後台食譜設定】
    # -----------------------------------------------------
    with setup_col:
        st.subheader("➕ 開發新餐點 (設定配方)")
        
        new_meal_name = st.text_input("1. 輸入新餐點名稱", placeholder="例如：培根蛋吐司")
        
        # 動態從 Google Sheets 撈出目前現有的所有原料品項
        available_ingredients = get_all_products()
        
        selected_ings = st.multiselect(
            "2. 選擇這道餐點會消耗哪些原料",
            options=available_ingredients,
            help="可以複選。如果找不到原料，請先去【語音】或【OCR】進貨建檔喔！"
        )
        
        # 用來暫存這道新菜的配方內容
        new_recipe = {}
        if selected_ings:
            st.markdown("##### 3. 設定原料消耗量：")
            for ing in selected_ings:
                # 幫每個選中的原料建立一個數值輸入欄
                qty = st.number_input(
                    f"每賣出一份，固定消耗【{ing}】多少數量？",
                    min_value=0.01,
                    value=1.0,
                    step=0.1,
                    key=f"setup_{new_meal_name}_{ing}" # 確保 key 唯一
                )
                new_recipe[ing] = qty
        
        if st.button("💾 儲存新餐點配方", use_container_width=True):
            if not new_meal_name.strip():
                st.error("請輸入餐點名稱！")
            elif not new_recipe:
                st.error("請至少選擇一種原料並設定數量！")
            else:
                # 將新菜色寫入暫存字典中
                st.session_state.menu_recipes[new_meal_name.strip()] = new_recipe
                st.success(f"🎉 成功新增餐點：{new_meal_name}！")
                st.rerun() # 重新整理網頁，讓右邊立刻看到新按鈕

    # -----------------------------------------------------
    # 【右半邊：前台一鍵出餐】
    # -----------------------------------------------------
    with pos_col:
        st.subheader("🛒 前台一鍵出餐 (自動連動 FIFO)")
        st.write("點擊餐點按鈕，系統會自動拆解食譜並扣除庫存：")
        
        st.markdown("---")
        
        # 讀取剛剛暫存的所有菜色（包含預設的與用戶自己新增的）
        current_menu = st.session_state.menu_recipes
        
        # 畫出前台按鈕
        grid_cols = st.columns(2)
        for idx, (meal_name, ingredients) in enumerate(current_menu.items()):
            with grid_cols[idx % 2]:
                
                # 建立按鈕，並在按鈕下方用小字顯示它的配方
                st.markdown(f"**{meal_name}**")
                recipe_text = " / ".join([f"{k}:{v}" for k, v in ingredients.items()])
                st.caption(f"配方：{recipe_text}")
                
                if st.button("🛒 賣出一份", key=f"pos_btn_{meal_name}", use_container_width=True):
                    st.toast(f"正在製作 {meal_name}...")
                    
                    # 依據該餐點的配方，逐一自動扣除 Google Sheets 庫存
                    for item_name, qty in ingredients.items():
                        update_sheet_stock(
                            product_name=item_name,
                            quantity=qty,
                            action='OUT',
                            detail_info=f"POS出餐：{meal_name}"
                        )
                    
                    st.success(f"✅ {meal_name} 出餐成功！已依 FIFO 扣除原料。")
                    st.balloons()
