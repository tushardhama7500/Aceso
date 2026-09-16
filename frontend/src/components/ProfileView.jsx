import { IconLogout } from "../icons.jsx";

function initialsFor(email) {
  const local = email.split("@")[0] || "?";
  const parts = local.split(/[._-]/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}

export default function ProfileView({ user, onLogout }) {
  const memberSince = user.created_at
    ? new Date(user.created_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })
    : null;

  return (
    <section className="page-panel">
      <h1 className="page-title">Profile</h1>
      <p className="page-subtitle">Your Aceso account details.</p>

      <div className="profile-card">
        <span className="avatar avatar-large">{initialsFor(user.email)}</span>
        <div className="profile-field">
          <span className="profile-label">Email</span>
          <span className="profile-value">{user.email}</span>
        </div>
        {memberSince && (
          <div className="profile-field">
            <span className="profile-label">Member since</span>
            <span className="profile-value">{memberSince}</span>
          </div>
        )}
        <button type="button" className="ghost-button danger" onClick={onLogout}>
          <IconLogout /> Log out
        </button>
      </div>
    </section>
  );
}
