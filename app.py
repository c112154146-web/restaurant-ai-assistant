import streamlit as st
import pandas as pd
import gspread
import json
import re
import uuid
import cn2an
import google.generativeai as genai

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


# =========================================================
# 3. 工具函式
# =========================================================
def show_kpi_dashboard():

    doc = connect_spreadsheet()

    df_stock = pd.DataFrame(
        doc.worksheet('工作表1').get_all_records()
    )

    df_in = pd.DataFrame(
        doc.worksheet('進貨紀錄').get_all_records()
    )

    df_waste = pd.DataFrame(
        doc.worksheet('報廢紀錄').get_all_records()
    )

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
                    - datetime.now()
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

def update_sheet_stock(
    product_name,
    quantity,
    action,
    expiry=None,
    detail_info="一般"
):

    try:

        doc = connect_spreadsheet()
        sheet = doc.worksheet('工作表1')

        headers = sheet.row_values(1)

        records = sheet.get_all_records()

        stock_col = headers.index('庫存數量') + 1

        target_row = None
        current_stock = 0.0
        current_unit = ""

        for i, rec in enumerate(records):

            if str(rec.get('商品名稱')) == product_name:

                target_row = i + 2

                stock_str = str(rec.get('庫存數量', '0'))

                current_stock = extract_number(stock_str)
                current_unit = extract_unit(stock_str)

                break

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

    in_kw = ['進貨', '新增', '補貨', '入庫']
    out_kw = ['使用', '用了', '消耗', '出餐']
    waste_kw = ['報廢', '壞掉', '過期', '爛掉']

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
                text = text.replace(k, '', 1)
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

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 AI 分析",
    "📸 OCR",
    "🎙️ 語音",
    "🕒 紀錄"
])

with tab1:

    ai_purchase_suggestion()

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

            today = datetime.now()

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

        for sheet_name, action_name in mapping.items():

            try:

                df = pd.DataFrame(
                    doc.worksheet(sheet_name).get_all_records()
                )

                if not df.empty:

                    df['動作'] = action_name

                    dfs.append(df)

            except:
                pass

        if dfs:

            df_all = pd.concat(dfs)

            df_all['日期'] = pd.to_datetime(
                df_all['日期'],
                errors='coerce'
            )

            df_all = df_all.sort_values(
                by='日期',
                ascending=False
            )

            st.dataframe(
                df_all.head(20),
                use_container_width=True
            )

        else:
            st.info("尚無資料")

    except Exception as e:
        st.error(e)
