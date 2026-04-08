import {
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ChevronDown, ChevronUp } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
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
  /** When true, ↑/↓ navigates rows and Enter activates onRowClick. Ignored in virtual mode. */
  keyboardNav?: boolean;
  /** Opt into virtualized rendering. Disables client pagination. */
  virtual?: boolean;
  /** Fixed row height in px when virtual=true. Default 44. */
  rowHeight?: number;
  /** Scroll container max-height in px when virtual=true. Default 600. */
  maxHeight?: number;
}

/**
 * Single table component for the entire app. Built on TanStack Table.
 * Sort + paginate + row selection + keyboard nav. Optional virtualization
 * via @tanstack/react-virtual for log-scale datasets (Wave 3).
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
  virtual = false,
  rowHeight = 44,
  maxHeight = 600,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [focusIndex, setFocusIndex] = useState(0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    // Only wire up client pagination when not virtualizing.
    ...(virtual ? {} : { getPaginationRowModel: getPaginationRowModel() }),
    getRowId,
    initialState: { pagination: { pageSize } },
  });

  const rows = table.getRowModel().rows;
  const colCount = table.getVisibleFlatColumns().length;

  const virtualizer = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const virtualRows = virtual ? virtualizer.getVirtualItems() : [];
  const totalSize = virtual ? virtualizer.getTotalSize() : 0;
  const paddingTop = virtual && virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtual && virtualRows.length > 0
      ? totalSize - virtualRows[virtualRows.length - 1].end
      : 0;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTableSectionElement>) => {
      if (!keyboardNav || virtual || rows.length === 0) return;
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
    [keyboardNav, virtual, rows, focusIndex, onRowClick],
  );

  useEffect(() => {
    if (!keyboardNav || virtual || !bodyRef.current) return;
    const el = bodyRef.current.querySelectorAll<HTMLTableRowElement>("tr")[focusIndex];
    el?.focus();
  }, [focusIndex, keyboardNav, virtual]);

  const pageIndex = table.getState().pagination.pageIndex;
  const pageCount = table.getPageCount();
  const totalRows = data.length;
  const start = pageIndex * pageSize + 1;
  const end = Math.min((pageIndex + 1) * pageSize, totalRows);

  const wrapperStyle = useMemo(
    () => (virtual ? { maxHeight: `${maxHeight}px` } : undefined),
    [virtual, maxHeight],
  );

  if (loading) {
    return (
      <div className={styles.wrapper}>
        <div className={styles.scroll}>
          <table className={styles.table}>
            <tbody>
              {Array.from({ length: 5 }).map((_, i) => (
                <tr key={i}>
                  <td className={styles.bodyCell}>
                    <Skeleton height={16} />
                  </td>
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
      <div
        ref={scrollRef}
        className={cn(styles.scroll, virtual && styles.virtualScroll)}
        style={wrapperStyle}
      >
        <table className={styles.table}>
          <thead className={cn(virtual && styles.stickyHead)}>
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
            {virtual && paddingTop > 0 && (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: paddingTop, padding: 0, border: 0 }} />
              </tr>
            )}
            {(virtual ? virtualRows.map((vr) => rows[vr.index]) : rows).map((row, idx) => {
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
                  tabIndex={keyboardNav && !virtual ? (idx === focusIndex ? 0 : -1) : undefined}
                  aria-selected={selected}
                  style={virtual ? { height: rowHeight } : undefined}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className={styles.bodyCell}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {virtual && paddingBottom > 0 && (
              <tr aria-hidden="true">
                <td colSpan={colCount} style={{ height: paddingBottom, padding: 0, border: 0 }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!virtual && totalRows > pageSize && (
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
      {!virtual && pageCount > 1 && totalRows <= pageSize && null}
    </div>
  );
}
