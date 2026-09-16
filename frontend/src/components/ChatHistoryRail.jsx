import { IconChat, IconPlus } from "../icons.jsx";

export default function ChatHistoryRail({ conversations, activeConversationId, onSelectConversation, onNewChat }) {
  return (
    <aside className="chat-history-rail">
      <button type="button" className="new-chat-button" onClick={onNewChat}>
        <IconPlus /> New Chat
      </button>

      <div className="chat-history-list">
        {conversations.length === 0 ? (
          <p className="empty-state chat-history-empty">Your past conversations will show up here.</p>
        ) : (
          conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`chat-history-item ${c.id === activeConversationId ? "active" : ""}`}
              onClick={() => onSelectConversation(c.id)}
            >
              <IconChat className="chat-history-icon" />
              <span className="chat-history-title">{c.title}</span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
