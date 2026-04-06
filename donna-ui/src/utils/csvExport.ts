interface CsvColumn {
  key: string;
  title: string;
}

function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const str = typeof value === "object" ? JSON.stringify(value) : String(value);
  // Wrap in quotes if contains comma, quote, or newline
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}

export function exportToCsv(
  filename: string,
  columns: CsvColumn[],
  data: Record<string, unknown>[],
): void {
  const header = columns.map((c) => escapeCell(c.title)).join(",");
  const rows = data.map((row) =>
    columns.map((c) => escapeCell(row[c.key])).join(","),
  );
  const csv = [header, ...rows].join("\n");

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
