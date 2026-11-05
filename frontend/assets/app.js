const API_BASE = "/api";

const TOKEN_KEY = "tunix_access_token";
const REFRESH_KEY = "tunix_refresh_token";
const USER_KEY = "tunix_user";

function applyTheme() {
  const mode = localStorage.getItem("tunix_theme") || "light";
  document.body.classList.toggle("dark", mode === "dark");
}

function toggleTheme() {
  const current = localStorage.getItem("tunix_theme") || "light";
  localStorage.setItem("tunix_theme", current === "light" ? "dark" : "light");
  applyTheme();
}

function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY) || "";
}

function setTokens(accessToken, refreshToken, user) {
  if (accessToken) localStorage.setItem(TOKEN_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

function getCachedUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

let _refreshPromise = null;
async function refreshAuth() {
  // Nhiều request cùng nhận 401 → dùng chung 1 lần refresh (tránh xoay token trùng)
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    const refresh = getRefreshToken();
    if (!refresh) return false;
    try {
      const res = await fetch(`${API_BASE}/auth/refresh-token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (!data.access_token) return false;
      setTokens(data.access_token, data.refresh_token, data.user);
      return true;
    } catch {
      return false;
    }
  })().finally(() => {
    _refreshPromise = null;
  });
  return _refreshPromise;
}

async function api(path, method = "GET", body = null, auth = false, retried = false) {
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    headers.Authorization = `Bearer ${getToken()}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });

  if (!res.ok) {
    // Token hết hạn/không hợp lệ → refresh một lần rồi thử lại
    if (res.status === 401 && auth && !retried) {
      const ok = await refreshAuth();
      if (ok) {
        return api(path, method, body, true, true);
      }
      clearTokens();
    }
    let detail = "API error";
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      detail = (await res.text()) || "API error";
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  return null;
}

/** Lấy user hiện tại từ backend (nếu có token hợp lệ), ngược lại trả null. */
async function getCurrentUser() {
  if (!getToken()) return null;
  try {
    const user = await api("/auth/me", "GET", null, true);
    setTokens(null, null, user);
    return user;
  } catch {
    return null;
  }
}

/** Đăng xuất: gọi backend thu hồi token + xoá localStorage. */
async function logout() {
  const refresh = getRefreshToken();
  try {
    await api("/auth/logout", "POST", { refresh_token: refresh || null }, true);
  } catch {
    /* bỏ qua lỗi mạng */
  }
  clearTokens();
}

async function loadIndices() {
  const wrap = document.getElementById("indices");
  if (!wrap) return;
  const data = await api("/stocks/indices");
  wrap.innerHTML = data
    .map((i) => `<div class="card"><h3>${i.symbol}</h3><p>${i.value}</p><p class="muted">${i.change}</p></div>`)
    .join("");
}

async function searchStocks() {
  const q = document.getElementById("q").value.trim();
  const out = document.getElementById("searchResult");
  if (!q || !out) return;
  const data = await api(`/stocks/search?q=${encodeURIComponent(q)}`);
  out.innerHTML = data
    .map((s) => `<li><a href="StockDetail.html?symbol=${s.symbol}">${s.symbol}</a> - ${s.name}</li>`)
    .join("");
}

async function loadQuote() {
  const view = document.getElementById("quoteView");
  if (!view) return;

  const params = new URLSearchParams(window.location.search);
  const symbol = params.get("symbol") || "VIC";
  const quote = await api(`/stocks/${symbol}/quote`);
  const history = await api(`/stocks/${symbol}/history`);

  view.innerHTML = `
    <div class="card">
      <h2>${quote.symbol}</h2>
      <p>Price: ${quote.price}</p>
      <p>High: ${quote.high || 0} | Low: ${quote.low || 0}</p>
      <p class="muted">Source: ${quote.source}</p>
    </div>
    <div class="card">
      <h3>Lich su gia (${history.length} ban ghi)</h3>
      <pre>${JSON.stringify(history.slice(-10), null, 2)}</pre>
    </div>
  `;
}

async function submitLogin(event) {
  event.preventDefault();
  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  const data = await api("/auth/login", "POST", { email, password });
  setTokens(data.access_token, data.refresh_token, data.user);
  window.location.href = "index.html";
}

async function loadAdminStats() {
  const out = document.getElementById("adminStats");
  if (!out) return;
  try {
    const stats = await api("/dashboard/stats", "GET", null, true);
    out.innerHTML = `<pre>${JSON.stringify(stats, null, 2)}</pre>`;
  } catch (err) {
    out.innerHTML = `<p class="muted">Khong tai duoc dashboard. Can token admin/super-admin.</p>`;
  }
}

window.VinaFintech = {
  api,
  getToken,
  getRefreshToken,
  setTokens,
  clearTokens,
  getCachedUser,
  getCurrentUser,
  logout,
  loadIndices,
  searchStocks,
  loadQuote,
  submitLogin,
  loadAdminStats,
  toggleTheme,
  applyTheme,
};

window.addEventListener("load", applyTheme);
