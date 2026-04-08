import { RotateCw, Save, Trash2 } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Input } from "../../primitives/Input";
import { Pill } from "../../primitives/Pill";
import { Segmented } from "../../primitives/Segmented";
import { Select, SelectItem } from "../../primitives/Select";
import { DateRangePicker, type DateRangeValue } from "./DateRangePicker";
import { LEVEL_OPTIONS, type LevelFilterValue } from "./levelStyles";
import styles from "./FilterBar.module.css";

export interface FilterPreset {
  name: string;
  eventTypes: string[];
  level: string;
  search: string;
}

interface Props {
  search: string;
  onSearchChange: (v: string) => void;
  level: LevelFilterValue;
  onLevelChange: (v: LevelFilterValue) => void;
  dateRange: DateRangeValue;
  onDateRangeChange: (v: DateRangeValue) => void;
  source: string;
  presets: FilterPreset[];
  onLoadPreset: (name: string) => void;
  onDeletePreset: (name: string) => void;
  onOpenSavePreset: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}

/**
 * Single filter row for the Logs page. Every interactive element has
 * an explicit aria-label (audit item P1 "Logs filter form lacks ARIA
 * labels").
 */
export function FilterBar({
  search,
  onSearchChange,
  level,
  onLevelChange,
  dateRange,
  onDateRangeChange,
  source,
  presets,
  onLoadPreset,
  onDeletePreset,
  onOpenSavePreset,
  onRefresh,
  refreshing,
}: Props) {
  return (
    <div className={styles.root} role="search" aria-label="Log filters">
      <div className={styles.row}>
        <Input
          type="search"
          className={styles.search}
          placeholder="Search message text…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="Search logs"
        />
        <Segmented
          value={level}
          onValueChange={onLevelChange}
          options={LEVEL_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
          aria-label="Log level filter"
        />
      </div>

      <div className={styles.row}>
        <DateRangePicker value={dateRange} onChange={onDateRangeChange} />
        <div className={styles.presets}>
          <Select
            value=""
            onValueChange={(v) => v && onLoadPreset(v)}
            placeholder="Load preset…"
            aria-label="Load saved filter preset"
          >
            {presets.length === 0 ? (
              <SelectItem value="__none__">No presets saved</SelectItem>
            ) : (
              presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  {p.name}
                </SelectItem>
              ))
            )}
          </Select>
          {presets.length > 0 && (
            <Select
              value=""
              onValueChange={(v) => v && onDeletePreset(v)}
              placeholder="Delete preset…"
              aria-label="Delete saved filter preset"
            >
              {presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  <span className={styles.deletePresetItem}>
                    <Trash2 size={11} /> {p.name}
                  </span>
                </SelectItem>
              ))}
            </Select>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onOpenSavePreset}
            aria-label="Save current filters as preset"
          >
            <Save size={12} /> Save
          </Button>
        </div>
        <div className={styles.spacer} />
        {source && (
          <Pill variant="muted" aria-label={`Log source: ${source}`}>
            {source}
          </Pill>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={onRefresh}
          disabled={refreshing}
          aria-label="Refresh log list"
        >
          <RotateCw size={12} className={refreshing ? styles.spin : undefined} /> Refresh
        </Button>
      </div>
    </div>
  );
}
