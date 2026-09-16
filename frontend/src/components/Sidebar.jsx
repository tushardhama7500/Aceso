import { IconCalendar, IconChat, IconHelp, IconLeaf, IconUser } from "../icons.jsx";

const NAV_ITEMS = [
  { id: "chat", label: "Chat Assistant", icon: IconChat },
  { id: "appointments", label: "Appointments", icon: IconCalendar },
  { id: "profile", label: "Profile", icon: IconUser },
  { id: "help", label: "Help & Support", icon: IconHelp },
];

export default function Sidebar({ activeTab, onSelectTab }) {
  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={`sidebar-nav-item ${activeTab === id ? "active" : ""}`}
            onClick={() => onSelectTab(id)}
          >
            <Icon />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-promo">
        <div className="sidebar-promo-icon">
          <IconLeaf />
        </div>
        <h3>Better healthcare starts with you</h3>
        <p>Describe your symptoms and let Aceso guide you to the right care.</p>
        <span className="sidebar-promo-bar" />
      </div>
    </aside>
  );
}
