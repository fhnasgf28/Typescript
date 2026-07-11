#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import https from "node:https";
import path from "node:path";
import readline from "node:readline";

const SERVER_NAME = "hmx-bug-tracker-sheets";
const SERVER_VERSION = "2.2.0";
const DEFAULT_SPREADSHEET_ID = process.env.HMX_BUG_TRACKER_SPREADSHEET_ID || "1jJ3laj-APsCIhJWvs-IjTGZTb-hGDAHRn15FTwYbd_4";
const DEFAULT_SHEET_NAME = process.env.HMX_BUG_TRACKER_SHEET_NAME || "Bug Tracker All";
const DEFAULT_ASSIGNEE = process.env.HMX_BUG_TRACKER_ASSIGNEE || "Farhan";
const DEFAULT_CREDENTIALS_PATH = process.env.HMX_BUG_TRACKER_GOOGLE_CREDENTIALS || process.env.GOOGLE_APPLICATION_CREDENTIALS || "/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json";
const SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets";
const STATE_DIR = process.env.HMX_BUG_TRACKER_STATE_DIR || "/home/adminftp/.local/share/openclaw-bug-tracker";
const CACHE_FILE = path.join(STATE_DIR, "cache.json");
const CACHE_TTL_MS = 8 * 60 * 1000;

// ─── In-memory cache ─────────────────────────────────────────────────────────
let _memCache = null;

function isCacheValid(entry) {
  return entry && typeof entry.timestamp === "number" && Date.now() - entry.timestamp < CACHE_TTL_MS;
}

async function readFileCache() {
  try {
    const raw = await fs.readFile(CACHE_FILE, "utf8");
    const parsed = JSON.parse(raw);
    const entry = deserializeEntry(parsed);
    if (isCacheValid(entry)) return entry;
  } catch { /* no file or corrupt */ }
  return null;
}

function serializeEntry(entry) {
  if (!entry) return null;
  const { key, timestamp, parsed } = entry;
  return {
    key, timestamp,
    parsed: {
      rows: parsed.rows,
      dataRows: parsed.dataRows,
      header: {
        index: parsed.header.index,
        headers: parsed.header.headers,
        mapEntries: [...parsed.header.map.entries()],
      },
      sheetName: parsed.sheetName,
      spreadsheetId: parsed.spreadsheetId,
      source: parsed.source,
      sourceWarning: parsed.sourceWarning,
    },
  };
}
function deserializeEntry(raw) {
  if (!raw) return null;
  const header = raw.parsed.header;
  return {
    key: raw.key,
    timestamp: raw.timestamp,
    parsed: {
      rows: raw.parsed.rows,
      dataRows: raw.parsed.dataRows,
      header: {
        index: header.index,
        headers: header.headers,
        map: new Map(header.mapEntries || []),
      },
      sheetName: raw.parsed.sheetName,
      spreadsheetId: raw.parsed.spreadsheetId,
      source: raw.parsed.source,
      sourceWarning: raw.parsed.sourceWarning,
    },
  };
}
async function writeFileCache(entry) {
  try {
    await fs.mkdir(STATE_DIR, { recursive: true });
    await fs.writeFile(CACHE_FILE, JSON.stringify(serializeEntry(entry), null, 2) + "\n", "utf8");
  } catch { /* non-critical */ }
}

// ─── HTTP helpers ─────────────────────────────────────────────────────────────
function jsonResponse(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}
function jsonError(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

function normalize(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}
function normalizeLoose(value) {
  return normalize(value).replace(/[^a-z0-9]+/g, "");
}
function compact(value, maxLength = 220) {
  const clean = String(value || "").replace(/https?:\/\/\S+/g, "[link]").replace(/\s+/g, " ").trim();
  if (clean.length <= maxLength) return clean;
  return clean.slice(0, maxLength - 3).trimEnd() + "...";
}
function compactRaw(value, maxLength = 420) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  if (clean.length <= maxLength) return clean;
  return clean.slice(0, maxLength - 3).trimEnd() + "...";
}
function isBlank(value) { return String(value || "").trim() === ""; }
function contains(value, needle) { return normalize(value).includes(normalize(needle)); }
function containsLoose(value, needle) {
  const strictNeedle = normalize(needle);
  if (!strictNeedle) return true;
  if (normalize(value).includes(strictNeedle)) return true;
  const looseNeedle = normalizeLoose(needle);
  return Boolean(looseNeedle) && normalizeLoose(value).includes(looseNeedle);
}
function buildCsvUrl(spreadsheetId, sheetName) {
  return `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheetName)}&cacheBust=${Date.now()}`;
}

function fetchText(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "OpenClaw Spreadsheet MCP" }, timeout: 20000 }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume();
        resolve(fetchText(new URL(res.headers.location, url).toString(), redirectCount + 1));
        return;
      }
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        if (status < 200 || status >= 300) { reject(new Error(`Spreadsheet export failed HTTP ${status}`)); return; }
        resolve(body);
      });
    });
    req.on("timeout", () => { req.destroy(new Error("Spreadsheet export timeout")); });
    req.on("error", reject);
  });
}

