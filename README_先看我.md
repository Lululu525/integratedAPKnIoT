# Apionix UI

## APK Analysis Platform 連線設定

首頁的「進入 APK 分析系統」會先開啟 `apk-system.html`，再由入口按鈕連到真正的 APK Analysis Platform 前端。

開發環境預設網址：

```text
http://127.0.0.1:5173
```

若前端部署到其他網址，只需要修改 `config.js`：

```js
window.APIONIX_CONFIG = Object.freeze({
  apkFrontendUrl: "https://your-apk-frontend.example.com",
});
```

入口頁不會顯示示意分析結果，所有上傳、分析、結果與 PDF 下載都由真正的 APK Analysis Platform 處理。


## 本版更新

- 已補回滾動時文字與區塊動態出現效果。
- Hero 標題會分段浮現。
- 區塊、圖片、服務卡、使用流程與頁尾會在滾動到畫面時淡入。
- 導覽列加入進場動畫。


## 本次微調
- 已將 APK 與 IoT 標題字體放大。
- 已將 APK 與 IoT 文字調整為置中顯示。


## 本版更新

- 已新增「進入 APK 分析系統」按鈕。
- 已新增「進入 IoT 管理系統」按鈕。
- 新增 `apk-system.html` 作為 APK 分析系統入口頁。
- 新增 `iot-system.html` 作為 IoT 韌體與裝置管理系統入口頁。
- 首頁兩大核心服務與使用流程區都可以進入兩邊系統。
