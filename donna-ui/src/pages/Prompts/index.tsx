import { useParams } from "react-router-dom";
import { PageHeader } from "../../primitives/PageHeader";
import PromptSidebar from "./PromptSidebar";
import PromptEditor from "./PromptEditor";
import PromptWelcome from "./PromptWelcome";
import styles from "./Prompts.module.css";

export default function PromptsPage() {
  const { "*": splat } = useParams();
  const selected = splat || null;

  return (
    <div className={styles.root}>
      <PromptSidebar selected={selected} />
      <section className={styles.main}>
        {selected ? (
          <PromptEditor file={selected} />
        ) : (
          <>
            <PageHeader eyebrow="System" title="Prompts" />
            <PromptWelcome />
          </>
        )}
      </section>
    </div>
  );
}
