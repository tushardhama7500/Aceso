import { useEffect, useRef, useState } from "react";
import { sendMessage, listAppointments } from "./api.js";

const WELCOME = {
  role: "assistant",
  content:
    "Hello, I'm Aceso. Tell me what you've been experiencing, and I'll help find the right department and get you booked in.",
};

export default function App() {
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [appointments, setAppointments] = useState([]);
  const scrollRef = useRef(null);

  useEffect(() => {
    refreshAppointments();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function refreshAppointments() {
    try {
      const data = await listAppointments();
      setAppointments(data);
    } catch (err) {
      // Non-fatal — the appointments panel is a convenience view.
      console.error(err);
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await sendMessage(conversationId, text);
      setConversationId(res.conversation_id);
      setMessages((prev) => [...prev, { role: "assistant", content: res.message }]);
      refreshAppointments();
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Something went wrong reaching Aceso. Please try again." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">Aceso</span>
          <span className="brand-tagline">Intelligent pathways to care.</span>
        </div>
      </header>

      <main className="app-main">
        <section className="chat-panel">
          <div className="chat-scroll" ref={scrollRef}>
            {messages.map((m, i) => (
              <div key={i} className={`bubble-row ${m.role}`}>
                <div className={`bubble ${m.role}`}>{m.content}</div>
              </div>
            ))}
            {loading && (
              <div className="bubble-row assistant">
                <div className="bubble assistant typing">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                </div>
              </div>
            )}
          </div>

          <form className="chat-input-row" onSubmit={handleSend}>
            <input
              className="chat-input"
              type="text"
              placeholder="Describe what you're experiencing…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={loading}
            />
            <button className="send-button" type="submit" disabled={loading || !input.trim()}>
              Send
            </button>
          </form>
        </section>

        <aside className="appointments-panel">
          <h2>Appointments</h2>
          {appointments.length === 0 ? (
            <p className="empty-state">No appointments booked yet.</p>
          ) : (
            <ul className="appointment-list">
              {appointments.map((a) => (
                <li key={a.appointment_id} className="appointment-card">
                  <div className="appointment-department">{a.department}</div>
                  <div className="appointment-meta">{a.patient_name}</div>
                  <div className="appointment-meta">{a.visit_date}</div>
                  <div className="appointment-id">{a.appointment_id}</div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </main>
    </div>
  );
}
