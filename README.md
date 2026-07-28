# integratedAPKnIoT

Apionix 整合入口網站，提供 APK 安全分析與 IoT 裝置管理兩個產品入口。

## APK Analysis Platform 連線

首頁的「進入 APK 分析系統」會直接開啟真正的 APK 分析前端，不經過中間介紹頁。舊的 `apk-system.html` 網址仍保留為自動轉址，避免既有書籤失效。

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

執行 `share-public.bat`。腳本會透過 Cloudflare Quick Tunnel 建立臨時 HTTPS 網址，並在完成後顯示可傳給組員的 Apionix 入口網址。測試期間電腦與腳本啟動的服務必須保持運作；完成測試後執行 `stop-public.bat` 關閉所有公開入口。

公開測試網址沒有固定網址或正常運作時間保證，請勿用於正式環境，也不要上傳機密 APK。

### 完整 APK 分析模式

請確認本專案與 `apk-analysis-platform` 位於同一個上層目錄，接著執行：

```powershell
.\start-local.bat
```

腳本會啟動 Apionix 封面（8080）、APK 前端（5173）與 FastAPI（8000）。本機模式使用 Celery eager 執行分析工作，因此不需要另外啟動 Redis；正式部署仍使用 Celery worker 與 Redis。

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
- `config.js`：外部系統網址設定
- `styles.css`：共用視覺樣式
- `script.js`：動畫與系統連結初始化
- `assets/`：品牌及產品圖片
