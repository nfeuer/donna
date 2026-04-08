import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";
import styles from "./Tabs.module.css";

interface TabsProps {
  value: string;
  onValueChange: (v: string) => void;
  children: ReactNode;
}

export function Tabs({ value, onValueChange, children }: TabsProps) {
  return (
    <RadixTabs.Root className={styles.root} value={value} onValueChange={onValueChange}>
      {children}
    </RadixTabs.Root>
  );
}

export function TabsList({ children }: { children: ReactNode }) {
  return <RadixTabs.List className={styles.list}>{children}</RadixTabs.List>;
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <RadixTabs.Trigger value={value} className={cn(styles.trigger)}>
      {children}
    </RadixTabs.Trigger>
  );
}

export function TabsContent({ value, children }: { value: string; children: ReactNode }) {
  return (
    <RadixTabs.Content value={value} className={styles.content}>
      {children}
    </RadixTabs.Content>
  );
}
