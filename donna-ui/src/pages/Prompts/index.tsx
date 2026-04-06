import PageShell from "../../components/PageShell";

export default function PromptsPage() {
  return (
    <PageShell
      title="Prompt Template Editor"
      description="Edit the Jinja2/markdown prompt templates that drive every LLM interaction — task parsing, agent instructions, nudge generation, and digests."
      session={2}
      features={[
        "Markdown editor with syntax highlighting for all 11 prompt templates",
        "Live preview panel showing rendered output with sample variables",
        "Template variable inspector (lists all {{ variables }} used in each template)",
        "Side-by-side diff when editing (original vs modified)",
        "Version history — see what changed and when",
        "Test prompt button — send to the LLM and see real output inline",
        "Link to associated JSON schema for each prompt's expected output",
      ]}
    />
  );
}
