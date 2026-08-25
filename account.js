(() => {
  const TOKEN_KEY = "apionix.account.token";
  const token = () => localStorage.getItem(TOKEN_KEY);
  const headers = () => token() ? { Authorization: `Bearer ${token()}` } : {};

  async function request(path, options = {}) {
    const response = await fetch(`/account-api${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...headers(), ...(options.headers || {}) }
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "服務暫時無法使用");
    return body;
  }

  async function renderAccountActions() {
    const containers = document.querySelectorAll("[data-account-actions]");
    if (!containers.length) return;
    let user = null;
    if (token()) {
      try {
        user = (await request("/auth/me")).user;
      } catch {
        localStorage.removeItem(TOKEN_KEY);
      }
    }
    containers.forEach((container) => {
      if (user) {
        container.innerHTML = `
          <a class="account-user-link" href="user.html" aria-label="開啟使用者頁面">
            <span class="account-avatar">${user.username.slice(0, 1).toUpperCase()}</span>
            <span>${user.username}</span>
          </a>
          <button class="btn btn-line account-logout" type="button">登出</button>`;
        container.querySelector(".account-logout").addEventListener("click", async () => {
          try { await request("/auth/logout", { method: "POST" }); } catch {}
          localStorage.removeItem(TOKEN_KEY);
          location.href = "index.html";
        });
      } else {
        container.innerHTML = `
          <a class="btn btn-line" href="auth.html?mode=register">註冊</a>
          <a class="btn btn-blue" href="auth.html?mode=login">登入</a>`;
      }
    });
  }

  async function recordActivity(service, action = "開啟服務") {
    if (!token()) return;
    try {
      await request("/activity", {
        method: "POST",
        body: JSON.stringify({ service, action })
      });
    } catch {}
  }

  window.APIONIX_ACCOUNT = { TOKEN_KEY, request, renderAccountActions, recordActivity };
  document.addEventListener("DOMContentLoaded", renderAccountActions);
})();
