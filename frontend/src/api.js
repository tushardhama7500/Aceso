const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

// Token lives in localStorage only for this take-home's scope — see README
// "Security considerations" for why a real deployment would want an
// httpOnly cookie instead (localStorage is readable by any script on the
// page, i.e. vulnerable to XSS-driven token theft).
const TOKEN_KEY = "aceso_token";
const USER_KEY = "aceso_user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

function storeSession(data) {
  localStorage.setItem(TOKEN_KEY, data.access_token);
  localStorage.setItem(USER_KEY, JSON.stringify(data.user));
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

function authHeaders() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseErrorMessage(res) {
  try {
    const body = await res.json();
    return body.detail || `Request failed: ${res.status}`;
  } catch {
    return `Request failed: ${res.status}`;
  }
}

export async function register(email, password) {
  const res = await fetch(`${API_BASE_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await parseErrorMessage(res));
  }
  const data = await res.json();
  storeSession(data);
  return data.user;
}

export async function login(email, password) {
  const res = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new Error(await parseErrorMessage(res));
  }
  const data = await res.json();
  storeSession(data);
  return data.user;
}

export async function sendMessage(conversationId, message) {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });
  if (res.status === 401) {
    logout();
    throw new Error("Session expired. Please log in again.");
  }
  if (!res.ok) {
    throw new Error(await parseErrorMessage(res));
  }
  return res.json();
}

async function authedGet(path) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { ...authHeaders() },
  });
  if (res.status === 401) {
    logout();
    throw new Error("Session expired. Please log in again.");
  }
  if (!res.ok) {
    throw new Error(await parseErrorMessage(res));
  }
  return res.json();
}

export async function listAppointments() {
  return authedGet("/appointments");
}

export async function listConversations() {
  return authedGet("/conversations");
}

export async function getConversationMessages(conversationId) {
  return authedGet(`/conversations/${conversationId}/messages`);
}