function requestText(method, targetUrl, { headers = {}, body = "" } = {}, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.request(targetUrl, { method, headers, timeout: 20000 }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume();
        resolve(requestText(method, new URL(res.headers.location, targetUrl).toString(), { headers, body }, redirectCount + 1));
        return;
      }
      let rb = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { rb += c; });
      res.on("end", () => {
        if (status < 200 || status >= 300) { reject(new Error(`Google API HTTP ${status}: ${rb.slice(0, 200)}`)); return; }
        resolve(rb);
      });
    });
    req.on("timeout", () => { req.destroy(new Error("Google API timeout")); });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function base64url(value) {
  const buf = Buffer.isBuffer(value) ? value : Buffer.from(value);
  return buf.toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

async function getSheetsAccessToken(credPath = DEFAULT_CREDENTIALS_PATH) {
  let raw;
  try { raw = await fs.readFile(credPath, "utf8"); }
  catch (e) {
    if (e && e.code === "ENOENT") throw new Error(`Service account JSON not found at ${credPath}. Share spreadsheet to service account as Editor.`);
    throw e;
  }
  const cred = JSON.parse(raw);
  if (cred.web || cred.installed) throw new Error("Credential is OAuth client secret, not service account JSON.");
  if (!cred.client_email || !cred.private_key) throw new Error(`Credential missing client_email/private_key: ${credPath}`);
  const now = Math.floor(Date.now() / 1000);
  const hdr = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const clm = base64url(JSON.stringify({ iss: cred.client_email, scope: SHEETS_SCOPE, aud: cred.token_uri || "https://oauth2.googleapis.com/token", exp: now + 3600, iat: now }));
  const unsigned = `${hdr}.${clm}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(unsigned); signer.end();
  const assertion = `${unsigned}.${base64url(signer.sign(cred.private_key))}`;
  const body = new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion }).toString();
  const text = await requestText("POST", cred.token_uri || "https://oauth2.googleapis.com/token", {
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Content-Length": Buffer.byteLength(body) }, body,
  });
  const parsed = JSON.parse(text);
  if (!parsed.access_token) throw new Error("No access_token in Google OAuth response");
  return parsed.access_token;
}

async function sheetsApiRequest(method, apiPath, accessToken, payload) {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  const text = await requestText(method, `https://sheets.googleapis.com${apiPath}`, {
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json", ...(body ? { "Content-Length": Buffer.byteLength(body) } : {}) },
    body,
  });
  return text ? JSON.parse(text) : {};
}

function columnLetter(index) {
  let v = Number(index) + 1, letters = "";
  while (v > 0) { const r = (v - 1) % 26; letters = String.fromCharCode(65 + r) + letters; v = Math.floor((v - 1) / 26); }
  return letters;
}
function sheetA1Name(name) { return `'${String(name).replace(/'/g, "''")}'`; }
function columnIndex(header, names) {
  for (const n of names) { const i = header.map.get(normalize(n)); if (i !== undefined) return i; }
  throw new Error(`Column not found: ${names.join(" / ")}`);
}
function buildNoteAddition(args = {}) {
  const parts = [];
  if (!isBlank(args.note)) parts.push(String(args.note).trim());
  if (!isBlank(args.commit)) parts.push(`commit: ${String(args.commit).trim()}`);
  if (!isBlank(args.link)) parts.push(`link: ${String(args.link).trim()}`);
  return parts.join(" | ");
}
function mergeNotes(existing, addition, replace) {
  if (!addition) return String(existing || "");
  if (replace || isBlank(existing)) return addition;
  return `${String(existing).trim()}\n${addition}`;
}

// ─── CSV parsing ─────────────────────────────────────────────────────────────
function parseCsv(text) {
  const rows = []; let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === "\"") { if (text[i + 1] === "\"") { field += "\""; i++; } else { quoted = false; } }
      else { field += ch; }
      continue;
    }
    if (ch === "\"") { quoted = true; }
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else { field += ch; }
  }
  if (field.length || row.length) { row.push(field.replace(/\r$/, "")); rows.push(row); }
  return rows;
}

function findHeader(rows) {
  for (let i = 0; i < Math.min(rows.length, 20); i++) {
    const norm = rows[i].map(normalize);
    if (norm.includes("dev") && norm.includes("dev status")) {
      const map = new Map();
      rows[i].forEach((name, idx) => { const k = normalize(name); if (k) map.set(k, idx); });
      return { index: i, map, headers: rows[i] };
    }
  }
  throw new Error("Could not find header row with Dev and Dev Status columns");
}

function cell(row, map, names) {
  for (const n of names) { const i = map.get(normalize(n)); if (i !== undefined) return row[i] || ""; }
  return "";
}


function cellByHeaderOrIndex(row, map, names, fallbackIndex) {
  for (const n of names) {
    const i = map.get(normalize(n));
    if (i !== undefined) return row[i] || "";
  }
  if (Number.isInteger(fallbackIndex)) return row[fallbackIndex] || "";
  return "";
}

function formatSheetDate(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Shanghai", year: "numeric", month: "numeric", day: "numeric" }).formatToParts(date);
  const get = (type) => Number(parts.find((p) => p.type === type)?.value || 0);
  return `${get("month")}/${get("day")}/${get("year")}`;
}

