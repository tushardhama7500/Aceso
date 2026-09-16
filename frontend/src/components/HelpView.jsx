const FAQS = [
  {
    q: "What can Aceso help with?",
    a: "Aceso helps you describe your symptoms, asks a few focused follow-up questions, recommends an appropriate department, and books an appointment for you.",
  },
  {
    q: "Is Aceso a doctor?",
    a: "No. Aceso never diagnoses a condition or prescribes treatment. It's a navigation assistant — the actual diagnosis and care happen with the doctor at your appointment.",
  },
  {
    q: "What if I describe a medical emergency?",
    a: "Aceso is built to recognize signs of a medical emergency and will tell you to seek immediate emergency care instead of continuing normal booking.",
  },
  {
    q: "Can I discuss more than one issue in the same conversation?",
    a: "Yes — mention as many concerns as you like in one conversation. Aceso tracks each one separately and books a separate appointment for each.",
  },
];

export default function HelpView() {
  return (
    <section className="page-panel">
      <h1 className="page-title">Help & Support</h1>
      <p className="page-subtitle">Quick answers about how Aceso works.</p>

      <div className="faq-list">
        {FAQS.map(({ q, a }) => (
          <div className="faq-item" key={q}>
            <h3>{q}</h3>
            <p>{a}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
