import { departmentStyle } from "../departments.js";
import { IconCalendar, IconUser } from "../icons.jsx";

export default function AppointmentsView({ appointments }) {
  return (
    <section className="page-panel">
      <h1 className="page-title">Your Appointments</h1>
      <p className="page-subtitle">Everything you've booked through Aceso, in one place.</p>

      {appointments.length === 0 ? (
        <p className="empty-state">No appointments booked yet. Start a conversation with Aceso to get one scheduled.</p>
      ) : (
        <ul className="appointment-list appointment-list-wide">
          {appointments.map((a) => {
            const { icon: Icon, bg, fg } = departmentStyle(a.department);
            return (
              <li key={a.appointment_id} className="appointment-card appointment-card-wide">
                <div className="appointment-icon" style={{ background: bg, color: fg }}>
                  <Icon />
                </div>
                <div className="appointment-body">
                  <div className="appointment-top-row">
                    <span className="appointment-department">{a.department}</span>
                    <span className="status-pill">Confirmed</span>
                  </div>
                  <div className="appointment-meta">
                    <IconCalendar className="inline-icon" /> {a.visit_date}
                  </div>
                  <div className="appointment-meta">
                    <IconUser className="inline-icon" /> {a.patient_name}
                  </div>
                  <div className="appointment-id">{a.appointment_id}</div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
