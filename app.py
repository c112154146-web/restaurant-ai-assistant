import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import re
import uuid
import google.generativeai as genai
from PIL import Image
from rapidfuzz import process, fuzz # 🌟 加入您的模糊比對套件

# ================= 1. 版面與基本設定 =================
st.set_page_config(page_title="餐廳倉儲助手", page_icon="📦", layout="centered")
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

def connect_spreadsheet():
    creds_dict = json.loads(st.secrets["gcp_service_account"]["credentials"])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client_gs = gspread.authorize(creds)
    return client_gs.open('智慧庫存系統')

# ================= 2. 雙軌紀錄與輔助函式 (來自您的完全體) =================
def extract_number(val):
    if pd.isna(val) or str(val).strip() == '': return 0.0
    match = re.search(r'[\d\.]+', str(val))
    return float(match.group()) if match else 0.0

def get_all_products():
    try:
        sheet = connect_spreadsheet().worksheet('工作表1')
        records = sheet.get_all_records()
        return [str(rec.get('商品名稱', '')) for rec in records if str(rec.get('商品名稱', '')).strip()]
    except:
        return []

def log_transaction(sheet_name, product_name, quantity, detail):
    try:
        log_sheet = connect_spreadsheet().worksheet(sheet_name)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_sheet.append_row([now, product_name, quantity, detail])
    except Exception as e:
        st.error(f"⚠️ 紀錄表寫入失敗: {e}")

