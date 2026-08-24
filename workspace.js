document.addEventListener("DOMContentLoaded", () => {
  const services = {
    apk: {
      title: "APK 安全分析",
      frameTitle: "APK 安全分析系統",
      url: window.APIONIX_CONFIG?.apkFrontendUrl,
      accentClass: "workspace-apk",
      unavailable: "APK 分析服務尚未啟動。請確認 APK 前端與 FastAPI 已開始運行。"
    },
    iot: {
      title: "IoT 韌體與裝置管理",
      frameTitle: "IoT 韌體與裝置管理系統",
      url: window.APIONIX_CONFIG?.iotSystemUrl,
      accentClass: "workspace-iot",
      unavailable: "IoT 管理服務尚未啟動。請確認 IoT 前端與 FastAPI 已開始運行。"
    }
  };

  const params = new URLSearchParams(window.location.search);
  const serviceKey = services[params.get("service")] ? params.get("service") : "apk";
  const service = services[serviceKey];
  const frame = document.querySelector("#serviceFrame");
  const status = document.querySelector("#workspaceStatus");
  const statusText = document.querySelector("#workspaceStatusText");
  const errorPanel = document.querySelector("#workspaceError");
  const errorText = document.querySelector("#workspaceErrorText");
  const openExternal = document.querySelector("#openExternal");
  let loadTimer;

  document.body.classList.add(service.accentClass);
  document.querySelector("#workspaceTitle").textContent = service.title;
  document.title = `${service.title}｜Apionix`;
  frame.title = service.frameTitle;

  document.querySelectorAll("[data-service-link]").forEach((link) => {
    const active = link.dataset.serviceLink === serviceKey;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
  });

  function showError(message) {
    window.clearTimeout(loadTimer);
    status.hidden = true;
    frame.hidden = true;
    errorText.textContent = message;
    errorPanel.hidden = false;
  }

  function loadService() {
    errorPanel.hidden = true;
    frame.hidden = false;
    status.hidden = false;
    statusText.textContent = `正在開啟${service.title}。`;

    if (!service.url) {
      showError(service.unavailable);
      return;
    }

    openExternal.href = service.url;
    frame.src = service.url;
    loadTimer = window.setTimeout(() => {
      showError(`${service.unavailable} 若服務已啟動，請按「重新連線」。`);
    }, 15000);
  }

  frame.addEventListener("load", () => {
    window.clearTimeout(loadTimer);
    status.hidden = true;
    errorPanel.hidden = true;
    frame.hidden = false;
  });

  frame.addEventListener("error", () => showError(service.unavailable));
  document.querySelector("#reloadService").addEventListener("click", loadService);
  document.querySelector("#retryService").addEventListener("click", loadService);

  loadService();
});
