import { IconBone, IconBrain, IconDroplet, IconEar, IconHeartPulse, IconStethoscope } from "./icons.jsx";

// Purely cosmetic mapping — the backend has no concept of department color.
// Falls back to a neutral teal tile for anything not listed here, so a new
// or unexpected department name never breaks rendering. Descriptions mirror
// the department guidance in the Agent's own system prompt.
const DEPARTMENTS = {
  ENT: {
    icon: IconEar,
    bg: "#fde4e8",
    fg: "#c0264a",
    description: "Ear, nose, and throat — hearing issues, sinus problems, throat pain.",
  },
  Neurology: {
    icon: IconBrain,
    bg: "#ece3fb",
    fg: "#6d28d9",
    description: "Headaches, dizziness, numbness, and other nervous-system concerns.",
  },
  Orthopedics: {
    icon: IconBone,
    bg: "#fdecd7",
    fg: "#c2660a",
    description: "Joint, bone, and muscle pain — injuries, back pain, sports-related issues.",
  },
  Cardiology: {
    icon: IconHeartPulse,
    bg: "#fde2e2",
    fg: "#c81e3a",
    description: "Chest and heart-related symptoms (non-emergency).",
  },
  Dermatology: {
    icon: IconDroplet,
    bg: "#fef6d8",
    fg: "#a3760a",
    description: "Skin, hair, and nail conditions.",
  },
  "General Medicine": {
    icon: IconStethoscope,
    bg: "#e0f2ef",
    fg: "#0b5b54",
    description: "General or unclear symptoms, routine concerns, and everything in between.",
  },
};

const DEFAULT = { icon: IconStethoscope, bg: "#e6f4f2", fg: "#0b5b54", description: "" };

export function departmentStyle(department) {
  return DEPARTMENTS[department] || DEFAULT;
}

export const DEPARTMENT_LIST = Object.entries(DEPARTMENTS).map(([name, style]) => ({ name, ...style }));