function dateKeyInTz(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function addDays(date, days) {
  const d = new Date(date.getTime());
  d.setUTCDate(d.getUTCDate() + days);
  return d;
}

function parseSheetDateKey(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  let m = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})$/);
  if (m) {
    const a = Number(m[1]);
    const b = Number(m[2]);
    const year = m[3].length === 2 ? `20${m[3]}` : m[3];
    const month = (a > 12 ? b : a).toString().padStart(2, "0");
    const day = (a > 12 ? a : b).toString().padStart(2, "0");
    return `${year}-${month}-${day}`;
  }
  m = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const serial = Number(text);
    if (serial > 20000 && serial < 80000) {
      const parsed = new Date(Date.UTC(1899, 11, 30) + serial * 86400000);
      return dateKeyInTz(parsed);
    }
  }
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) return dateKeyInTz(parsed);
  return "";
}

function statusLine(row, header, rowNumber) {
  const note = compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status Note"], 13), 220);
  if (note) return note;
  const menu = compact(cell(row, header.map, ["Menu"]), 80);
  const moduleName = compact(cell(row, header.map, ["Modul", "Module"]), 80);
  const desc = compact(cell(row, header.map, ["Description"]), 160);
  const prefix = [menu, moduleName].filter(Boolean).join(" - ");
  return compactRaw(prefix && desc ? `${prefix}: ${desc}` : (desc || prefix || `Row ${rowNumber}`), 240);
}

function buildStatusItem(row, header, rowNumber) {
  return {
    row: rowNumber,
    status: compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status"], 11), 80),
    date: compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status Date"], 12), 40),
    note: compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status Note"], 13), 220),
    line: statusLine(row, header, rowNumber),
  };
}

function dateMatches(item, wantedKey, { allowBlank = false } = {}) {
  const key = parseSheetDateKey(item.date);
  if (!key && allowBlank) return true;
  return key === wantedKey;
}

function renderBullets(items, emptyText = "None") {
  if (!items.length) return `- ${emptyText}`;
  return items.map((item) => `- ${item.line}`).join("\n");
}

async function generateInternalStatusDraft(args = {}) {
  const periodArg = normalize(args.period || "morning");
  const period = periodArg.includes("sore") || periodArg.includes("evening") ? "evening" : "morning";
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxItems = Math.max(1, Math.min(Number(args.maxItems || 10), 30));
  const forceRefresh = Boolean(args.forceRefresh);
  const now = args.reportDate ? new Date(`${args.reportDate}T00:00:00+08:00`) : new Date();
  const todayKey = dateKeyInTz(now);
  const yesterdayKey = dateKeyInTz(addDays(now, -1));
  const reportDate = formatSheetDate(now);
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, forceRefresh);
  const items = [];
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    if (!contains(dev, assignee)) return;
    const item = buildStatusItem(row, header, rowNumber);
    if (!item.status || normalize(item.status) === "skip") return;
    items.push(item);
  });
  const byStatus = (statusName, key, opts = {}) => items.filter((item) => normalize(item.status) === normalize(statusName) && dateMatches(item, key, opts)).slice(0, maxItems);
  const doneYesterday = byStatus("Done", yesterdayKey);
  const doneToday = byStatus("Done", todayKey);
  const planToday = byStatus("Plan Today", todayKey);
  const onProgress = byStatus("On Progress", todayKey, { allowBlank: true });
  const blockers = byStatus("Blocker", todayKey, { allowBlank: true });
  let message;
  if (period === "evening") {
    message = [
      `Internal Status - Sore (${reportDate})`,
      "",
      "Done hari ini:",
      renderBullets(doneToday),
      "",
      "On progress:",
      renderBullets(onProgress),
      "",
      "Blocker:",
      renderBullets(blockers),
    ].join("\n");
  } else {
    message = [
      `Internal Status - Pagi (${reportDate})`,
      "",
      "Done kemarin:",
      renderBullets(doneYesterday),
      "",
      "Plan hari ini:",
      renderBullets(planToday),
      "",
      "On progress:",
      renderBullets(onProgress),
      "",
      "Blocker:",
      renderBullets(blockers),
    ].join("\n");
  }
  return {
    source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch",
    sheet: sheetName,
    assignee,
    period,
    reportDate,
    markerColumns: { internalStatus: "L", internalStatusDate: "M", internalStatusNote: "N" },
    counts: { doneYesterday: doneYesterday.length, doneToday: doneToday.length, planToday: planToday.length, onProgress: onProgress.length, blockers: blockers.length, markedRows: items.length },
    message,
  };
}

function rowToTask(row, map, rowNumber) {
  const attachment = cell(row, map, ["Attachment (link)", "Attachment", "Link"]);
  return {
    row: rowNumber,
    menu: compact(cell(row, map, ["Menu"]), 90),
    module: compact(cell(row, map, ["Modul", "Module"]), 90),
    description: compact(cell(row, map, ["Description"]), 260),
    feedbackType: compact(cell(row, map, ["Feedback Type"]), 60),
    dev: compact(cell(row, map, ["Dev"]), 60),
    devStatus: compact(cell(row, map, ["Dev Status"]), 80),
    saPoStatus: compact(cell(row, map, ["SA/PO Status", "SA Status", "PO Status"]), 80),
    notes: compact(cell(row, map, ["notes", "Notes"]), 180),
    attachment: compactRaw(attachment),
    hasAttachment: !isBlank(attachment),
  };
}

function rowRangeFromArgs(args = {}) {
  const min = Number.isInteger(Number(args.startRow)) ? Number(args.startRow)
    : Number.isInteger(Number(args.minRow)) ? Number(args.minRow)
      : Number.isInteger(Number(args.afterRow)) ? Number(args.afterRow) + 1
        : 0;
  const max = Number.isInteger(Number(args.endRow)) ? Number(args.endRow)
    : Number.isInteger(Number(args.maxRow)) ? Number(args.maxRow)
      : Number.POSITIVE_INFINITY;
  return { min, max };
}

