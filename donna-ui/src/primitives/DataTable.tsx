import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "../lib/cn";
import { Button } from "./Button";
import { Skeleton } from "./Skeleton";
import styles from "./DataTable.module.css";

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  getRowId: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedRowId?: string | null;
  pageSize?: number;
  loading?: boolean;
  emptyState?: ReactNode;
  /** When true, ↑/↓ navigates rows and Enter activates onRowClick. */
  keyboardNav?: boolean;
}

/**
 * Single table component for the entire app. Built on TanStack Table.
 * Sort + paginate + row selection + keyboard nav. No virtualization in
 * this version — Wave 5 adds it for Logs using @tanstack/react-virtual.
 */
export function DataTable<T>({
  data,
  columns,
  getRowId,
  onRowClick,
  selectedRowId,
  pageSize = 50,
  loading = false,
  emptyState,
  keyboardNav = false,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [focusIndex, setFocusIndex] = useState(0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getRowId,
    initialState: { pagination: { pageSize } },
  });

  const rows = table.getRowModel().rows;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTableSectionElement>) => {
      if (!keyboardNav || rows.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIndex((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const row = rows[focusIndex];
        if (row && onRowClick) onRowClick(row.original);
      }
    },
    [keyboardNav, rows, focusIndex, onRowClick],
  );

  useEffect(() => {
    if (!keyboardNav || !bodyRef.current) return;
    const el = bodyRef.current.querySelectorAll<HTMLTableRowElement>("tr")[focusIndex];
    el?.focus();
  }, [focusIndex, keyboardNav]);

  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount();
  const totalRows = data.length;
  const start = pageIndex * pageSize + 1;
  const end = Math.min((pageIndex + 1) * pageSize, totalRows);

  if (loading) {
    return (
      <div className={styles.wrapper}>
        <div className={styles.scroll}>
          <table className={styles.table}>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td className={styles.bodyCell}><Skeleton height={16} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (rows.length === 0 && emptyState) {
    return <div className={styles.empty}>{emptyState}</div>;
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className={styles.headerRow}>
                {hg.headers.map((h) => {
                  const sortable = h.column.getCanSort();
                  const sort = h.column.getIsSorted();
                  return (
                    <th
                      key={h.id}
                      className={cn(styles.headerCell, sortable && styles.sortable)}
                      onClick={sortable ? h.column.getToggleSortingHandler() : undefined}
                      style={{ width: h.getSize() === 150 ? undefined : h.getSize() }}
                    >
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      {sortable && (
                        <span className={cn(styles.sortIcon, sort && styles.active)}>
                          {sort === "desc" ? <ChevronDown size={11} /> : <ChevronUp size={11} />}
                        </span>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody ref={bodyRef} onKeyDown={handleKeyDown}>
            {rows.map((row, idx) => {
              const id = getRowId(row.original);
              const selected = selectedRowId === id;
              return (
                <tr
                  key={row.id}
                  className={cn(
                    styles.row,
                    selected && styles.selected,
                    onRowClick && styles.clickable,
                  )}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  tabIndex={keyboardNav ? (idx === focusIndex ? 0 : -1) : undefined}
                  aria-selected={selected}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className={styles.bodyCell}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {totalRows > pageSize && (
        <div className={styles.footer}>
          <span>
            Showing {start}–{end} of {totalRows}
          </span>
          <div className={styles.footerActions}>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              Prev
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              Next
            </Button>
          </div>
        </div>
      )}
      {pageCount > 1 && totalRows <= pageSize && null}
    </div>
  );
}
