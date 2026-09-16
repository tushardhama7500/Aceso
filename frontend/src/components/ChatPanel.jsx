import { useEffect, useRef } from "react";
import { IconChat, IconPaperclip, IconSend, IconTrash } from "../icons.jsx";

const QUICK_REPLIES = ["I have a headache", "I've been feeling anxious", "Stomach pain", "Ear ringing"];

export default function ChatPanel({ messages, loading, input, setInput, onSend, onQuickReply, onClear }) {
  const scrollRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const showQuickReplies = messages.length === 1;

  return (
    <section className="chat-panel">
      <div className="chat-panel-header">
        <div className="chat-panel-heading">
          <div className="bot-avatar">
            <IconChat />
          </div>
          <div>
            <div className="chat-panel-title">
              Aceso <span className="online-badge">● Online</span>
            </div>
            <div className="chat-panel-subtitle">Your AI healthcare assistant</div>
          </div>
        </div>
        <button className="ghost-button" type="button" onClick={onClear}>
          <IconTrash /> Clear Chat
        </button>
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {messages.map((m, i) => (
          <div key={i} className={`bubble-row ${m.role}`}>
            {m.role === "assistant" && (
              <div className="bubble-avatar">
                <IconChat />
              </div>
            )}
            <div className={`bubble ${m.role}`}>{m.content}</div>
          </div>
        ))}

        {showQuickReplies && (
          <div className="quick-replies">
            {QUICK_REPLIES.map((text) => (
              <button key={text} type="button" className="quick-reply-chip" onClick={() => onQuickReply(text)}>
                {text}
              </button>
            ))}
          </div>
        )}

        {loading && (
          <div className="bubble-row assistant">
            <div className="bubble-avatar">
              <IconChat />
            </div>
            <div className="bubble assistant typing">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
            </div>
          </div>
        )}
      </div>

      <form className="chat-input-row" onSubmit={onSend}>
        <button type="button" className="attach-button" tabIndex={-1} aria-hidden="true">
          <IconPaperclip />
        </button>
        <input
          ref={inputRef}
          className="chat-input"
          type="text"
          placeholder="Describe what you're experiencing…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button className="send-button" type="submit" disabled={loading || !input.trim()}>
          <IconSend /> Send
        </button>
      </form>
      <div className="chat-input-hint">You can mention multiple symptoms in one message.</div>
    </section>
  );
}
