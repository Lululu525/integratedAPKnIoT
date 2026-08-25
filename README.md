# integratedAPKnIoT

Apionix 整合入口網站，提供 APK 安全分析與 IoT 裝置管理兩個產品入口。兩套來源系統維持各自的 GitHub 儲存庫與前後端架構，本專案僅負責一致的導航、服務切換與啟動流程。

## 整合操作流程

APK 與 IoT 入口都會進入 `system.html` 共用操作工作區，並在其中載入兩個來源專案的真正前端。IoT 工作區會保留安全登入，同時補充中文操作說明，避免使用者突然被帶到缺乏脈絡的登入頁。共用工作區提供：

- 在 APK 安全分析與 IoT 裝置管理之間切換
- 返回 Apionix 首頁
- 重新載入目前系統
- 在新分頁開啟原始系統
- 在服務尚未啟動時看到明確的修復提示

IoT 的帳號與密碼仍由 IoT 後端驗證；整合入口不會儲存登入密碼，也不會繞過原系統的權限控管。

## APK Analysis Platform 連線

首頁的「進入 APK 分析系統」會在共用操作殼層中載入真正的 APK 分析前端。舊的 `apk-system.html` 網址仍保留，避免既有書籤失效。

預設開發環境網址：

```text
http://127.0.0.1:5173
```

部署 APK 前端後，請修改 `config.js`：

```js
window.APIONIX_CONFIG = Object.freeze({
  apkFrontendUrl: "https://your-apk-frontend.example.com",
});
```

## 本機預覽

### 分享公開測試網址

執行 `share-public.bat`。腳本會建置最新 APK 與 IoT 前端、啟動兩套 API，再透過 Cloudflare Quick Tunnel 建立臨時 HTTPS 網址，並在完成後顯示可傳給組員的單一 Apionix 入口網址。測試期間電腦與腳本啟動的服務必須保持運作；完成測試後執行 `stop-public.bat` 關閉所有公開入口。

公開測試網址沒有固定網址或正常運作時間保證，請勿用於正式環境，也不要上傳機密 APK。

網站透過 `serve_static.py` 提供正確的 JavaScript MIME 類型，避免 Windows 將 Vite 模組誤判為 `text/plain`。

### 完整 APK 分析模式

請確認本專案與 `apk-analysis-platform` 位於同一個上層目錄，接著執行：

```powershell
.\start-local.bat
```

腳本會一次啟動：

- Apionix 整合入口：8080
- APK 前端：5173
- APK FastAPI：8000
- IoT 前端：5180
- IoT FastAPI：8100

啟動完成後會自動開啟整合首頁。請保持 `start-local.bat` 的命令視窗開啟；關閉視窗或按下 `Ctrl+C` 會停止由它啟動的服務。啟動器會檢查各服務是否成功回應，避免入口已開啟但 IoT 前後端其實尚未運行。

本機 APK 模式使用 Celery eager 執行分析工作，因此不需要另外啟動 Redis；正式部署仍使用 Celery worker 與 Redis。IoT 系統沿用其來源專案的資料與驗證設定。

### 只預覽封面

在本專案目錄執行：

```powershell
python -m http.server 8080
```

瀏覽器開啟 `http://127.0.0.1:8080`。若要使用 APK 分析功能，請另行啟動 `apk-analysis-platform/FrontendUI`（預設為 `http://127.0.0.1:5173`），以及其 FastAPI、Celery、Redis 與 AI-model 服務。

## 專案結構

- `index.html`：Apionix 封面與產品介紹
- `apk-system.html`：舊網址相容用的自動轉址頁
- `iot-system.html`：IoT 系統入口
- `system.html`：APK／IoT 共用操作殼層
- `workspace.js`：服務切換、載入狀態與錯誤處理
- `run_integrated.py`：啟動、檢查並持續監看完整本機服務
- `config.js`：外部系統網址設定
- `styles.css`：共用視覺樣式
- `script.js`：動畫與系統連結初始化
- `assets/`：品牌及產品圖片
