import clsx, { type ClassValue } from "clsx";

/**
 * Compose className values. Thin alias around clsx so every primitive
 * imports from the same place. If we later want tailwind-merge we add
 * it here and only here.
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
