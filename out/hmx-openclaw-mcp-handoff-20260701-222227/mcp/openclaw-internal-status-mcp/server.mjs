#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import https from "node:https";
import path from "node:path";
import readline from "node:readline";

const SERVER_NAME = "hmx-internal-status";
const SERVER_VERSION = "1.1.0";
const CONFIG_PATH = process.env.HMX_INTERNAL_STATUS_CONFIG || "/home/adminftp/.config/openclaw-internal-status/config.json";
const DEFAULT_SOURCE_CACHE = "/home/adminftp/.local/share/openclaw-bug-tracker/cache.json";
const DEFAULT_SHEETS_CREDENTIALS = "/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json";
const SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly";
const DEFAULT_MR_LOG_CONFIG = "/home/adminftp/.config/openclaw-mr-log/config.json";
const DEFAULT_MR_CACHE_FILE = "/home/adminftp/.local/share/openclaw-internal-status/mr-cache.json";

async function readConfig() {
  const fallback = {
    spreadsheetId: "1jJ3laj-APsCIhJWvs-IjTGZTb-hGDAHRn15FTwYbd_4",
    sheetName: "Bug Tracker All",
    assignee: "Farhan",
    timeZone: "Asia/Shanghai",
    chatGroupName: "Farhan DevTeam - Support Team",
    sourceCacheFile: DEFAULT_SOURCE_CACHE,
    credentialsPath: process.env.HMX_BUG_TRACKER_GOOGLE_CREDENTIALS || DEFAULT_SHEETS_CREDENTIALS,
    preferSheetsApi: true,
    cacheTtlMs: 8 * 60 * 1000,
    includeMergeRequests: true,
    mrLogConfigPath: DEFAULT_MR_LOG_CONFIG,
    mrCacheFile: DEFAULT_MR_CACHE_FILE,
    mrCacheTtlMs: 8 * 60 * 1000,
    maxMergeRequests: 5,
    sendEnabled: false,
  };
  try {
    const parsed = JSON.parse(await fs.readFile(CONFIG_PATH, "utf8"));
    return { ...fallback, ...parsed };
  } catch {
    return fallback;
  }
}

function jsonResponse(id, result) { process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n"); }
function jsonError(id, code, message) { process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n"); }
function normalize(value) { return String(value || "").trim().toLowerCase().replace(/\s+/g, " "); }
function isBlank(value) { return String(value || "").trim() === ""; }
function contains(value, needle) { return normalize(value).includes(normalize(needle)); }
function compact(value, maxLength = 220) {
  const clean = String(value || "").replace(/https?:\/\/\S+/g, "[link]").replace(/\s+/g, " ").trim();
  return clean.length <= maxLength ? clean : clean.slice(0, maxLength - 3).trimEnd() + "...";
}
function compactRaw(value, maxLength = 420) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  return clean.length <= maxLength ? clean : clean.slice(0, maxLength - 3).trimEnd() + "...";
}
function buildCsvUrl(spreadsheetId, sheetName) {
  const cacheBust = Date.now();
  return `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheetName)}&cacheBust=${cacheBust}`;
}
function base64url(value) {
  const buf = Buffer.isBuffer(value) ? value : Buffer.from(value);
  return buf.toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}
async function getSheetsAccessToken(credPath = DEFAULT_SHEETS_CREDENTIALS) {
  const cred = JSON.parse(await fs.readFile(credPath, "utf8"));
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
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Content-Length": Buffer.byteLength(body) },
    body,
  });
  const parsed = JSON.parse(text);
  if (!parsed.access_token) throw new Error("No access_token in Google OAuth response");
  return parsed.access_token;
}
function fetchText(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "OpenClaw Internal Status MCP", "Cache-Control": "no-cache" }, timeout: 20000 }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume(); resolve(fetchText(new URL(res.headers.location, url).toString(), redirectCount + 1)); return;
      }
      let body = ""; res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => status >= 200 && status < 300 ? resolve(body) : reject(new Error(`Spreadsheet export failed HTTP ${status}`)));
    });
    req.on("timeout", () => req.destroy(new Error("Spreadsheet export timeout")));
    req.on("error", reject);
  });
}
function requestText(method, targetUrl, { headers = {}, body = "" } = {}, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.request(targetUrl, { method, headers: { "User-Agent": "OpenClaw Internal Status MCP", ...headers }, timeout: 20000 }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume(); resolve(requestText(method, new URL(res.headers.location, targetUrl).toString(), { headers, body }, redirectCount + 1)); return;
      }
      let rb = ""; res.setEncoding("utf8");
      res.on("data", (c) => { rb += c; });
      res.on("end", () => status >= 200 && status < 300 ? resolve(rb) : reject(new Error(`HTTP ${status}: ${rb.slice(0, 200)}`)));
    });
    req.on("timeout", () => req.destroy(new Error("HTTP request timeout")));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}
