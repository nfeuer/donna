import PageShell from "../../components/PageShell";

export default function PreferencesPage() {
  return (
    <PageShell
      title="Preference Manager"
      description="View and manage learned preference rules extracted from your corrections. Control how Donna adapts to your patterns."
      session={3}
      features={[
        "Active rules table — all learned preferences with confidence scores",
        "Enable/disable toggle per rule — instantly control which rules apply",
        "Correction history — full log of field corrections with original/corrected values",
        "Rule provenance — see which corrections support each extracted rule",
        "Manual rule creation — add rules directly without waiting for extraction",
        "Confidence trend — how rule confidence changes as more corrections are logged",
        "Rule conflict detection — identify rules that contradict each other",
      ]}
    />
  );
}
