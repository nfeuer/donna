import { RotateCcw } from "lucide-react";
import { Button } from "../../primitives/Button";
import { Input } from "../../primitives/Input";
import { Select, SelectItem } from "../../primitives/Select";
import {
  ALL_VALUE,
  DOMAIN_OPTIONS,
  PRIORITY_OPTIONS,
  STATUS_OPTIONS,
} from "./taskStatusStyles";
import styles from "./TaskFilters.module.css";

interface Props {
  status: string;
  domain: string;
  priority: string;
  search: string;
  onStatusChange: (v: string) => void;
  onDomainChange: (v: string) => void;
  onPriorityChange: (v: string) => void;
  onSearchChange: (v: string) => void;
  onReset: () => void;
}

/**
 * Primitive filter row for the Tasks list. Every interactive control
 * has an explicit aria-label (audit item P1 "filter form lacks ARIA
 * labels" applied preventively to Tasks). Includes a Reset button
 * (audit item P2 "Task filter form lacks reset button").
 */
export default function TaskFilters({
  status,
  domain,
  priority,
  search,
  onStatusChange,
  onDomainChange,
  onPriorityChange,
  onSearchChange,
  onReset,
}: Props) {
  const isDirty =
    status !== ALL_VALUE ||
    domain !== ALL_VALUE ||
    priority !== ALL_VALUE ||
    search.length > 0;

  return (
    <div className={styles.root} role="search" aria-label="Task filters">
      <Input
        type="search"
        className={styles.search}
        placeholder="Search title or description…"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        aria-label="Search tasks"
      />
      <Select
        value={status}
        onValueChange={onStatusChange}
        aria-label="Status filter"
      >
        {STATUS_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Select
        value={domain}
        onValueChange={onDomainChange}
        aria-label="Domain filter"
      >
        {DOMAIN_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Select
        value={priority}
        onValueChange={onPriorityChange}
        aria-label="Priority filter"
      >
        {PRIORITY_OPTIONS.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </Select>
      <Button
        variant="ghost"
        size="sm"
        onClick={onReset}
        disabled={!isDirty}
        aria-label="Reset all task filters"
      >
        <RotateCcw size={12} /> Reset
      </Button>
    </div>
  );
}