function requestJson(url, { headers = {}, redirectCount = 0 } = {}) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { "User-Agent": "OpenClaw Internal Status MCP", ...headers }, timeout: 20000 }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume(); resolve(requestJson(new URL(res.headers.location, url).toString(), { headers, redirectCount: redirectCount + 1 })); return;
      }
      let body = ""; res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        let parsed = null;
        try { parsed = body ? JSON.parse(body) : null; } catch {}
        if (status < 200 || status >= 300) {
          const msg = parsed?.message || parsed?.error || body.slice(0, 120);
          reject(new Error(`GitLab API HTTP ${status}${msg ? `: ${compact(msg, 100)}` : ""}`)); return;
        }
        resolve(parsed);
      });
    });
    req.on("timeout", () => req.destroy(new Error("GitLab API timeout")));
    req.on("error", reject);
  });
}
async function readJsonFile(file) {
  try { return JSON.parse(await fs.readFile(file, "utf8")); } catch { return null; }
}
async function writeJsonFile(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(value, null, 2) + "\n", "utf8");
}
function extractGitLabToken(raw) {
  const text = String(raw || "").trim();
  const match = text.match(/glpat-[^\s`'\")]+/);
  return match ? match[0].trim() : text.split(/\s+/)[0];
}
async function readMrLogConfig(cfg) {
  const file = cfg.mrLogConfigPath || DEFAULT_MR_LOG_CONFIG;
  const parsed = await readJsonFile(file);
  return parsed ? { ...parsed, _configPath: file } : null;
}
async function fetchDailyMergeRequests(cfg, now, args = {}) {
  if (args.includeMergeRequests === false || cfg.includeMergeRequests === false) return { source: "disabled", items: [] };
  const mrCfg = await readMrLogConfig(cfg);
  if (!mrCfg?.gitlabBaseUrl || !mrCfg?.gitlabTokenPath) return { source: "not_configured", items: [] };
  const dayKey = dateKeyInTz(now, cfg.timeZone);
  const maxItems = Math.max(1, Math.min(Number(args.maxMergeRequests || cfg.maxMergeRequests || 5), 10));
  const cacheFile = cfg.mrCacheFile || DEFAULT_MR_CACHE_FILE;
  const cacheKey = [dayKey, mrCfg.gitlabProjectPath || "", mrCfg.gitlabSourceBranch || "", mrCfg.gitlabTargetBranch || "", maxItems].join("|");
  const cache = await readJsonFile(cacheFile);
  const ttl = Number(cfg.mrCacheTtlMs || cfg.cacheTtlMs || 480000);
  if (!args.forceMergeRequestRefresh && cache?.key === cacheKey && typeof cache.timestamp === "number" && Date.now() - cache.timestamp < ttl) {
    return { source: `cache(${Math.round((Date.now() - cache.timestamp) / 1000)}s)`, items: cache.items || [] };
  }
  try {
    const token = extractGitLabToken(await fs.readFile(mrCfg.gitlabTokenPath, "utf8"));
    const baseUrl = String(mrCfg.gitlabBaseUrl || "https://gitlab.com").replace(/\/$/, "");
    const headers = { "PRIVATE-TOKEN": token };
    const user = await requestJson(`${baseUrl}/api/v4/user`, { headers });
    if (!user?.id) throw new Error("GitLab token user unavailable");
    const endpoint = mrCfg.gitlabProjectPath
      ? `${baseUrl}/api/v4/projects/${encodeURIComponent(mrCfg.gitlabProjectPath)}/merge_requests`
      : `${baseUrl}/api/v4/merge_requests`;
    const start = new Date(`${dayKey}T00:00:00+08:00`);
    const params = new URLSearchParams({
      scope: "all",
      state: args.mrState || "all",
      author_id: String(user.id),
      order_by: "created_at",
      sort: "desc",
      per_page: String(Math.max(maxItems, 20)),
      created_after: start.toISOString(),
    });
    if (mrCfg.gitlabSourceBranch) params.set("source_branch", mrCfg.gitlabSourceBranch);
    if (mrCfg.gitlabTargetBranch) params.set("target_branch", mrCfg.gitlabTargetBranch);
    const rows = await requestJson(`${endpoint}?${params.toString()}`, { headers });
    const items = (Array.isArray(rows) ? rows : [])
      .filter((mr) => dateKeyInTz(new Date(mr.created_at || mr.createdAt || 0), cfg.timeZone) === dayKey)
      .slice(0, maxItems)
      .map((mr) => ({ title: compactRaw(mr.title || "Merge Request", 180), webUrl: String(mr.web_url || mr.webUrl || "").trim() }));
    await writeJsonFile(cacheFile, { key: cacheKey, timestamp: Date.now(), items });
    return { source: "gitlab", items };
  } catch (error) {
    return { source: "error", error: compact(error?.message || String(error), 160), items: [] };
  }
}

function parseCsv(text) {
  const rows = []; let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === "\"") { if (text[i + 1] === "\"") { field += "\""; i++; } else { quoted = false; } }
      else field += ch;
      continue;
    }
    if (ch === "\"") quoted = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += ch;
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
function deserializeSharedCache(raw) {
  if (!raw?.parsed?.header) return null;
  const header = raw.parsed.header;
  return {
    key: raw.key,
    timestamp: raw.timestamp,
    parsed: {
      rows: raw.parsed.rows,
      dataRows: raw.parsed.dataRows,
      header: { index: header.index, headers: header.headers, map: new Map(header.mapEntries || []) },
      sheetName: raw.parsed.sheetName,
      spreadsheetId: raw.parsed.spreadsheetId,
      source: raw.parsed.source,
      sourceWarning: raw.parsed.sourceWarning,
    },
  };
}
function serializeSharedCache(entry) {
  return {
    key: entry.key,
    timestamp: entry.timestamp,
    parsed: {
      rows: entry.parsed.rows,
      dataRows: entry.parsed.dataRows,
      header: { index: entry.parsed.header.index, headers: entry.parsed.header.headers, mapEntries: [...entry.parsed.header.map.entries()] },
      sheetName: entry.parsed.sheetName,
      spreadsheetId: entry.parsed.spreadsheetId,
      source: entry.parsed.source,
      sourceWarning: entry.parsed.sourceWarning,
    },
  };
}
function cacheValid(entry, cfg) { return entry && typeof entry.timestamp === "number" && Date.now() - entry.timestamp < Number(cfg.cacheTtlMs || 480000); }
async function readSharedCache(cfg) {
  try { return deserializeSharedCache(JSON.parse(await fs.readFile(cfg.sourceCacheFile || DEFAULT_SOURCE_CACHE, "utf8"))); }
  catch { return null; }
}
async function writeSharedCache(cfg, entry) {
  const file = cfg.sourceCacheFile || DEFAULT_SOURCE_CACHE;
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(serializeSharedCache(entry), null, 2) + "\n", "utf8");
}
async function fetchRowsViaSheetsApi(cfg, spreadsheetId, sheetName) {
  const token = await getSheetsAccessToken(cfg.credentialsPath || DEFAULT_SHEETS_CREDENTIALS);
  const range = encodeURIComponent(`'${String(sheetName).replace(/'/g, "''")}'`);
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values/${range}?valueRenderOption=FORMATTED_VALUE&dateTimeRenderOption=FORMATTED_STRING`;
  const text = await requestText("GET", url, { headers: { Authorization: `Bearer ${token}` } });
  const parsed = JSON.parse(text);
  return (parsed.values || []).filter((r) => r.some((v) => !isBlank(v)));
}
async function fetchRowsViaCsv(spreadsheetId, sheetName) {
  const text = await fetchText(buildCsvUrl(spreadsheetId, sheetName));
  return parseCsv(text).filter((r) => r.some((v) => !isBlank(v)));
}
async function fetchLiveRows(cfg, spreadsheetId, sheetName) {
  if (cfg.preferSheetsApi !== false && cfg.credentialsPath) {
    try {
      const rows = await fetchRowsViaSheetsApi(cfg, spreadsheetId, sheetName);
      return { rows, source: "sheets_api" };
    } catch (error) {
      const rows = await fetchRowsViaCsv(spreadsheetId, sheetName);
      return { rows, source: "public_csv_fallback", sourceWarning: compact(error?.message || String(error), 180) };
    }
  }
  return { rows: await fetchRowsViaCsv(spreadsheetId, sheetName), source: "public_csv" };
}
async function loadSource(args = {}, forceRefresh = false) {
  const cfg = await readConfig();
  const spreadsheetId = args.spreadsheetId || cfg.spreadsheetId;
  const sheetName = args.sheetName || cfg.sheetName;
  const key = `${spreadsheetId}::${sheetName}`;
  if (!forceRefresh) {
    const cached = await readSharedCache(cfg);
    if (cached && cached.key === key && cacheValid(cached, cfg)) {
      return { ...cached.parsed, fromCache: true, cacheAge: Math.round((Date.now() - cached.timestamp) / 1000), cfg };
    }
  }
  const live = await fetchLiveRows(cfg, spreadsheetId, sheetName);
  const rows = live.rows;
  const header = findHeader(rows);
  const parsed = { rows, dataRows: rows.slice(header.index + 1), header, sheetName, spreadsheetId, source: live.source, sourceWarning: live.sourceWarning };
  const entry = { key, timestamp: Date.now(), parsed };
  await writeSharedCache(cfg, entry);
  return { ...parsed, fromCache: false, cacheAge: 0, cfg };
}
function cell(row, map, names) {
  for (const n of names) { const i = map.get(normalize(n)); if (i !== undefined) return row[i] || ""; }
  return "";
}
function cellByHeaderOrIndex(row, map, names, fallbackIndex) {
  const value = cell(row, map, names);
  if (value !== "") return value;
  return Number.isInteger(fallbackIndex) ? (row[fallbackIndex] || "") : "";
}
function dateKeyInTz(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
function formatSheetDate(date, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, year: "numeric", month: "numeric", day: "numeric" }).formatToParts(date);
  const get = (type) => Number(parts.find((p) => p.type === type)?.value || 0);
  return `${get("month")}/${get("day")}/${get("year")}`;
}
function addDays(date, days) { const d = new Date(date.getTime()); d.setUTCDate(d.getUTCDate() + days); return d; }
function parseSheetDateKey(value, timeZone) {
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
      return dateKeyInTz(parsed, timeZone);
    }
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? "" : dateKeyInTz(parsed, timeZone);
}
function statusLine(row, header, rowNumber) {
  const note = compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status Note"], 13), 220);
  if (note) return note;
  const menu = compact(cell(row, header.map, ["Menu"]), 80);
  const moduleName = compact(cell(row, header.map, ["Modul", "Module"]), 80);
  const descRaw = compact(cell(row, header.map, ["Description"]), 160);
  const desc = descRaw.replace(/\[link\]/g, "").replace(/\s+/g, " ").trim();
  const prefix = [menu, moduleName].filter(Boolean).join(" - ");
  return compactRaw(prefix && desc ? `${prefix}: ${desc}` : (desc || prefix || `Row ${rowNumber}`), 240);
}
function buildStatusItem(row, header, rowNumber) {
  return {
    row: rowNumber,
    status: compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status"], 11), 80),
    date: compactRaw(cellByHeaderOrIndex(row, header.map, ["Internal Status Date"], 12), 40),
    line: statusLine(row, header, rowNumber),
  };
}
function renderBullets(items, emptyText = "None") { return items.length ? items.map((item) => `- ${item.line}`).join("\n") : `- ${emptyText}`; }
function dateMatches(item, key, timeZone, { allowBlank = false } = {}) {
  const parsed = parseSheetDateKey(item.date, timeZone);
  if (!parsed && allowBlank) return true;
  return parsed === key;
}
async function getStatus(args = {}) {
  const cfg = await readConfig();
  const cached = await readSharedCache(cfg);
  const valid = cached && cached.key === `${cfg.spreadsheetId}::${cfg.sheetName}` && cacheValid(cached, cfg);
  return {
    server: SERVER_NAME,
    cacheValid: Boolean(valid),
    sourceCacheFile: cfg.sourceCacheFile,
    cacheAgeSeconds: cached ? Math.round((Date.now() - cached.timestamp) / 1000) : null,
    ttlSeconds: Math.round(Number(cfg.cacheTtlMs || 480000) / 1000),
    sheet: cfg.sheetName,
    assignee: cfg.assignee,
    sendEnabled: Boolean(cfg.sendEnabled),
  };
}
async function refreshSource(args = {}) {
  const result = await loadSource(args, true);
  return {
    refreshed: true,
    sheet: result.sheetName,
    dataRows: result.dataRows.length,
    source: result.source || "live fetch",
    ...(result.sourceWarning ? { sourceWarning: result.sourceWarning } : {}),
  };
}
async function generateDraft(args = {}) {
  const result = await loadSource(args, Boolean(args.forceRefresh));
  const { dataRows, header, sheetName, fromCache, cacheAge, cfg, source, sourceWarning } = result;
  const periodArg = normalize(args.period || "morning");
  const period = periodArg.includes("sore") || periodArg.includes("evening") ? "evening" : "morning";
  const assignee = args.assignee || cfg.assignee;
  const maxItems = Math.max(1, Math.min(Number(args.maxItems || 10), 30));
  const now = args.reportDate ? new Date(`${args.reportDate}T00:00:00+08:00`) : new Date();
  const todayKey = dateKeyInTz(now, cfg.timeZone);
  const yesterdayKey = dateKeyInTz(addDays(now, -1), cfg.timeZone);
  const reportDate = formatSheetDate(now, cfg.timeZone);
  const shouldIncludeMergeRequests = period === "evening";
  const mergeRequests = shouldIncludeMergeRequests ? await fetchDailyMergeRequests(cfg, now, args) : { source: "skipped_morning", items: [] };
  // Short date like 29/6/26 for sore title
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: cfg.timeZone, year: "2-digit", month: "numeric", day: "numeric" }).formatToParts(now);
  const gp = (t) => parts.find((p) => p.type === t)?.value || "";
  const shortDate = `${gp("day")}/${gp("month")}/${gp("year")}`;

  const items = [];
  const devStatusDoneRows = []; // Dev Status = Done dev but no Internal Status
  for (let offset = 0; offset < dataRows.length; offset++) {
    const row = dataRows[offset];
    if (!contains(cell(row, header.map, ["Dev"]), assignee)) continue;
    const item = buildStatusItem(row, header, header.index + 2 + offset);
    // track Dev Status = Done dev rows missing Internal Status
    const devStatus = normalize(cell(row, header.map, ["Dev Status"]));
    if (devStatus === "done dev" && (!item.status || normalize(item.status) === "")) {
      const menu = compact(cell(row, header.map, ["Menu"]), 80);
      const desc = compact(cell(row, header.map, ["Description"]), 160);
      devStatusDoneRows.push({ row: item.row, brief: [menu, desc].filter(Boolean).join(": ") || `Row ${item.row}` });
    }
    if (!item.status || normalize(item.status) === "skip") continue;
    items.push(item);
  }

  // Filter helpers
  const byStatus = (status, key, opts = {}) =>
    items.filter((item) => normalize(item.status) === normalize(status) && dateMatches(item, key, cfg.timeZone, opts)).slice(0, maxItems);

  const doneYesterday = byStatus("Done", yesterdayKey);
  const doneToday = byStatus("Done", todayKey);
  const planToday = byStatus("Plan Today", todayKey, { allowBlank: true });
  // Plan Today overdue: planned for past dates but not yet done
  const planOverdue = items.filter((item) => {
    if (normalize(item.status) !== "plan today") return false;
    const d = parseSheetDateKey(item.date, cfg.timeZone);
    return d && d < todayKey;
  }).slice(0, maxItems);
  // On Progress/Blocker: sticky — show for any past date or blank
  const onProgress = items.filter((item) => {
    if (normalize(item.status) !== "on progress") return false;
    const d = parseSheetDateKey(item.date, cfg.timeZone);
    return !d || d <= todayKey;
  }).slice(0, maxItems);
  const blockers = items.filter((item) => {
    if (normalize(item.status) !== "blocker") return false;
    const d = parseSheetDateKey(item.date, cfg.timeZone);
    return !d || d <= todayKey;
  }).slice(0, maxItems);
  const doneMissingDateRows = items
    .filter((item) => normalize(item.status) === "done" && !parseSheetDateKey(item.date, cfg.timeZone))
    .map((item) => ({ row: item.row, line: item.line }));
  const warnings = [];
  if (doneMissingDateRows.length) {
    warnings.push({
      code: "done_internal_status_missing_date",
      message: `${doneMissingDateRows.length} row Internal Status=Done tidak punya Internal Status Date, jadi tidak masuk Done kemarin/Done hari ini tanpa override.`,
      rows: doneMissingDateRows,
    });
  }

  function appendMergeRequestSection(lines) {
    if (!mergeRequests.items.length) return;
    lines.push("");
    lines.push("Create Merge Request");
    for (const mr of mergeRequests.items) {
      if (mr.title) lines.push(`- ${mr.title}`);
      if (mr.webUrl) lines.push(mr.webUrl);
    }
  }

  const overrides = args.overrideSections && typeof args.overrideSections === "object" ? args.overrideSections : {};
  function overrideLines(key) {
    if (!Object.prototype.hasOwnProperty.call(overrides, key)) return null;
    const value = overrides[key];
    if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean);
    const text = String(value ?? "").trim();
    if (!text) return ["-"];
    return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  }
  function pushSection(lines, title, key, items, emptyText = "None") {
    lines.push(title);
    const custom = overrideLines(key);
    if (custom) {
      for (const line of custom) lines.push(line.startsWith("-") || line.startsWith("✅") ? line : `- ${line}`);
      return;
    }
    lines.push(renderBullets(items, emptyText));
  }

  // Build morning message
  function buildMorning() {
    const lines = [`Internal Status - Pagi (${reportDate})`, ""];
    pushSection(lines, "Done kemarin:", "doneYesterday", doneYesterday);
    lines.push("");
    pushSection(lines, "Plan hari ini:", "planToday", planToday, "belum ada task baru yang di assign hari ini");
    if (planOverdue.length > 0 || overrideLines("planOverdue")) {
      lines.push("");
      pushSection(lines, "Plan belum selesai:", "planOverdue", planOverdue);
    }
    lines.push("");
    pushSection(lines, "On progress:", "onProgress", onProgress);
    lines.push("");
    pushSection(lines, "Blocker:", "blocker", blockers);
    appendMergeRequestSection(lines);
    return lines.join("\n");
  }

  // Build evening message
  function buildEvening() {
    const title = `Internal Status - Sore (${reportDate})`;
    const lines = [title, ""];
    pushSection(lines, "Done hari ini:", "doneToday", doneToday);
    if (!overrideLines("doneToday") && devStatusDoneRows.length > 0) {
      lines.push(`- (${devStatusDoneRows.length} task ${doneToday.length ? "lain " : ""}Dev Status=Done dev belum diisi kolom L/M)`);
    }
    if (planOverdue.length > 0 || overrideLines("planOverdue")) {
      lines.push("");
      pushSection(lines, "Plan belum selesai:", "planOverdue", planOverdue);
    }
    lines.push("");
    pushSection(lines, "On progress:", "onProgress", onProgress);
    lines.push("");
    pushSection(lines, "Blocker:", "blocker", blockers);
    appendMergeRequestSection(lines);
    return lines.join("\n");
  }

  const message = period === "evening" ? buildEvening() : buildMorning();

  return {
    src: fromCache ? `cache(${cacheAge}s${source ? `/${source}` : ""})` : (source || "live"),
    ...(sourceWarning ? { sourceWarning } : {}),
    period,
    reportDate,
    counts: {
      doneYesterday: doneYesterday.length,
      doneToday: doneToday.length,
      planToday: planToday.length,
      planOverdue: planOverdue.length,
      onProgress: onProgress.length,
      blockers: blockers.length,
      devStatusDoneMissing: devStatusDoneRows.length,
      doneMissingDate: doneMissingDateRows.length,
      mergeRequests: mergeRequests.items.length,
    },
    warnings,
    mergeRequestSource: mergeRequests.source,
    message,
  };
}
const tools = [
  {
    name: "generate_farhan_internal_status_draft",
    description: "Generate Farhan's ready-to-copy morning/evening internal status draft for HashMicro Chat from Bug Tracker All marker columns L/M/N. Does not send messages.",
    inputSchema: { type: "object", properties: { period: { type: "string", enum: ["morning", "evening", "pagi", "sore"], default: "morning" }, reportDate: { type: "string", description: "Optional YYYY-MM-DD date. Defaults to today Asia/Shanghai." }, maxItems: { type: "number", default: 10 }, includeMergeRequests: { type: "boolean", default: true }, maxMergeRequests: { type: "number", default: 5 }, forceMergeRequestRefresh: { type: "boolean", default: false }, forceRefresh: { type: "boolean", default: false }, overrideSections: { type: "object", description: "Optional manual section overrides. Keys: doneYesterday, doneToday, planToday, planOverdue, onProgress, blocker. Values may be string or array of lines." } }, additionalProperties: false },
  },
  {
    name: "get_internal_status_source_status",
    description: "Check source cache state for internal status drafts without scanning the sheet.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "refresh_internal_status_source_cache",
    description: "Force refresh Bug Tracker All source cache before generating internal status drafts. Use only after sheet marker columns were edited or data is stale.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
];
async function callTool(name, args) {
  if (name === "generate_farhan_internal_status_draft") return generateDraft(args);
  if (name === "get_internal_status_source_status") return getStatus(args);
  if (name === "refresh_internal_status_source_cache") return refreshSource(args);
  throw new Error(`Unknown tool: ${name}`);
}
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let request;
  try { request = JSON.parse(line); } catch { jsonError(null, -32700, "Parse error"); return; }
  const id = request.id ?? null;
  try {
    if (request.method === "initialize") { jsonResponse(id, { protocolVersion: request.params?.protocolVersion || "2024-11-05", capabilities: { tools: {} }, serverInfo: { name: SERVER_NAME, version: SERVER_VERSION } }); return; }
    if (request.method === "notifications/initialized" || request.method === "ping") { jsonResponse(id, {}); return; }
    if (request.method === "tools/list") { jsonResponse(id, { tools }); return; }
    if (request.method === "tools/call") { const result = await callTool(request.params?.name, request.params?.arguments || {}); jsonResponse(id, { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] }); return; }
    jsonError(id, -32601, `Method not found: ${request.method}`);
  } catch (error) { jsonError(id, -32000, error instanceof Error ? error.message : String(error)); }
});
