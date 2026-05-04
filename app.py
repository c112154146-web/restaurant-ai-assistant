import streamlit as st
import pandas as pd

# 1. 設定網頁標題與手機版面配置
st.set_page_config(page_title="餐廳倉儲助手", page_icon="📦", layout="centered")

st.title("📦 餐廳倉儲助手")
st.markdown("歡迎使用多模態 AI 倉儲管理系統")

# 2. 建立手機 App 的三個直覺化分頁
tab1, tab2, tab3 = st.tabs(["📊 預測報表", "📸 拍照進貨", "🎙️ 語音操作"])

# --- 分頁 1：戰情儀表板 ---
with tab1:
    st.header("📊 庫存戰情室與採購建議")
    st.info("這裡稍後會接上您的 Pandas AI 預測演算法，直接在手機顯示精美報表！")
    
    # 放個假按鈕預留位置
    if st.button("🔄 立即重新運算耗損率"):
        st.success("雲端運算中...")

# --- 分頁 2：視覺多模態進貨 ---
with tab2:
    st.header("📸 影像 OCR 自動建檔")
    st.write("請對準進貨單或食材拍照，系統將自動萃取品項與數量。")
    
    # 🌟 神奇的 Streamlit 相機元件：在手機上按下去會直接打開相機！
    camera_photo = st.camera_input("開啟相機拍攝")
    
    if camera_photo:
        st.success("📸 照片已拍攝！準備傳送給 Gemini 2.5 Flash 大腦...")
        st.image(camera_photo, caption="準備辨識的單據", use_container_width=True)

# --- 分頁 3：語音進貨與報廢 ---
with tab3:
    st.header("🎙️ 語音助理更新庫存")
    st.write("請點擊下方麥克風，直接說出例如：「高麗菜進貨五個」。")
    
    # 🌟 神奇的 Streamlit 錄音元件 (需較新版本支援)
    audio_file = st.audio_input("點擊錄音")
    
    if audio_file:
        st.success("🎤 錄音完成！準備傳送給 Whisper 模型解析...")
