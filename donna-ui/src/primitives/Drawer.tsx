import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import styles from "./Drawer.module.css";

/**
 * Side-sheet drawer — built on Radix Dialog since it gives us focus trap
 * and Escape handling for free. Slides in from the right.
 */
export function Drawer({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className={styles.overlay} />
        <RadixDialog.Content className={styles.content}>
          <RadixDialog.Title className={styles.title}>{title}</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">
            Detail drawer
          </RadixDialog.Description>
          {children}
          <RadixDialog.Close className={styles.close} aria-label="Close">
            <X size={16} />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