function rowNumberMatchesArgs(rowNumber, args = {}) {
  const { min, max } = rowRangeFromArgs(args);
  return rowNumber >= min && rowNumber <= max;
}

function statusMatchesArgs(row, map, args = {}) {
  const status = normalize(cell(row, map, ["Dev Status"]));
  const filters = [];
  if (args.devStatus !== undefined && String(args.devStatus).trim() !== "") filters.push(normalize(args.devStatus));
  if (args.status !== undefined && String(args.status).trim() !== "") filters.push(normalize(args.status));
  if (Array.isArray(args.devStatuses)) {
    for (const item of args.devStatuses) {
      if (String(item || "").trim() !== "") filters.push(normalize(item));
    }
  }
  if (args.blankDevStatus === true || args.includeBlankStatus === true) filters.push("");
  if (!filters.length) return true;
  return filters.includes(status);
}

function filterSummaryFromArgs(args = {}) {
  const filters = {};
  if (args.afterRow !== undefined) filters.afterRow = Number(args.afterRow);
  if (args.startRow !== undefined || args.minRow !== undefined) filters.startRow = Number(args.startRow ?? args.minRow);
  if (args.endRow !== undefined || args.maxRow !== undefined) filters.endRow = Number(args.endRow ?? args.maxRow);
  if (args.devStatus !== undefined) filters.devStatus = String(args.devStatus);
  if (args.status !== undefined) filters.status = String(args.status);
  if (Array.isArray(args.devStatuses)) filters.devStatuses = args.devStatuses.map(String);
  if (args.blankDevStatus === true || args.includeBlankStatus === true) filters.blankDevStatus = true;
  if (args.query !== undefined) filters.query = String(args.query);
  return filters;
}

async function fetchRowsViaSheetsApi(spreadsheetId, sheetName) {
  const accessToken = await getSheetsAccessToken(DEFAULT_CREDENTIALS_PATH);
  const range = encodeURIComponent(`'${String(sheetName).replace(/'/g, "''")}'`);
  const result = await sheetsApiRequest(
    "GET",
    `/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values/${range}?valueRenderOption=FORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING`,
    accessToken,
  );
  return (result.values || []).filter((r) => r.some((v) => !isBlank(v)));
}
async function fetchRowsViaCsv(spreadsheetId, sheetName) {
  const text = await fetchText(buildCsvUrl(spreadsheetId, sheetName));
  return parseCsv(text).filter((r) => r.some((v) => !isBlank(v)));
}
async function fetchLiveRows(spreadsheetId, sheetName) {
  try {
    const rows = await fetchRowsViaSheetsApi(spreadsheetId, sheetName);
    return { rows, source: "sheets_api" };
  } catch (error) {
    const rows = await fetchRowsViaCsv(spreadsheetId, sheetName);
    return { rows, source: "public_csv_fallback", sourceWarning: compact(error?.message || String(error), 180) };
  }
}

// ─── Cached loader ────────────────────────────────────────────────────────────
async function loadBugTrackerCached(args = {}, forceRefresh = false) {
  const spreadsheetId = args.spreadsheetId || DEFAULT_SPREADSHEET_ID;
  const sheetName = args.sheetName || DEFAULT_SHEET_NAME;
  const cacheKey = `${spreadsheetId}::${sheetName}`;

  if (!forceRefresh) {
    // 1. check in-memory
    if (_memCache && _memCache.key === cacheKey && isCacheValid(_memCache)) {
      return { ..._memCache.parsed, fromCache: true, cacheAge: Math.round((Date.now() - _memCache.timestamp) / 1000) };
    }
    // 2. check file cache
    const fileCache = await readFileCache();
    if (fileCache && fileCache.key === cacheKey) {
      _memCache = fileCache;
      return { ..._memCache.parsed, fromCache: true, cacheAge: Math.round((Date.now() - _memCache.timestamp) / 1000) };
    }
  }

  // 3. fetch fresh (prefer Sheets API because public gviz/CSV can lag after writes)
  const live = await fetchLiveRows(spreadsheetId, sheetName);
  const rows = live.rows;
  const header = findHeader(rows);
  const dataRows = rows.slice(header.index + 1);

  const parsed = { rows, dataRows, header, sheetName, spreadsheetId, source: live.source, sourceWarning: live.sourceWarning };
  const entry = { key: cacheKey, timestamp: Date.now(), parsed };
  _memCache = entry;
  await writeFileCache(entry);

  return { ...parsed, fromCache: false, cacheAge: 0 };
}

// Update a single row in the cache after a write, without re-fetching
function updateCacheRow(rowNumber, updates, sheetName, spreadsheetId) {
  const cacheKey = `${spreadsheetId}::${sheetName}`;
  if (!_memCache || _memCache.key !== cacheKey) return;
  const { header, dataRows } = _memCache.parsed;
  const offset = rowNumber - (header.index + 2);
  if (offset < 0 || offset >= dataRows.length) return;
  const row = [...dataRows[offset]];
  for (const { colIndex, value } of updates) {
    while (row.length <= colIndex) row.push("");
    row[colIndex] = value;
  }
  _memCache.parsed.dataRows[offset] = row;
}

