// Minimal hand-drawn stroke icons (no icon library dependency) — 24x24,
// currentColor stroke, matching the weight/style used throughout the UI.
const base = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export function IconLeaf(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20 4c-8 0-14 5-14 13 0 1.5.3 2.3.3 2.3S12 18 14 14c1.3-2.6 2-6 2-6" />
      <path d="M6 19c-1-4 1-9 4-11.5C13 5 17 4 20 4c0 4-1 9-4.5 12C12 19 8 20 6 19Z" />
    </svg>
  );
}

export function IconBell(props) {
  return (
    <svg {...base} {...props}>
      <path d="M6 8a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </svg>
  );
}

export function IconChevronDown(props) {
  return (
    <svg {...base} {...props}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function IconChevronRight(props) {
  return (
    <svg {...base} {...props}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

export function IconSend(props) {
  return (
    <svg {...base} {...props}>
      <path d="M22 2 11 13" />
      <path d="M22 2 15 22l-4-9-9-4Z" />
    </svg>
  );
}

export function IconPaperclip(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 12.5 12.5 21a5 5 0 0 1-7-7L14 5.5a3.5 3.5 0 0 1 5 5L10.5 19a2 2 0 0 1-3-3L15 8.5" />
    </svg>
  );
}

export function IconChat(props) {
  return (
    <svg {...base} {...props}>
      <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5 8.6 8.6 0 0 1-3.4-.7L4 21l1.8-4.8A8.5 8.5 0 1 1 21 11.5Z" />
    </svg>
  );
}

export function IconCalendar(props) {
  return (
    <svg {...base} {...props}>
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}

export function IconUser(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20.5c1.5-4 4.2-6 7.5-6s6 2 7.5 6" />
    </svg>
  );
}

export function IconHelp(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.3 9.3a2.8 2.8 0 1 1 3.9 2.5c-.9.5-1.2 1-1.2 1.9" />
      <path d="M12 17.2h.01" />
    </svg>
  );
}

export function IconBuilding(props) {
  return (
    <svg {...base} {...props}>
      <rect x="4" y="3" width="12" height="18" rx="1" />
      <path d="M9 8h2M9 12h2M9 16h2M16 10h4v11h-4" />
    </svg>
  );
}

export function IconShieldCheck(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3 5 6v6c0 4.5 3 7.7 7 9 4-1.3 7-4.5 7-9V6Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function IconZap(props) {
  return (
    <svg {...base} {...props}>
      <path d="M13 3 5 14h6l-1 7 8-11h-6l1-7Z" />
    </svg>
  );
}

export function IconTrash(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 7h16M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7M18 7l-.8 12.3A2 2 0 0 1 15.2 21H8.8a2 2 0 0 1-2-1.7L6 7" />
    </svg>
  );
}

export function IconEar(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 4a5 5 0 0 1 5 5c0 2-1 2.8-1 4.5a2.5 2.5 0 0 1-5 0" />
      <path d="M9.5 12A2.5 2.5 0 0 1 12 9.5" />
      <path d="M7 9a7 7 0 0 0 3 12.5c1.5.7 2-.2 2-1.5v-.5" />
    </svg>
  );
}

export function IconBrain(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9 4.5a2.5 2.5 0 0 0-2.5 2.5v.3A3 3 0 0 0 5 10a3 3 0 0 0 .6 5.8A3 3 0 0 0 8.5 20a2.5 2.5 0 0 0 2.5-2.5v-10A2.5 2.5 0 0 0 9 4.5Z" />
      <path d="M15 4.5a2.5 2.5 0 0 1 2.5 2.5v.3A3 3 0 0 1 19 10a3 3 0 0 1-.6 5.8A3 3 0 0 1 15.5 20a2.5 2.5 0 0 1-2.5-2.5v-10a2.5 2.5 0 0 1 2-2.5Z" />
    </svg>
  );
}

export function IconBone(props) {
  return (
    <svg {...base} {...props}>
      <path d="M7 17 17 7" />
      <path d="M5.5 20a2 2 0 1 1 2-3.5 2 2 0 1 1 2 3.4 2 2 0 1 1-2 3.4 2 2 0 0 1-2-3.3Z" />
      <path d="M18.5 4a2 2 0 1 0-2 3.5 2 2 0 1 0-2-3.4 2 2 0 1 0 2-3.4 2 2 0 0 0 2 3.3Z" />
    </svg>
  );
}

export function IconHeartPulse(props) {
  return (
    <svg {...base} {...props}>
      <path d="M20.8 8.6c0 5-8.8 10.4-8.8 10.4S3.2 13.6 3.2 8.6a4.6 4.6 0 0 1 8.8-2 4.6 4.6 0 0 1 8.8 2Z" />
      <path d="M5 11h2l1.5-3L11 14l1.5-4H14" />
    </svg>
  );
}

export function IconStethoscope(props) {
  return (
    <svg {...base} {...props}>
      <path d="M6 4v5a4 4 0 0 0 8 0V4" />
      <path d="M10 13v2a5 5 0 0 0 10 0v-2.5" />
      <circle cx="20" cy="10.5" r="1.8" />
    </svg>
  );
}

export function IconDroplet(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11Z" />
    </svg>
  );
}

export function IconPlus(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function IconLogout(props) {
  return (
    <svg {...base} {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </svg>
  );
}

export function IconSparkle(props) {
  return (
    <svg {...base} {...props}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
    </svg>
  );
}
