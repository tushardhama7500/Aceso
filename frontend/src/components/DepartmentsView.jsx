import { DEPARTMENT_LIST } from "../departments.js";
import { IconChevronRight } from "../icons.jsx";

export default function DepartmentsView({ onSelectDepartment }) {
  return (
    <section className="page-panel">
      <h1 className="page-title">Find a Department</h1>
      <p className="page-subtitle">
        Not sure where to start? Pick the area closest to what you're experiencing, or just describe your
        symptoms in the chat and Aceso will work it out for you.
      </p>

      <div className="department-grid">
        {DEPARTMENT_LIST.map(({ name, icon: Icon, bg, fg, description }) => (
          <button
            key={name}
            type="button"
            className="department-card"
            onClick={() => onSelectDepartment(name)}
          >
            <div className="department-icon" style={{ background: bg, color: fg }}>
              <Icon />
            </div>
            <div className="department-body">
              <span className="department-name">{name}</span>
              <span className="department-description">{description}</span>
            </div>
            <IconChevronRight className="chevron muted" />
          </button>
        ))}
      </div>
    </section>
  );
}
