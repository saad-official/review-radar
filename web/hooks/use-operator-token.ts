"use client";

import { useSyncExternalStore } from "react";
import { readOperatorToken, subscribeOperatorToken } from "@/lib/operator";

/** The operator token from localStorage; always null on the server and during hydration. */
export function useOperatorToken(): string | null {
  return useSyncExternalStore(subscribeOperatorToken, readOperatorToken, () => null);
}
