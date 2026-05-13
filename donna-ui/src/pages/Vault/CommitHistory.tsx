import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { DataTable } from "../../primitives/DataTable";
import type { VaultCommit } from "../../api/vault";
import styles from "./Vault.module.css";

interface Props {
  commits: VaultCommit[];
  loading: boolean;
}

export default function CommitHistory({ commits, loading }: Props) {
  const columns = useMemo<ColumnDef<VaultCommit>[]>(
    () => [
      {
        accessorKey: "sha",
        header: "SHA",
        size: 100,
        cell: ({ getValue }) => (
          <code className={styles.sha}>{getValue<string>().slice(0, 8)}</code>
        ),
      },
      {
        accessorKey: "message",
        header: "Message",
      },
    ],
    [],
  );

  return (
    <DataTable
      data={commits}
      columns={columns}
      getRowId={(r) => r.sha}
      loading={loading}
      pageSize={25}
      emptyState="No vault activity yet"
    />
  );
}
