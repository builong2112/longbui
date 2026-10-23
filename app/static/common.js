// Shared helpers for all pages.
const STATUS_VI = {
  processing: "Đang xử lý",
  pending_review: "Chờ duyệt",
  approved: "Đã duyệt",
  rejected: "Từ chối",
  error: "Lỗi",
};
const OVERALL_VI = { pass: "Đạt", fail: "Không đạt", "n/a": "" };

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  // index.html shows the login form itself, so only redirect from other pages.
  if (res.status === 401 && !location.pathname.endsWith("/index.html")) {
    location.href = "/static/index.html";
    throw new Error("Chưa đăng nhập");
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error((data && data.detail) || `Lỗi ${res.status}`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function badge(status, text) {
  return `<span class="badge ${esc(status)}">${esc(text ?? STATUS_VI[status] ?? status)}</span>`;
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

function showMsg(el, text, kind = "error") {
  el.className = `msg ${kind}`;
  el.textContent = text;
  el.classList.remove("hidden");
}

async function renderUser(el) {
  try {
    const me = await api("/api/me");
    el.innerHTML = `${esc(me.full_name)} · <a href="#" id="logout">Đăng xuất</a>`;
    el.querySelector("#logout").onclick = async (e) => {
      e.preventDefault();
      await api("/api/logout", { method: "POST" });
      location.href = "/static/index.html";
    };
    return me;
  } catch {
    return null;
  }
}