// ─── Tool implementations ─────────────────────────────────────────────────────
async function getOpenDevTasks(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 12), 50));
  const forceRefresh = Boolean(args.forceRefresh);
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, forceRefresh);
  const matches = [];
  let totalForAssignee = 0;
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    const devStatus = cell(row, header.map, ["Dev Status"]);
    if (contains(dev, assignee)) {
      totalForAssignee++;
      if (rowNumberMatchesArgs(rowNumber, args) && isBlank(devStatus)) matches.push(rowToTask(row, header.map, rowNumber));
    }
  });
  return {
    source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch",
    sheet: sheetName, assignee,
    filter: "Dev contains assignee AND Dev Status blank",
    filters: filterSummaryFromArgs(args),
    matched: matches.length, totalRowsForAssignee: totalForAssignee,
    returned: Math.min(matches.length, maxRows),
    tasks: matches.slice(0, maxRows),
  };
}

async function summarize(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const forceRefresh = Boolean(args.forceRefresh);
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, forceRefresh);
  const counts = new Map();
  let total = 0, blank = 0;
  dataRows.forEach((row) => {
    const dev = cell(row, header.map, ["Dev"]);
    if (!contains(dev, assignee)) return;
    total++;
    const status = compact(cell(row, header.map, ["Dev Status"]), 80) || "(blank)";
    if (status === "(blank)") blank++;
    counts.set(status, (counts.get(status) || 0) + 1);
  });
  return {
    source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch",
    sheet: sheetName, assignee, totalRowsForAssignee: total, blankDevStatus: blank,
    byDevStatus: Object.fromEntries([...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))),
  };
}

async function searchTasks(args = {}) {
  const query = String(args.query || "").trim();
  if (!query) throw new Error("query is required");
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 10), 50));
  const forceRefresh = Boolean(args.forceRefresh);
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, forceRefresh);
  const matches = [];
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    if (
      contains(dev, assignee)
      && rowNumberMatchesArgs(rowNumber, args)
      && statusMatchesArgs(row, header.map, args)
      && containsLoose(row.join(" "), query)
    ) matches.push(rowToTask(row, header.map, rowNumber));
  });
  return {
    source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch",
    sheet: sheetName, assignee, query,
    filters: filterSummaryFromArgs(args),
    matched: matches.length, returned: Math.min(matches.length, maxRows),
    tasks: matches.slice(0, maxRows),
  };
}

async function scanTaskQueue(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 20), 100));
  const forceRefresh = Boolean(args.forceRefresh);
  const query = String(args.query || "").trim();
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, forceRefresh);
  const matches = [];
  let totalForAssignee = 0;
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    if (!contains(dev, assignee)) return;
    totalForAssignee++;
    if (!rowNumberMatchesArgs(rowNumber, args)) return;
    if (!statusMatchesArgs(row, header.map, args)) return;
    if (query && !containsLoose(row.join(" "), query)) return;
    matches.push(rowToTask(row, header.map, rowNumber));
  });
  return {
    source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch",
    sheet: sheetName,
    assignee,
    filters: filterSummaryFromArgs(args),
    matched: matches.length,
    totalRowsForAssignee: totalForAssignee,
    returned: Math.min(matches.length, maxRows),
    tasks: matches.slice(0, maxRows),
  };
}

