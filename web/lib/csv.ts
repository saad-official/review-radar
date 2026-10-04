/**
 * The review CSV accepted by `POST /api/apps/{id}/import`. The web app checks the
 * header row before uploading so a wrong file fails fast with a useful message;
 * the API validates every row again.
 */

export const CSV_REQUIRED = ["store_review_id", "rating", "body", "date"] as const;
export const CSV_OPTIONAL = ["author", "title", "app_version", "country"] as const;
export const CSV_COLUMNS = [...CSV_REQUIRED, ...CSV_OPTIONAL] as const;

export const CSV_EXAMPLE = `store_review_id,rating,title,body,app_version,date,author
11843097342,1,"Crashes on launch","Since 4.2.0 it closes at the splash screen. iPhone 13 mini, iOS 18.0.1",4.2.0,2026-10-03,marlowe_k
11843101188,4,"Please add export","Would give 5 stars with CSV export",4.1.3,2026-10-02,`;

export const CSV_MAX_BYTES = 2 * 1024 * 1024;

/** Split one CSV line, honouring double quotes. Enough for a header row. */
export function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out.map((c) => c.trim());
}

export type HeaderCheck = { ok: true; columns: string[] } | { ok: false; error: string };

export function checkCsvHeader(text: string): HeaderCheck {
  const firstLine = text.replace(/^﻿/, "").split(/\r?\n/, 1)[0] ?? "";
  if (firstLine.trim() === "") return { ok: false, error: "The file is empty." };
  const columns = splitCsvLine(firstLine).map((c) => c.toLowerCase());
  const missing = CSV_REQUIRED.filter((c) => !columns.includes(c));
  if (missing.length > 0) {
    return { ok: false, error: `Missing column${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}. The header row needs ${CSV_REQUIRED.join(", ")}.` };
  }
  return { ok: true, columns };
}
