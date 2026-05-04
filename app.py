import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import re
import google.generativeai as genai # 🌟 新增 Gemini 套件
from PIL import Image # 🌟 新增影像處理套件

# ================= 1. 版面與基本設定 =================
st.set_page_config(page_title="餐廳倉儲助手", page_icon="📦", layout="centered")

# ================= 2. 安全連線與 API 設定 =================
# 讀取 Gemini 金鑰
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

def connect_spreadsheet():
    creds_dict = json.loads(st.secrets["gcp_service_account"]["credentials"])
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client_gs = gspread.authorize(creds)
    return client_gs.open('智慧庫存系統') 

# ================= 3. 資料清洗輔助函式 =================
def extract_number(val):
    if pd.isna(val) or str(val).strip() == '':
        return 0.0
    match = re.search(r'[\d\.]+', str(val))
    return float(match.group()) if match else 0.0

# ================= 4. 建立 App 介面 =================
st.title("📦 鼎極餐廳倉儲助手")
st.markdown("歡迎使用 AI 庫存管理系統")

tab1, tab2, tab3 = st.tabs(["📊 庫存預測", "📸 單據辨識", "🎙️ 語音助理"])

# --- 分頁 1：預測報表 (維持不變) ---
with tab1:
    st.header("庫存戰情室與 AI 採購建議")
    if st.button("啟動 AI 運算 🚀", use_container_width=True):
        with st.spinner('正在從 Google Sheets 拉取資料並運算中...'):
            try:
                doc = connect_spreadsheet()
                df_stock = pd.DataFrame(doc.worksheet('工作表1').get_all_records())
                df_in = pd.DataFrame(doc.worksheet('進貨紀錄').get_all_records())

                df_stock['庫存數量'] = df_stock['庫存數量'].apply(extract_number)
                if '數量' in df_in.columns:
                    df_in['數量'] = df_in['數量'].apply(extract_number)

                df_in['日期'] = pd.to_datetime(df_in['日期'])
                today = datetime.now()
                report_data = []

                for index, row in df_stock.iterrows():
                    product = str(row.get('商品名稱', ''))
                    current_stock = row.get('庫存數量', 0.0)
                    if not product: continue

                    product_in = df_in[df_in['商品名稱'] == product]
                    if product_in.empty: continue

                    days_tracked = max(1, (today - product_in['日期'].min()).days)
                    total_consumed = max(0, product_in['數量'].sum() - current_stock)
                    daily_burn_rate = total_consumed / days_tracked
                    days_remaining = current_stock / daily_burn_rate if daily_burn_rate > 0 else 999

                    suggestion = "✅ 安全"
                    if days_remaining <= 3: suggestion = "🚨 立即叫貨"
                    elif days_remaining <= 7: suggestion = "⚠️ 即將見底"

                    report_data.append({
                        "品項": product,
                        "日耗/天": f"{daily_burn_rate:.1f}",
                        "剩餘天數": f"{int(days_remaining)}天" if days_remaining != 999 else "極少",
                        "建議": suggestion
                    })

                if report_data:
                    st.success("✅ 運算完成！")
                    st.dataframe(pd.DataFrame(report_data), use_container_width=True)
                else:
                    st.info("目前沒有足夠數據可分析。")
            except Exception as e:
                st.error(f"連線或運算發生錯誤：{e}")

# --- 分頁 2：影像辨識 (🌟 全新裝上 Gemini 大腦) ---
with tab2:
    st.header("📸 單據自動建檔")
    st.write("請對準進貨單拍照，系統將自動萃取品項與數量寫入雲端。")
    
    camera_photo = st.camera_input("拍攝進貨單")
    
    if camera_photo:
        st.image(camera_photo, caption="準備辨識的單據", use_container_width=True)
        
        if st.button("🧠 開始 AI 辨識並入庫", use_container_width=True):
            with st.spinner("Gemini 視覺大腦分析中..."):
                try:
                    # 1. 讀取照片
                    img = Image.open(camera_photo)
                    
                    # 2. 呼叫 Gemini 2.5 Flash
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    prompt = """
                    這是一張餐廳的單據或盤點表。請幫我萃取裡面的品項與數量。
                    嚴格輸出 JSON 陣列格式，只能包含 'product' 和 'quantity' 兩個 key，不要輸出其他任何文字。
                    """
                    response = model.generate_content([img, prompt])
                    
                    # 3. 解析 JSON
                    result_text = response.text.replace("```json", "").replace("```", "").strip()
                    items = json.loads(result_text)
                    
                    # 4. 寫入 Google Sheets (進貨紀錄表)
                    doc = connect_spreadsheet()
                    in_log_sheet = doc.worksheet('進貨紀錄')
                    today_str = datetime.now().strftime("%Y/%m/%d")
                    
                    for item in items:
                        product_name = item['product']
                        qty = item['quantity']
                        # 依序寫入：日期, 來源(填寫AI辨識), 商品名稱, 數量
                        in_log_sheet.append_row([today_str, "AI 視覺單據", product_name, qty])
                        st.success(f"✅ 已成功建檔：{product_name} (數量: {qty})")
                        
                    st.balloons() # 飄氣球慶祝
                    
                except Exception as e:
                    st.error(f"❌ 辨識或寫入失敗：{e}")

# --- 分頁 3：語音辨識 (保留外觀，下一步處理) ---
with tab3:
    st.header("🎙️ 語音操作")
    st.info("即將串接 Whisper 語音模型...")
    audio_file = st.audio_input("點擊錄音")
    if audio_file:
        st.success("錄音已擷取！準備開發中...")
# ================= 5. 開發者工具：一鍵重置 (側邊欄) =================
with st.sidebar:
    st.header("⚙️ 開發者測試區")
    st.warning("🚨 警告：這將會清空所有商品與進貨資料！")
    
    # 加上防呆機制：必須打勾，按鈕才能按
    confirm_reset = st.checkbox("我確定要清空測試資料")
    
    # disabled=not confirm_reset 代表：如果沒打勾，按鈕就會反灰不能按
    if st.button("🗑️ 一鍵重置試算表", disabled=not confirm_reset, use_container_width=True):
        with st.spinner("正在清空資料，保留標題列..."):
            try:
                doc = connect_spreadsheet()
                
                # 使用 batch_clear 範圍清空：
                # 只清空 A2 到 Z10000 的範圍，這樣就能完美保留第 1 列的「標題」與格式！
                doc.worksheet('工作表1').batch_clear(['A2:Z10000'])
                doc.worksheet('進貨紀錄').batch_clear(['A2:Z10000'])
                
                st.success("✨ 重置成功！試算表已恢復乾淨狀態，可以開始全新的測試了。")
            except Exception as e:
                st.error(f"❌ 重置失敗：{e}")
