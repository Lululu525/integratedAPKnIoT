# integratedAPKnIoT

Apionix 整合入口網站，提供 APK 安全分析與 IoT 裝置管理兩個產品入口。

## APK Analysis Platform 連線

首頁的「進入 APK 分析系統」會先進入 `apk-system.html`。使用者點擊「開啟 APK Analysis Platform」後，網站會開啟真正的 APK 分析前端；入口頁不會產生或顯示虛構分析結果。

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

在本專案目錄執行：

```powershell
python -m http.server 8080
```

瀏覽器開啟 `http://127.0.0.1:8080`。若要使用 APK 分析功能，請另行啟動 `apk-analysis-platform/FrontendUI`（預設為 `http://127.0.0.1:5173`），以及其 FastAPI、Celery、Redis 與 AI-model 服務。

## 專案結構

- `index.html`：Apionix 封面與產品介紹
- `apk-system.html`：APK Analysis Platform 入口
- `iot-system.html`：IoT 系統入口
- `config.js`：外部系統網址設定
- `styles.css`：共用視覺樣式
- `script.js`：動畫與系統連結初始化
- `assets/`：品牌及產品圖片