async function updateTask(args = {}) {
  const rowNumber = Number(args.row);
  if (!Number.isInteger(rowNumber) || rowNumber < 1) throw new Error("row must be a positive row number from read/search output");
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const devStatus = args.devStatus === undefined ? "" : String(args.devStatus).trim();
  const noteAddition = buildNoteAddition(args);
  const attachment = args.attachment === undefined ? undefined : String(args.attachment).trim();
  const shouldUpdateAttachment = attachment !== undefined && attachment !== "";
  if (!devStatus && !noteAddition && !shouldUpdateAttachment && !args.internalStatus && !args.internalStatusDate && !args.internalStatusNote) throw new Error("Nothing to update. Provide devStatus, note, commit, link, attachment, or internalStatus/internalStatusDate/internalStatusNote.");

  const spreadsheetId = args.spreadsheetId || DEFAULT_SPREADSHEET_ID;
  const sheetName = args.sheetName || DEFAULT_SHEET_NAME;
  const { dataRows, header } = await loadBugTrackerCached(args, false);
  const offset = rowNumber - (header.index + 2);
  if (offset < 0 || offset >= dataRows.length) throw new Error(`row ${rowNumber} is outside the data table`);
  const row = dataRows[offset];
  const dev = cell(row, header.map, ["Dev"]);
  if (!contains(dev, assignee) && !args.allowOtherAssignee) throw new Error(`row ${rowNumber} is not assigned to ${assignee}`);

  const updates = [];
  const fields = [];
  const cacheUpdates = [];
  if (devStatus) {
    const ci = columnIndex(header, ["Dev Status"]); updates.push({ index: ci, value: devStatus }); fields.push("Dev Status"); cacheUpdates.push({ colIndex: ci, value: devStatus });
  }
  if (noteAddition) {
    const ci = columnIndex(header, ["notes", "Notes"]);
    const merged = mergeNotes(cell(row, header.map, ["notes", "Notes"]), noteAddition, Boolean(args.replaceNotes));
    updates.push({ index: ci, value: merged }); fields.push("notes"); cacheUpdates.push({ colIndex: ci, value: merged });
  }
  if (shouldUpdateAttachment) {
    const ci = columnIndex(header, ["Attachment (link)", "Attachment", "Link"]);
    updates.push({ index: ci, value: attachment }); fields.push("Attachment (link)"); cacheUpdates.push({ colIndex: ci, value: attachment });
  }
  const internalStatus = args.internalStatus === undefined ? "" : String(args.internalStatus).trim();
  if (internalStatus) {
    let ci; try { ci = columnIndex(header, ["Internal Status"]); } catch { ci = 11; }
    updates.push({ index: ci, value: internalStatus }); fields.push("Internal Status"); cacheUpdates.push({ colIndex: ci, value: internalStatus });
  }
  const internalStatusDate = args.internalStatusDate === undefined ? "" : String(args.internalStatusDate).trim();
  if (internalStatusDate) {
    let ci; try { ci = columnIndex(header, ["Internal Status Date"]); } catch { ci = 12; }
    updates.push({ index: ci, value: internalStatusDate }); fields.push("Internal Status Date"); cacheUpdates.push({ colIndex: ci, value: internalStatusDate });
  }
  const internalStatusNote = args.internalStatusNote === undefined ? "" : String(args.internalStatusNote).trim();
  if (internalStatusNote) {
    let ci; try { ci = columnIndex(header, ["Internal Status Note"]); } catch { ci = 13; }
    updates.push({ index: ci, value: internalStatusNote }); fields.push("Internal Status Note"); cacheUpdates.push({ colIndex: ci, value: internalStatusNote });
  }

  const ranges = updates.map((u) => `${sheetA1Name(sheetName)}!${columnLetter(u.index)}${rowNumber}`);
  if (args.dryRun) {
    return { dryRun: true, sheet: sheetName, row: rowNumber, assignee, wouldUpdateFields: fields, ranges, currentTask: rowToTask(row, header.map, rowNumber) };
  }

  const accessToken = await getSheetsAccessToken(args.credentialsPath || DEFAULT_CREDENTIALS_PATH);
  const result = await sheetsApiRequest("POST", `/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values:batchUpdate`, accessToken, {
    valueInputOption: "USER_ENTERED",
    data: updates.map((u, i) => ({ range: ranges[i], values: [[u.value]] })),
  });

  // write-back: update cache row so next call doesn't need re-fetch
  updateCacheRow(rowNumber, cacheUpdates, sheetName, spreadsheetId);

  return { updated: true, sheet: sheetName, row: rowNumber, assignee, updatedFields: fields, updatedCells: result.totalUpdatedCells || updates.length, cacheUpdated: true };
}

async function markTaskDone(args = {}) {
  const today = formatSheetDate(new Date());
  return updateTask({ ...args, devStatus: "Done dev", internalStatus: "Done", internalStatusDate: today });
}

async function updateInternalStatusMarker(args = {}) {
  const marker = args.internalStatus === undefined ? "" : String(args.internalStatus).trim();
  const note = args.internalStatusNote === undefined ? "" : String(args.internalStatusNote).trim();
  const date = args.internalStatusDate === undefined ? (marker ? formatSheetDate(new Date()) : "") : String(args.internalStatusDate).trim();
  if (!marker && !date && !note) throw new Error("Nothing to update. Provide internalStatus, internalStatusDate, or internalStatusNote.");
  return updateTask({ ...args, internalStatus: marker, internalStatusDate: date, internalStatusNote: note });
}

async function getCacheStatus(args = {}) {
  const spreadsheetId = args.spreadsheetId || DEFAULT_SPREADSHEET_ID;
  const sheetName = args.sheetName || DEFAULT_SHEET_NAME;
  const cacheKey = `${spreadsheetId}::${sheetName}`;
  // Check mem cache first, then load file cache if needed
  let cacheEntry = (_memCache && _memCache.key === cacheKey) ? _memCache : null;
  if (!cacheEntry) {
    const fc = await readFileCache();
    if (fc && fc.key === cacheKey) { _memCache = fc; cacheEntry = fc; }
  }
  const valid = cacheEntry != null && isCacheValid(cacheEntry);
  const cacheAge = valid ? Math.round((Date.now() - cacheEntry.timestamp) / 1000) : null;
  const rowCount = valid ? cacheEntry.parsed.dataRows.length : null;
  const ttlRemaining = valid ? Math.round((CACHE_TTL_MS - (Date.now() - cacheEntry.timestamp)) / 1000) : 0;
  return {
    cacheValid: valid,
    fileCache: cacheEntry != null,
    cacheAge: cacheAge !== null ? `${cacheAge}s` : null,
    ttlMs: CACHE_TTL_MS,
    ttlRemainingSeconds: ttlRemaining,
    dataRows: rowCount,
    cacheFile: CACHE_FILE,
    tip: valid
      ? `Cache valid for ~${ttlRemaining}s more. No forceRefresh needed.`
      : "No valid cache. Next query will fetch live from Google Sheets.",
  };
}

async function refreshCache(args = {}) {
  const before = _memCache ? Math.round((Date.now() - _memCache.timestamp) / 1000) : null;
  const result = await loadBugTrackerCached(args, true);
  return {
    refreshed: true,
    sheet: result.sheetName,
    dataRows: result.dataRows.length,
    previousCacheAge: before !== null ? `${before}s` : "no cache",
    tip: "Cache refreshed. Next queries will use new cache for 8 minutes.",
  };
}

