import { useParams } from "react-router-dom";
import ConfigsList from "./ConfigsList";
import ConfigEditor from "./ConfigEditor";

export default function ConfigsPage() {
  const { file } = useParams<{ file?: string }>();
  return file ? <ConfigEditor /> : <ConfigsList />;
}
