import { IconBuilding, IconCalendar, IconChevronRight, IconHelp, IconShieldCheck, IconUser } from "../icons.jsx";

const QUICK_ACTIONS = [
  { id: "departments", icon: IconBuilding, title: "Find a Department", subtitle: "Get the right care" },
  { id: "appointments", icon: IconCalendar, title: "View All Appointments", subtitle: "See your bookings" },
  { id: "profile", icon: IconUser, title: "Update Profile", subtitle: "Keep your info current" },
  { id: "help", icon: IconHelp, title: "Help & FAQs", subtitle: "Find answers" },
];

export default function RightPanel({ onSelectTab }) {
  return (
    <aside className="right-panel">
      <section className="panel-card">
        <h2>
          <IconShieldCheck className="inline-icon" /> Quick Actions
        </h2>
        <div className="quick-actions-grid">
          {QUICK_ACTIONS.map(({ id, icon: Icon, title, subtitle }) => (
            <button key={title} type="button" className="quick-action-tile" onClick={() => onSelectTab(id)}>
              <Icon />
              <span className="quick-action-title">{title}</span>
              <span className="quick-action-subtitle">{subtitle}</span>
              <IconChevronRight className="chevron muted quick-action-chevron" />
            </button>
          ))}
        </div>
      </section>

      <button type="button" className="promo-banner" onClick={() => onSelectTab("help")}>
        <span className="promo-banner-icon">
          <IconShieldCheck />
        </span>
        <span className="promo-banner-text">
          <strong>Your Health. Our Priority.</strong>
          <span>Secure, private, and designed around you.</span>
        </span>
        <IconChevronRight className="chevron" />
      </button>
    </aside>
  );
}
