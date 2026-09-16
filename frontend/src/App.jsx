import { useEffect, useState } from "react";
import {
  sendMessage,
  listAppointments,
  listConversations,
  getConversationMessages,
  register,
  login,
  logout,
  getToken,
  getStoredUser,
} from "./api.js";
import { IconLeaf } from "./icons.jsx";
import Sidebar from "./components/Sidebar.jsx";
import TopBar from "./components/TopBar.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import ChatHistoryRail from "./components/ChatHistoryRail.jsx";
import RightPanel from "./components/RightPanel.jsx";
import AppointmentsView from "./components/AppointmentsView.jsx";
import ProfileView from "./components/ProfileView.jsx";
import HelpView from "./components/HelpView.jsx";
import DepartmentsView from "./components/DepartmentsView.jsx";

const WELCOME = {
  role: "assistant",
  content:
    "Hello, I'm Aceso 👋\n\nTell me what you've been experiencing, and I'll help find the right department and get you booked in.\n\nYou can describe symptoms, ask questions, or even mention multiple concerns — I'll handle the rest.",
};

export default function App() {
  const [user, setUser] = useState(() => (getToken() ? getStoredUser() : null));

  if (!user) {
    return <AuthScreen onAuthenticated={setUser} />;
  }
  return <Dashboard user={user} onLogout={() => { logout(); setUser(null); }} />;
}

function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const authedUser = mode === "login" ? await login(email, password) : await register(email, password);
      onAuthenticated(authedUser);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="brand auth-brand">
          <span className="brand-mark">
            <IconLeaf className="brand-icon" /> Aceso
          </span>
          <span className="brand-tagline">Intelligent pathways to care.</span>
        </div>

        <h1 className="auth-title">{mode === "login" ? "Log in" : "Create an account"}</h1>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label className="auth-label">
            Email
            <input
              className="auth-input"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
          <label className="auth-label">
            Password
            <input
              className="auth-input"
              type="password"
              required
              minLength={mode === "register" ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </label>

          {error && <div className="auth-error">{error}</div>}

          <button className="send-button auth-submit" type="submit" disabled={loading}>
            {loading ? "Please wait…" : mode === "login" ? "Log in" : "Register"}
          </button>
        </form>

        <button
          className="auth-toggle"
          type="button"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
        </button>
      </div>
    </div>
  );
}

function Dashboard({ user, onLogout }) {
  const [activeTab, setActiveTab] = useState("chat");
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [appointments, setAppointments] = useState([]);
  const [conversations, setConversations] = useState([]);

  useEffect(() => {
    refreshAppointments();
    refreshConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshAppointments() {
    try {
      const data = await listAppointments();
      setAppointments(data);
    } catch (err) {
      if (err.message.includes("Session expired")) {
        onLogout();
        return;
      }
      console.error(err);
    }
  }

  async function refreshConversations() {
    try {
      const data = await listConversations();
      setConversations(data);
    } catch (err) {
      if (err.message.includes("Session expired")) {
        onLogout();
        return;
      }
      console.error(err);
    }
  }

  async function submitMessage(text) {
    if (!text || loading) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await sendMessage(conversationId, text);
      setConversationId(res.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", content: res.message }]);
      refreshAppointments();
      refreshConversations();
    } catch (err) {
      if (err.message.includes("Session expired")) {
        onLogout();
        return;
      }
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Something went wrong reaching Aceso. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function handleSelectConversation(id) {
    if (id === conversationId || loading) return;
    setLoading(true);
    try {
      const history = await getConversationMessages(id);
      setConversationId(id);
      setMessages(history.length > 0 ? history.map((m) => ({ role: m.role, content: m.content })) : [WELCOME]);
      setActiveTab("chat");
    } catch (err) {
      if (err.message.includes("Session expired")) {
        onLogout();
        return;
      }
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  function handleSend(e) {
    e.preventDefault();
    submitMessage(input.trim());
  }

  function handleQuickReply(text) {
    submitMessage(text);
  }

  function handleClearChat() {
    setConversationId(null);
    setMessages([WELCOME]);
  }

  function handleSelectDepartment(department) {
    setActiveTab("chat");
    submitMessage(`I'd like to book an appointment with ${department}.`);
  }

  return (
    <div className="dashboard">
      <TopBar user={user} onSelectTab={setActiveTab} onLogout={onLogout} />

      <div className="dashboard-body">
        <Sidebar activeTab={activeTab} onSelectTab={setActiveTab} />

        <div className="dashboard-content">
          <main className="main-panel">
            {activeTab === "chat" && (
              <div className="chat-layout">
                <ChatHistoryRail
                  conversations={conversations}
                  activeConversationId={conversationId}
                  onSelectConversation={handleSelectConversation}
                  onNewChat={handleClearChat}
                />
                <ChatPanel
                  messages={messages}
                  loading={loading}
                  input={input}
                  setInput={setInput}
                  onSend={handleSend}
                  onQuickReply={handleQuickReply}
                  onClear={handleClearChat}
                />
              </div>
            )}
            {activeTab === "appointments" && <AppointmentsView appointments={appointments} />}
            {activeTab === "profile" && <ProfileView user={user} onLogout={onLogout} />}
            {activeTab === "help" && <HelpView />}
            {activeTab === "departments" && <DepartmentsView onSelectDepartment={handleSelectDepartment} />}
          </main>

          <RightPanel onSelectTab={setActiveTab} />
        </div>
      </div>
    </div>
  );
}
