import { useParams } from "react-router-dom";
import PromptsList from "./PromptsList";
import PromptEditor from "./PromptEditor";

export default function PromptsPage() {
  const { file } = useParams<{ file?: string }>();
  return file ? <PromptEditor /> : <PromptsList />;
}