// ─── Tool definitions ─────────────────────────────────────────────────────────
const tools = [
  {
    name: "get_farhan_open_dev_tasks",
    description: "FIRST CHOICE: use whenever Farhan asks for open/pending spreadsheet tasks. Returns Farhan rows from Bug Tracker All where Dev Status is blank. Reads from local cache (fast, no network) if cache is < 8 min old. Use forceRefresh:true only when Farhan asks for latest or suspects data is stale.",
    inputSchema: {
      type: "object",
      properties: {
        assignee: { type: "string", default: "Farhan" },
        maxRows: { type: "number", default: 12 },
        afterRow: { type: "number", description: "Only include rows after this sheet row number, e.g. afterRow=79." },
        startRow: { type: "number", description: "Only include rows at or after this sheet row number." },
        endRow: { type: "number", description: "Only include rows at or before this sheet row number." },
        forceRefresh: { type: "boolean", default: false, description: "Force live fetch even if cache is valid." },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "summarize_farhan_bug_tracker",
    description: "Use when Farhan asks for a count/status summary of all tasks. Groups tasks by Dev Status. Reads from cache if valid.",
    inputSchema: {
      type: "object",
      properties: {
        assignee: { type: "string", default: "Farhan" },
        forceRefresh: { type: "boolean", default: false },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "search_farhan_bug_tracker",
    description: "Use when Farhan gives a keyword, module, or menu name to search. Returns matching Farhan rows with attachment links. Reads from cache if valid.",
    inputSchema: {
      type: "object",
      required: ["query"],
      properties: {
        query: { type: "string" },
        assignee: { type: "string", default: "Farhan" },
        maxRows: { type: "number", default: 10 },
        afterRow: { type: "number", description: "Only include rows after this sheet row number." },
        startRow: { type: "number", description: "Only include rows at or after this sheet row number." },
        endRow: { type: "number", description: "Only include rows at or before this sheet row number." },
        devStatus: { type: "string", description: "Optional exact Dev Status filter, e.g. To-Do, Done dev, On Progress." },
        devStatuses: { type: "array", items: { type: "string" }, description: "Optional exact Dev Status allow-list." },
        blankDevStatus: { type: "boolean", description: "Include only blank Dev Status rows when true." },
        forceRefresh: { type: "boolean", default: false },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "scan_farhan_bug_tracker_task_queue",
    description: "Fast combined scanner for Farhan task queue. Use for questions like 'after row 79', 'status To-Do', 'todo task', or status+keyword checks. Supports row range, Dev Status filters, and keyword search in one cached call.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Optional keyword/menu/module/description text. Fuzzy enough that 'todo' matches 'To-Do'." },
        assignee: { type: "string", default: "Farhan" },
        maxRows: { type: "number", default: 20 },
        afterRow: { type: "number", description: "Only include rows after this sheet row number, e.g. afterRow=79." },
        startRow: { type: "number", description: "Only include rows at or after this sheet row number." },
        endRow: { type: "number", description: "Only include rows at or before this sheet row number." },
        devStatus: { type: "string", description: "Exact Dev Status filter, e.g. To-Do, Done dev, On Progress." },
        devStatuses: { type: "array", items: { type: "string" }, description: "Exact Dev Status allow-list." },
        blankDevStatus: { type: "boolean", description: "Include only blank Dev Status rows when true." },
        forceRefresh: { type: "boolean", default: false },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "mark_farhan_task_done",
    description: "Fast path: use when Farhan says a task is done/selesai. Sets Dev Status = 'Done dev' directly. Updates local cache immediately — no re-fetch needed afterward.",
    inputSchema: {
      type: "object",
      required: ["row"],
      properties: {
        row: { type: "number" },
        note: { type: "string" },
        commit: { type: "string" },
        link: { type: "string" },
        internalStatusNote: { type: "string", description: "Short note/commit for Internal Status Note column N" },
        dryRun: { type: "boolean", default: false },
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "update_farhan_bug_tracker_task",
    description: "Use when Farhan asks to update Dev Status, notes, commit, link, or attachment on a specific row. Updates local cache immediately after write — no re-fetch needed. Use dryRun:true first unless Farhan explicitly confirms.",
    inputSchema: {
      type: "object",
      required: ["row"],
      properties: {
        row: { type: "number" },
        devStatus: { type: "string" },
        note: { type: "string" },
        commit: { type: "string" },
        link: { type: "string" },
        attachment: { type: "string" },
        internalStatus: { type: "string", enum: ["Plan Today", "On Progress", "Done", "Blocker", "Skip"], description: "Column L marker for internal status draft." },
        internalStatusDate: { type: "string", description: "Column M date in M/D/YYYY, e.g. 6/29/2026." },
        internalStatusNote: { type: "string", description: "Column N short note used in internal status draft." },
        replaceNotes: { type: "boolean", default: false },
        dryRun: { type: "boolean", default: false },
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "generate_farhan_internal_status_draft",
    description: "Generate Farhan internal status draft for HashMicro Chat from Bug Tracker All marker columns L/M/N. Use for morning/sore reports. Reads cache first; forceRefresh only when sheet was just edited.",
    inputSchema: {
      type: "object",
      properties: {
        period: { type: "string", enum: ["morning", "evening", "pagi", "sore"], default: "morning" },
        reportDate: { type: "string", description: "Optional YYYY-MM-DD date. Defaults to today Asia/Shanghai." },
        assignee: { type: "string", default: "Farhan" },
        maxItems: { type: "number", default: 10 },
        forceRefresh: { type: "boolean", default: false },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "update_farhan_internal_status_marker",
    description: "Update Bug Tracker All internal status marker columns L/M/N on a specific Farhan row. Use this when Farhan says to mark a row for daily internal status: Plan Today, On Progress, Done, Blocker, or Skip. Defaults date to today when internalStatus is provided. Updates local cache immediately.",
    inputSchema: {
      type: "object",
      required: ["row"],
      properties: {
        row: { type: "number" },
        internalStatus: { type: "string", enum: ["Plan Today", "On Progress", "Done", "Blocker", "Skip"] },
        internalStatusDate: { type: "string", description: "Optional M/D/YYYY date. Defaults to today if internalStatus is provided." },
        internalStatusNote: { type: "string", description: "Short bullet text/commit/MR note for column N." },
        dryRun: { type: "boolean", default: false },
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "get_bug_tracker_cache_status",
    description: "Check if the local bug tracker cache is valid before querying. Use this at the start of a session to decide if forceRefresh is needed. Returns cache age, TTL, and row count.",
    inputSchema: {
      type: "object",
      properties: { sheetName: { type: "string", default: "Bug Tracker All" }, spreadsheetId: { type: "string" } },
      additionalProperties: false,
    },
  },
  {
    name: "refresh_bug_tracker_cache",
    description: "Force a live re-fetch of Bug Tracker All and rebuild local cache. Use when Farhan says the data looks outdated or after someone else edits the sheet.",
    inputSchema: {
      type: "object",
      properties: { sheetName: { type: "string", default: "Bug Tracker All" }, spreadsheetId: { type: "string" } },
      additionalProperties: false,
    },
  },
  {
    name: "get_farhan_tasks_missing_internal_status",
    description: "Evening check: list Farhan tasks with Dev Status=Done dev but Internal Status column L is still blank. Use before generating sore internal status to see what needs to be marked.",
    inputSchema: {
      type: "object",
      properties: { assignee: { type: "string", default: "Farhan" }, maxRows: { type: "number", default: 15 }, forceRefresh: { type: "boolean", default: false }, sheetName: { type: "string", default: "Bug Tracker All" }, spreadsheetId: { type: "string" } },
      additionalProperties: false,
    },
  },
];

async function getTasksMissingInternalStatus(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 15), 50));
  const { dataRows, header, sheetName, fromCache, cacheAge } = await loadBugTrackerCached(args, Boolean(args.forceRefresh));
  const matches = [];
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    if (!contains(cell(row, header.map, ["Dev"]), assignee)) return;
    if (normalize(cell(row, header.map, ["Dev Status"])) !== "done dev") return;
    const intStatus = normalize(cellByHeaderOrIndex(row, header.map, ["Internal Status"], 11));
    if (intStatus) return;
    matches.push({ row: rowNumber, menu: compact(cell(row, header.map, ["Menu"]), 80), description: compact(cell(row, header.map, ["Description"]), 160), devStatus: cell(row, header.map, ["Dev Status"]) });
  });
  return { source: fromCache ? `cache (${cacheAge}s ago)` : "live fetch", sheet: sheetName, assignee, count: matches.length, tip: "Tasks are Done dev but missing Internal Status (col L) and Internal Status Date (col M). Use mark_farhan_task_done or update_farhan_bug_tracker_task with internalStatus=Done to fill them.", tasks: matches.slice(0, maxRows) };
}

async function callTool(name, args) {
  if (name === "get_farhan_open_dev_tasks") return getOpenDevTasks(args);
  if (name === "summarize_farhan_bug_tracker") return summarize(args);
  if (name === "search_farhan_bug_tracker") return searchTasks(args);
  if (name === "scan_farhan_bug_tracker_task_queue") return scanTaskQueue(args);
  if (name === "mark_farhan_task_done") return markTaskDone(args);
  if (name === "update_farhan_internal_status_marker") return updateInternalStatusMarker(args);
  if (name === "update_farhan_bug_tracker_task") return updateTask(args);
  if (name === "generate_farhan_internal_status_draft") return generateInternalStatusDraft(args);
  if (name === "get_farhan_tasks_missing_internal_status") return getTasksMissingInternalStatus(args);
  if (name === "get_bug_tracker_cache_status") return getCacheStatus(args);
  if (name === "refresh_bug_tracker_cache") return refreshCache(args);
  throw new Error(`Unknown tool: ${name}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let request;
  try { request = JSON.parse(line); } catch { jsonError(null, -32700, "Parse error"); return; }
  const id = request.id ?? null;
  try {
    if (request.method === "initialize") {
      jsonResponse(id, { protocolVersion: request.params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: SERVER_NAME, version: SERVER_VERSION } });
      return;
    }
    if (request.method === "notifications/initialized" || request.method === "ping") { jsonResponse(id, {}); return; }
    if (request.method === "tools/list") { jsonResponse(id, { tools }); return; }
    if (request.method === "tools/call") {
      const name = request.params?.name;
      const args = request.params?.arguments || {};
      const result = await callTool(name, args);
      jsonResponse(id, { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] });
      return;
    }
    jsonError(id, -32601, `Method not found: ${request.method}`);
  } catch (error) {
    jsonError(id, -32000, error instanceof Error ? error.message : String(error));
  }
});
