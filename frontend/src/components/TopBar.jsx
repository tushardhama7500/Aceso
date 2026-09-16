import { useEffect, useRef, useState } from "react";
import { IconBell, IconChevronDown, IconLeaf, IconLogout, IconUser } from "../icons.jsx";

function initialsFor(email) {
  const local = email.split("@")[0] || "?";
  const parts = local.split(/[._-]/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}

export default function TopBar({ user, onSelectTab, onLogout }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const menuRef = useRef(null);
  const notifRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
      if (notifRef.current && !notifRef.current.contains(e.target)) setNotifOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">
          <IconLeaf className="brand-icon" /> Aceso
        </span>
        <span className="brand-tagline">Intelligent pathways to care.</span>
      </div>

      <div className="topbar-actions">
        <div className="topbar-icon-wrap" ref={notifRef}>
          <button
            type="button"
            className="topbar-icon-button"
            aria-label="Notifications"
            onClick={() => setNotifOpen((v) => !v)}
          >
            <IconBell />
            <span className="notif-dot" />
          </button>
          {notifOpen && (
            <div className="dropdown-panel notif-panel">
              <p className="empty-state">No new notifications.</p>
            </div>
          )}
        </div>

        <div className="topbar-icon-wrap" ref={menuRef}>
          <button type="button" className="user-chip" onClick={() => setMenuOpen((v) => !v)}>
            <span className="avatar">{initialsFor(user.email)}</span>
            <span className="user-chip-text">
              <span className="user-chip-name">{user.email.split("@")[0]}</span>
              <span className="user-chip-email">{user.email}</span>
            </span>
            <IconChevronDown className="chevron" />
          </button>
          {menuOpen && (
            <div className="dropdown-panel user-menu">
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  onSelectTab("profile");
                  setMenuOpen(false);
                }}
              >
                <IconUser /> Profile
              </button>
              <button type="button" className="dropdown-item danger" onClick={onLogout}>
                <IconLogout /> Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