def update_sheet_stock(product_name, quantity, action, expiry=None, detail_info="一般"):
    try:
        doc = connect_spreadsheet()
        sheet = doc.worksheet('工作表1')
        headers = sheet.row_values(1)
        stock_col_idx = headers.index('庫存數量') + 1

        records = sheet.get_all_records()
        target_row = None
        current_stock = 0

        for i, rec in enumerate(records):
            if str(rec.get('商品名稱')) == product_name:
                target_row = i + 2
                current_stock = rec.get('庫存數量', 0)
                if str(current_stock).strip() == '': current_stock = 0
                else: current_stock = int(current_stock)
                break

        if target_row is None:
            if action == 'WASTE':
                st.error(f"❌ 找不到商品：【{product_name}】，無法報廢。")
                return
            new_row = [""] * len(headers)
            if '商品名稱' in headers: new_row[headers.index('商品名稱')] = product_name
            if '庫存數量' in headers: new_row[headers.index('庫存數量')] = quantity
            if '有效期限' in headers and expiry: new_row[headers.index('有效期限')] = expiry
            if '最後更新時間' in headers: new_row[headers.index('最後更新時間')] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if 'ID' in headers: new_row[headers.index('ID')] = str(uuid.uuid4())[:8]

            sheet.append_row(new_row)
            st.success(f"✨ [新商品建檔] 自動新增【{product_name}】，數量: {quantity}")
            log_transaction('進貨紀錄', product_name, quantity, detail_info)
            return

        if action == 'IN':
            new_stock = current_stock + quantity
            sheet.update_cell(target_row, stock_col_idx, new_stock)
            st.success(f"✅ [進貨成功] {product_name} +{quantity} (目前: {new_stock})")
            log_transaction('進貨紀錄', product_name, quantity, detail_info)

        elif action == 'WASTE':
            if current_stock >= quantity:
                new_stock = current_stock - quantity
                sheet.update_cell(target_row, stock_col_idx, new_stock)
                st.warning(f"⚠️ [報廢成功] {product_name} -{quantity} (剩餘: {new_stock})")
                log_transaction('報廢紀錄', product_name, quantity, detail_info)
            else:
                st.error(f"❌ [庫存不足] {product_name} 只有 {current_stock}，無法報廢 {quantity}")
                return

        if expiry and '有效期限' in headers:
            expiry_col_idx = headers.index('有效期限') + 1
            sheet.update_cell(target_row, expiry_col_idx, expiry)

        if '最後更新時間' in headers:
            time_col_idx = headers.index('最後更新時間') + 1
            sheet.update_cell(target_row, time_col_idx, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    except Exception as e:
        st.error(f"❌ 更新失敗: {e}")

def smart_parse_and_execute(text):
    detail_info = "一般"
    src_match = re.search(r'(?:從|由)(.+?)(?:進貨|買|叫貨|來)', text)
    if src_match:
        detail_info = src_match.group(1).strip()
        text = text.replace(src_match.group(0), '進貨')
    else:
        reason_match = re.search(r'(?:因為)?(過期|壞掉|爛掉|發霉|破掉|損壞)', text)
        if reason_match:
            detail_info = reason_match.group(1).strip()
            text = text.replace(reason_match.group(0), '')

    action = None
    in_kw = ['進貨', '買', '新增', '入庫', '增加', '補貨']
    waste_kw = ['報廢', '丟', '爛', '壞', '出庫', '減少', '過期']
    for k in in_kw:
        if k in text:
            action = 'IN'
            text = text.replace(k, '', 1)
            break
    if not action:
        for k in waste_kw:
            if k in text:
                action = 'WASTE'
                text = text.replace(k, '', 1)
                break
    if not action and detail_info in ['過期', '壞掉', '爛掉', '發霉', '破掉', '損壞']:
        action = 'WASTE'

    qty = 1
    qty_match = re.search(r'([0-9一二兩三四五六七八九十百]+)(個|箱|包|公斤|克|斤|件|瓶|罐|把|顆|隻|條|台)?', text)
    if qty_match:
        num_str = qty_match.group(1)
        if num_str.isdigit(): qty = int(num_str)
        else:
            cn_map = {'一':1, '二':2, '兩':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10}
            for k, v in cn_map.items():
                if k in num_str: qty = v; break
        text = text.replace(qty_match.group(0), '', 1)

    spoken_product = re.sub(r'[，。、！？的幫我在到期因為]', '', text).strip()
    
    if not action:
        st.error("❌ 無法執行：請確認指令包含「進貨」或「報廢」等動作。")
        return
        
    all_products = get_all_products()
    target_product = spoken_product

    if all_products and spoken_product:
        best_match = process.extractOne(spoken_product, all_products, scorer=fuzz.partial_ratio)
        if best_match:
            matched_name, score, _ = best_match
            if score >= 80: 
                target_product = matched_name
                if target_product != spoken_product:
                    st.info(f"🔍 系統啟動模糊比對：已將「{spoken_product}」校正為「{target_product}」")
    
    update_sheet_stock(target_product, qty, action, expiry=None, detail_info=detail_info)

# ================= 3. 建立 App 介面 =================
st.title("📦倉儲助手")
st.markdown("歡迎使用 AI 庫存管理系統")

tab1, tab2, tab3 = st.tabs(["📊 庫存預測", "📸 單據辨識", "🎙️ 語音助理"])

with tab1:
    st.header("📊 庫存戰情室與採購建議")
    
    # 這裡可以設定您的安全警戒線（完美繼承您的設計）
    SAFE_STOCK_LEVEL = 5 
    
    if st.button("啟動 AI 運算 🚀", use_container_width=True):
        with st.spinner('正在拉取資料並運算中...'):
            try:
                doc = connect_spreadsheet()
                df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
                df_in = pd.DataFrame(doc.worksheet('進貨紀錄').get_all_records())

                df_stock['庫存數量'] = df_stock['庫存數量'].apply(extract_number)
                if '數量' in df_in.columns: df_in['數量'] = df_in['數量'].apply(extract_number)

                df_in['日期'] = pd.to_datetime(df_in['日期'])
                today = datetime.now()
                report_data = []
                
                # 🚨 新增：用來收集需要發出紅色警告的品項
                alert_items = [] 

                for index, row in df_stock.iterrows():
                    product = str(row.get('商品名稱', ''))
                    current_stock = row.get('庫存數量', 0.0)
                    if not product: continue

                    product_in = df_in[df_in['商品名稱'] == product]
                    
                    # 預設狀態
                    days_remaining = 999
                    daily_burn_rate = 0.0
                    
                    if not product_in.empty:
                        days_tracked = max(1, (today - product_in['日期'].min()).days)
                        total_consumed = max(0, product_in['數量'].sum() - current_stock)
                        daily_burn_rate = total_consumed / days_tracked
                        if daily_burn_rate > 0:
                            days_remaining = current_stock / daily_burn_rate

                    suggestion = "✅ 安全"
                    alert_reason = ""
                    needs_alert = False

                    # 雙重警告機制：數量太少 OR 天數太少 都會觸發
                    if current_stock <= SAFE_STOCK_LEVEL:
                        needs_alert = True
                        alert_reason = f"剩餘數量極低 (僅剩 {int(current_stock)})"
                    elif days_remaining <= 3:
                        needs_alert = True
                        alert_reason = f"預測撐不過 3 天 (約剩 {int(days_remaining)} 天)"
                        
                    if needs_alert:
                        suggestion = "🚨 立即叫貨"
                        alert_items.append(f"**{product}**：{alert_reason}")
                    elif days_remaining <= 7: 
                        suggestion = "⚠️ 即將見底"

                    report_data.append({
                        "品項": product, 
                        "庫存": int(current_stock), 
                        "日耗/天": f"{daily_burn_rate:.1f}", 
                        "剩餘天數": f"{int(days_remaining)}天" if days_remaining != 999 else "-", 
                        "建議": suggestion
                    })

                # 🌟 亮點：在畫面最上方顯示大大的警告框！
               # 🌟 亮點：在畫面最上方顯示大大的警告框！
                if alert_items:
                    st.error("🚨 **【異常狀況警報：庫存過低】** 請盡速安排補貨！")
                    for item in alert_items:
                        st.warning(f"👉 {item}")
                        
                    # ==========================================
                    # 🚀 新增功能：一鍵產生 LINE 採購單
                    # ==========================================
                    import urllib.parse
                    
                    # 1. 組合超專業的 LINE 訊息內容
                    msg_lines = [
                        "【📦 鼎極餐廳 - AI 緊急採購單】", 
                        f"📅 日期：{today.strftime('%Y/%m/%d')}",
                        "------------------------"
                    ]
                    
                    for item in alert_items:
                        # 把粗體符號 ** 拿掉，讓 LINE 顯示更乾淨
                        clean_item = item.replace("**", "")
                        msg_lines.append(f"🛒 {clean_item}")
                        
                    msg_lines.append("------------------------")
                    msg_lines.append("🤖 此訊息由 AI 倉儲系統自動生成，請協助盡速補貨！")
                    
                    # 2. 將文字轉換成 LINE 看得懂的網址格式
                    final_msg = "\n".join(msg_lines)
                    line_url = f"https://line.me/R/msg/text/?{urllib.parse.quote(final_msg)}"
                    
                    # 3. 顯示超搶眼的按鈕
                    st.write("") # 空一行比較好看
                    st.link_button("📲 一鍵傳送採購單至 LINE", line_url, type="primary", use_container_width=True)
                    # ==========================================

                else:
                    st.success("✅ 目前所有食材庫存量皆充足！無急需採購項目。")

                # 最後才顯示完整的分析表格
                # 最後才顯示完整的分析表格
                if report_data:
                    df_report = pd.DataFrame(report_data)
                    st.dataframe(df_report, use_container_width=True)
                    
                    # ==========================================
                    # 🚀 新增功能：視覺化庫存圖表
                    # ==========================================
                    st.divider() # 畫一條漂亮的分隔線
                    st.subheader("📈 庫存水位分佈圖")
                    
                    # 把「品項」設定為圖表的 X 軸標籤，並抓出「庫存」欄位畫圖
                    chart_data = df_report.set_index("品項")[["庫存"]]
                    
                    # Streamlit 超強的一行畫圖指令 (加上酷炫的顏色)
                    st.bar_chart(chart_data, color="#1f77b4")
                    # ==========================================
                    
                else: 
                    st.info("目前沒有足夠數據可分析。")
                    
            except Exception as e: 
                st.error(f"運算錯誤：{e}")

with tab2:
    st.header("📸 單據自動建檔")
    st.write("請拍攝或上傳進貨單，系統將自動讀取品項與數量。")
    
    # ✅ 改用 file_uploader，在手機上會自動喚醒原生高畫質相機！
    camera_photo = st.file_uploader("點擊這裡拍照或上傳單據", type=['jpg', 'jpeg', 'png', 'webp'])
    
    if camera_photo:
        st.image(camera_photo, caption="準備辨識的單據", use_container_width=True)
        if st.button("🧠 開始 AI 辨識並入庫", use_container_width=True):
            with st.spinner("Gemini 視覺分析中..."):
                try:
                    img = Image.open(camera_photo)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    prompt = "這是一張餐廳的單據。請幫我萃取裡面的品項與數量。嚴格輸出 JSON 陣列格式，只能包含 'product' 和 'quantity' 兩個 key，不要輸出其他任何文字。"
                    response = model.generate_content([img, prompt])
                    result_text = response.text.replace("```json", "").replace("```", "").strip()
                    items = json.loads(result_text)
                    
                    for item in items:
                        update_sheet_stock(item['product'], item['quantity'], 'IN', detail_info="AI視覺辨識建檔")
                    st.balloons()
                except Exception as e: st.error(f"❌ 辨識或寫入失敗：{e}")

with tab3:
    st.header("🎙️ 語音操作")
    st.write("請說出指令，例如：「進貨高麗菜五個」。")
    audio_file = st.audio_input("點擊錄音")
    
    if audio_file:
        if st.button("🧠 開始 AI 語音解析與建檔", use_container_width=True):
            with st.spinner("Gemini 正在聆聽並分析..."):
                try:
                    import tempfile, os
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                        tmp_file.write(audio_file.getvalue())
                        tmp_filename = tmp_file.name
                    
                    audio_upload = genai.upload_file(path=tmp_filename)
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    
                    # 🌟 亮點一：加入聰明的提示詞！
                    all_products = get_all_products()
                    prompt = f"""
                    請將這段語音轉譯成繁體中文文字。
                    這是一段餐廳倉儲人員的語音指令，請注意發音可能不標準。
                    提示：系統中已有的商品包含：{','.join(all_products)}。
                    常出現的動詞有：進貨、報廢、壞掉、過期。
                    請參考上述商品名稱進行語音校正（例如聽到發音相似的「夠綠債」，請自動校正為「高麗菜」）。
                    請嚴格只輸出最終的文字結果，不要加上任何引號或說明。
                    """
                    response = model.generate_content([audio_upload, prompt])
                    spoken_text = response.text.strip()
                    st.success(f"🗣️ AI 聽寫結果：『{spoken_text}』")
                    
                    os.remove(tmp_filename)
                    try: audio_upload.delete()
                    except: pass
                    
                    # 🌟 亮點二：呼叫您寫好的完全體解析大腦！
                    smart_parse_and_execute(spoken_text)
                    
                except Exception as e: st.error(f"❌ 語音辨識失敗：{e}")

# ================= 4. 開發者工具 =================
with st.sidebar:
    st.header("⚙️ 開發者測試區")
    st.warning("🚨 警告：這將會清空所有商品與進貨/報廢資料！")
    confirm_reset = st.checkbox("我確定要清空測試資料")
    if st.button("🗑️ 一鍵重置試算表", disabled=not confirm_reset, use_container_width=True):
        with st.spinner("正在清空資料，保留標題列..."):
            try:
                doc = connect_spreadsheet()
                doc.worksheet('工作表1').batch_clear(['A2:Z10000'])
                doc.worksheet('進貨紀錄').batch_clear(['A2:Z10000'])
                # 新增報廢紀錄清空
                try: doc.worksheet('報廢紀錄').batch_clear(['A2:Z10000'])
                except: pass
                st.success("✨ 重置成功！可以開始全新的測試了。")
            except Exception as e: st.error(f"❌ 重置失敗：{e}")
