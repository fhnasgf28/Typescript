#!/usr/bin/env node
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

const SERVER_NAME = "hmx-mr-log";
const SERVER_VERSION = "1.0.0";
const DEFAULT_CONFIG_PATH = process.env.OPENCLAW_MR_LOG_CONFIG || "/home/adminftp/.config/openclaw-mr-log/config.json";
const SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets";
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/spreadsheets";
const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const XLSX_HELPER = "/home/adminftp/farhan/openclaw-mr-log-mcp/xlsx_helper.py";
const MAX_LOOKBACK = 100;

function jsonResponse(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function jsonError(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

function asTextResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

function compact(value, maxLength = 180) {
  const clean = String(value || "").replace(/https?:\/\/\S+/g, "[link]").replace(/\s+/g, " ").trim();
  if (clean.length <= maxLength) return clean;
  return clean.slice(0, maxLength - 3).trimEnd() + "...";
}

function normalize(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function normalizeHeader(value) {
  return normalize(value).replace(/[^a-z0-9 ]/g, "").trim();
}

function isBlank(value) {
  return String(value || "").trim() === "";
}

function base64url(value) {
  const buffer = Buffer.isBuffer(value) ? value : Buffer.from(value);
  return buffer.toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function columnLetter(index) {
  let value = Number(index) + 1;
  let letters = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    letters = String.fromCharCode(65 + remainder) + letters;
    value = Math.floor((value - 1) / 26);
  }
  return letters;
}

function sheetA1Name(name) {
  return `'${String(name).replace(/'/g, "''")}'`;
}

async function loadConfig() {
  const raw = await fs.readFile(DEFAULT_CONFIG_PATH, "utf8");
  const cfg = JSON.parse(raw);
  const required = ["gitlabBaseUrl", "gitlabTokenPath", "googleCredentialsPath", "spreadsheetId", "sheetId"];
  for (const key of required) {
    if (isBlank(cfg[key])) throw new Error(`MR log config is missing ${key}`);
  }
  return cfg;
}

async function readGitLabToken(tokenPath) {
  let raw;
  try {
    raw = await fs.readFile(tokenPath, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") throw new Error("GitLab token file is missing");
    throw error;
  }
  const known = raw.match(/(glpat|gloas|glrt|glcbt)-\S+/);
  if (known) return known[0].trim().replace(/^[`"']+|[`"')\]}>.,;:]+$/g, "");
  for (let line of raw.split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice(7).trim();
    if (line.includes("=")) line = line.split("=").slice(1).join("=").trim();
    line = line.replace(/^['"]|['"]$/g, "").trim();
    if (line) return line;
  }
  throw new Error("GitLab token file does not contain a token");
}

function requestText(method, targetUrl, { headers = {}, body = "", timeoutMs = 30000 } = {}, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.request(targetUrl, { method, headers, timeout: timeoutMs }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume();
        resolve(requestText(method, new URL(res.headers.location, targetUrl).toString(), { headers, body, timeoutMs }, redirectCount + 1));
        return;
      }
      let responseBody = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { responseBody += chunk; });
      res.on("end", () => resolve({ status, body: responseBody }));
    });
    req.on("timeout", () => req.destroy(new Error("request timeout")));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function requestBuffer(method, targetUrl, { headers = {}, body = "", timeoutMs = 30000 } = {}, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    const req = https.request(targetUrl, { method, headers, timeout: timeoutMs }, (res) => {
      const status = res.statusCode || 0;
      if ([301, 302, 303, 307, 308].includes(status) && res.headers.location && redirectCount < 5) {
        res.resume();
        resolve(requestBuffer(method, new URL(res.headers.location, targetUrl).toString(), { headers, body, timeoutMs }, redirectCount + 1));
        return;
      }
      const chunks = [];
      res.on("data", (chunk) => { chunks.push(Buffer.from(chunk)); });
      res.on("end", () => resolve({ status, body: Buffer.concat(chunks), headers: res.headers }));
    });
    req.on("timeout", () => req.destroy(new Error("request timeout")));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function requestJson(method, url, options = {}) {
  const result = await requestText(method, url, options);
  let parsed = null;
  try { parsed = result.body ? JSON.parse(result.body) : null; } catch {}
  if (result.status < 200 || result.status >= 300) {
    const provider = url.includes("gitlab") ? "GitLab" : url.includes("google") || url.includes("sheets") ? "Google" : "HTTP";
    const apiStatus = parsed?.error?.status || parsed?.message || parsed?.error || "";
    throw new Error(`${provider} API failed with HTTP ${result.status}${apiStatus ? ` (${compact(apiStatus, 80)})` : ""}`);
  }
  return parsed;
}

async function getGitLabClient(config) {
  const token = await readGitLabToken(config.gitlabTokenPath);
  const baseUrl = String(config.gitlabBaseUrl).replace(/\/$/, "");
  const headers = { "PRIVATE-TOKEN": token, "User-Agent": "OpenClaw MR Log MCP" };
  async function api(path, params = {}) {
    const url = new URL(`${baseUrl}/api/v4${path}`);
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    }
    return requestJson("GET", url.toString(), { headers });
  }
  const user = await api("/user");
  if (!user?.id) throw new Error("GitLab token did not return an authenticated user");
  return { api, user };
}

async function getGoogleAccessToken(credentialsPath, scope = SHEETS_SCOPE) {
  let credential;
  try {
    credential = JSON.parse(await fs.readFile(credentialsPath, "utf8"));
  } catch (error) {
    if (error && error.code === "ENOENT") throw new Error("Google service account file is missing");
    throw new Error("Google service account file is not valid JSON");
  }
  if (!credential.client_email || !credential.private_key) {
    throw new Error("Google credential must be a service account JSON with client_email and private_key");
  }
  const now = Math.floor(Date.now() / 1000);
  const assertionHeader = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const assertionClaims = base64url(JSON.stringify({
    iss: credential.client_email,
    scope,
    aud: credential.token_uri || "https://oauth2.googleapis.com/token",
    exp: now + 3600,
    iat: now,
  }));
  const unsigned = `${assertionHeader}.${assertionClaims}`;
  const signer = crypto.createSign("RSA-SHA256");
  signer.update(unsigned);
  signer.end();
  const assertion = `${unsigned}.${base64url(signer.sign(credential.private_key))}`;
  const body = new URLSearchParams({
    grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
    assertion,
  }).toString();
  const token = await requestJson("POST", credential.token_uri || "https://oauth2.googleapis.com/token", {
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Content-Length": Buffer.byteLength(body),
    },
    body,
  });
  if (!token.access_token) throw new Error("Google OAuth did not return an access token");
  return token.access_token;
}

async function getSheetsAccessToken(credentialsPath) {
  return getGoogleAccessToken(credentialsPath, SHEETS_SCOPE);
}

async function getDriveAccessToken(credentialsPath) {
  return getGoogleAccessToken(credentialsPath, DRIVE_SCOPE);
}

async function sheetsApiRequest(method, apiPath, accessToken, payload) {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  return requestJson(method, `https://sheets.googleapis.com${apiPath}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(body ? { "Content-Length": Buffer.byteLength(body) } : {}),
    },
    body,
  });
}


async function driveApiRequestJson(method, apiPath, accessToken, payload) {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  return requestJson(method, `https://www.googleapis.com${apiPath}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(body ? { "Content-Length": Buffer.byteLength(body) } : {}),
    },
    body,
  });
}

async function driveDownloadBuffer(fileId, accessToken) {
  const result = await requestBuffer("GET", `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(fileId)}?alt=media`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    timeoutMs: 60000,
  });
  if (result.status < 200 || result.status >= 300) throw new Error(`Drive download failed with HTTP ${result.status}`);
  return result.body;
}

async function driveUploadBuffer(fileId, accessToken, buffer) {
  const parsed = await requestJson("PATCH", `https://www.googleapis.com/upload/drive/v3/files/${encodeURIComponent(fileId)}?uploadType=media`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": XLSX_MIME,
      "Content-Length": buffer.length,
    },
    body: buffer,
    timeoutMs: 60000,
  });
  return parsed;
}

function runXlsxHelper(payload) {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", [XLSX_HELPER], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`XLSX helper failed with code ${code}${stderr ? `: ${compact(stderr, 180)}` : ""}`));
        return;
      }
      try { resolve(JSON.parse(stdout || "{}")); } catch (error) { reject(error); }
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

async function withTempXlsx(buffer, fn) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "openclaw-mr-log-"));
  const input = path.join(dir, "input.xlsx");
  const output = path.join(dir, "output.xlsx");
  try {
    await fs.writeFile(input, buffer);
    return await fn(input, output);
  } finally {
    await fs.rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}

function backupRoot(config) {
  return config.rollbackDir || path.join(os.homedir(), ".local", "share", "openclaw-mr-log", "backups");
}

function backupId() {
  return new Date().toISOString().replace(/[:.]/g, "-") + "-" + crypto.randomBytes(3).toString("hex");
}

function publicBackupInfo(manifest) {
  return {
    backupId: manifest.backupId,
    createdAt: manifest.createdAt,
    reason: manifest.reason || "",
    operation: manifest.operation || "",
    sheetName: manifest.sheetName || "",
    sourceBranch: manifest.sourceBranch || "",
    targetBranch: manifest.targetBranch || "",
    writeColumnLimit: manifest.writeColumnLimit || null,
    developerName: manifest.developerName || "",
    plannedCount: manifest.plannedCount || null,
    sizeBytes: manifest.sizeBytes || null,
  };
}

async function saveXlsxBackup(config, buffer, meta = {}) {
  const root = backupRoot(config);
  await fs.mkdir(root, { recursive: true, mode: 0o700 });
  const id = backupId();
  const workbookPath = path.join(root, id + ".xlsx");
  const manifestPath = path.join(root, id + ".json");
  const manifest = {
    backupId: id,
    createdAt: new Date().toISOString(),
    reason: meta.reason || "before write",
    operation: meta.operation || "backup",
    sheetName: config.xlsxSheetName || "HR",
    sourceBranch: config.gitlabSourceBranch || "",
    targetBranch: config.gitlabTargetBranch || "",
    writeColumnLimit: Number(config.writeColumnLimit || 0) || null,
    developerName: config.developerName || "Farhan",
    plannedCount: meta.plannedCount || null,
    sizeBytes: buffer.length,
  };
  await fs.writeFile(workbookPath, buffer, { mode: 0o600 });
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n", { mode: 0o600 });
  await pruneBackups(config).catch(() => {});
  return publicBackupInfo(manifest);
}

async function readBackupManifests(config) {
  const root = backupRoot(config);
  let names = [];
  try {
    names = await fs.readdir(root);
  } catch (error) {
    if (error && error.code === "ENOENT") return [];
    throw error;
  }
  const manifests = [];
  for (const name of names.filter((entry) => entry.endsWith(".json"))) {
    try {
      const manifest = JSON.parse(await fs.readFile(path.join(root, name), "utf8"));
      if (manifest.backupId) manifests.push(manifest);
    } catch {}
  }
  manifests.sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
  return manifests;
}

async function pruneBackups(config) {
  const keep = Math.max(1, Number(config.backupRetention || 20));
  const manifests = await readBackupManifests(config);
  const root = backupRoot(config);
  for (const manifest of manifests.slice(keep)) {
    await fs.rm(path.join(root, manifest.backupId + ".json"), { force: true }).catch(() => {});
    await fs.rm(path.join(root, manifest.backupId + ".xlsx"), { force: true }).catch(() => {});
  }
}

async function listMrLogBackups(config, args = {}) {
  const limit = Math.max(1, Math.min(Number(args.limit || 10), 50));
  const manifests = await readBackupManifests(config);
  return { count: manifests.length, items: manifests.slice(0, limit).map(publicBackupInfo) };
}

async function createMrLogBackup(config, args = {}) {
  const accessToken = await getDriveAccessToken(config.googleCredentialsPath);
  const buffer = await driveDownloadBuffer(config.spreadsheetId, accessToken);
  const backup = await saveXlsxBackup(config, buffer, {
    operation: "manual_checkpoint",
    reason: args.reason || "manual checkpoint before MR log changes",
  });
  return { created: true, backup };
}

async function restoreMrLogBackup(config, args = {}) {
  const dryRun = args.dryRun !== false;
  const backups = await readBackupManifests(config);
  if (!backups.length) throw new Error("No MR log backup checkpoint exists yet");
  const selected = args.backupId ? backups.find((entry) => entry.backupId === args.backupId) : backups[0];
  if (!selected) throw new Error("Requested MR log backup was not found");
  const backup = publicBackupInfo(selected);
  if (dryRun) return { dryRun: true, write: false, backup };
  const root = backupRoot(config);
  const buffer = await fs.readFile(path.join(root, selected.backupId + ".xlsx"));
  const accessToken = await getDriveAccessToken(config.googleCredentialsPath);
  await driveUploadBuffer(config.spreadsheetId, accessToken, buffer);
  return { dryRun: false, write: true, restored: true, backup };
}

async function getXlsxContext(config, { includeValues = true } = {}) {
  const accessToken = await getDriveAccessToken(config.googleCredentialsPath);
  let metadata;
  try {
    metadata = await driveApiRequestJson("GET", `/drive/v3/files/${encodeURIComponent(config.spreadsheetId)}?fields=mimeType,capabilities/canEdit`, accessToken);
  } catch (error) {
    throw new Error("Drive API cannot access the MR log file. Share the file with the configured service account as Editor.");
  }
  if (metadata.mimeType !== XLSX_MIME) throw new Error("MR log file is not a native Google Sheet and not an XLSX file supported by the in-place fallback.");
  if (!metadata.capabilities?.canEdit) throw new Error("Service account can read the MR log XLSX but does not have Editor permission.");
  if (!includeValues) return { mode: "xlsx", accessToken, canEdit: true, title: config.xlsxSheetName || "active", values: [] };
  const buffer = await driveDownloadBuffer(config.spreadsheetId, accessToken);
  const extracted = await withTempXlsx(buffer, (input) => runXlsxHelper({ op: "extract", workbook: input, sheetName: config.xlsxSheetName || "" }));
  return { mode: "xlsx", accessToken, canEdit: true, title: extracted.sheetName || config.xlsxSheetName || "active", values: extracted.values || [] };
}

async function writeXlsxRow(config, ctx, insertRowIndex, row, meta = {}) {
  const buffer = await driveDownloadBuffer(config.spreadsheetId, ctx.accessToken);
  let backup = null;
  if (!meta.skipBackup) {
    backup = await saveXlsxBackup(config, buffer, {
      operation: meta.operation || "before_single_write",
      reason: meta.reason || "before MR log write",
      plannedCount: meta.plannedCount || null,
    });
  }
  const updated = await withTempXlsx(buffer, async (input, output) => {
    await runXlsxHelper({ op: "insert", workbook: input, output, rowIndexZero: insertRowIndex, values: row, sheetName: config.xlsxSheetName || "" });
    return fs.readFile(output);
  });
  await driveUploadBuffer(config.spreadsheetId, ctx.accessToken, updated);
  return backup;
}

async function getSheetContext(config, { includeValues = true } = {}) {
  const accessToken = await getSheetsAccessToken(config.googleCredentialsPath);
  let metadata;
  try {
    metadata = await sheetsApiRequest("GET", `/v4/spreadsheets/${encodeURIComponent(config.spreadsheetId)}?fields=sheets.properties(sheetId,title,index,gridProperties(rowCount,columnCount))`, accessToken);
  } catch (error) {
    if (String(error.message || "").includes("FAILED_PRECONDITION") || String(error.message || "").includes("Office")) {
      return getXlsxContext(config, { includeValues });
    }
    throw error;
  }
  const sheet = (metadata.sheets || []).find((entry) => String(entry.properties?.sheetId) === String(config.sheetId));
  if (!sheet) throw new Error("Target sheet tab was not found by configured sheetId");
  const title = sheet.properties.title;
  if (!includeValues) return { mode: "sheets", accessToken, sheet, title, values: [] };
  const valuesResponse = await sheetsApiRequest("GET", `/v4/spreadsheets/${encodeURIComponent(config.spreadsheetId)}/values/${encodeURIComponent(sheetA1Name(title))}?majorDimension=ROWS`, accessToken);
  return { mode: "sheets", accessToken, sheet, title, values: valuesResponse.values || [] };
}

function detectHeader(values) {
  let best = { rowIndex: 0, score: -1, row: values[0] || [] };
  const needles = ["date", "tanggal", "mr", "merge", "link", "url", "project", "repo", "title", "judul", "status", "author", "pic", "branch"];
  const maxRows = Math.min(values.length, 10);
  for (let i = 0; i < maxRows; i += 1) {
    const row = values[i] || [];
    const joined = normalizeHeader(row.join(" "));
    const score = needles.reduce((total, needle) => total + (joined.includes(needle) ? 1 : 0), 0) + row.filter((cell) => !isBlank(cell)).length / 100;
    if (score > best.score) best = { rowIndex: i, score, row };
  }
  const names = best.row.map((cell) => normalizeHeader(cell));
  return { rowIndex: best.rowIndex, row: best.row, names };
}

function findColumn(header, patterns) {
  for (let i = 0; i < header.names.length; i += 1) {
    const name = header.names[i];
    if (!name) continue;
    if (patterns.some((pattern) => pattern.test(name))) return i;
  }
  return -1;
}

function mapColumns(header) {
  return {
    no: findColumn(header, [/^no$/, /^nomor$/, /^number$/]),
    date: findColumn(header, [/tanggal/, /date/, /created/]),
    project: findColumn(header, [/project/, /repo/, /repository/, /module/, /modul/]),
    title: findColumn(header, [/title/, /judul/, /merge request/, /^mr$/]),
    url: findColumn(header, [/link/, /url/]),
    sourceBranch: findColumn(header, [/source.*branch/, /branch.*source/, /^source$/]),
    targetBranch: findColumn(header, [/target.*branch/, /branch.*target/, /^target$/]),
    state: findColumn(header, [/^mr status$/, /^gitlab status$/, /^state$/]),
    author: findColumn(header, [/author/, /pic/, /developer/, /dev/]),
    note: findColumn(header, [/note/, /notes/, /keterangan/, /remark/]),
  };
}

function parseDate(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const iso = text.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (iso) return new Date(Date.UTC(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3])));
  const dmy = text.match(/(\d{1,2})[-/](\d{1,2})[-/](\d{4})/);
  if (dmy) return new Date(Date.UTC(Number(dmy[3]), Number(dmy[2]) - 1, Number(dmy[1])));
  const parsed = new Date(text);
  if (!Number.isNaN(parsed.getTime())) return parsed;
  return null;
}

function isoDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

function sheetDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return String(date.getUTCMonth() + 1) + "/" + String(date.getUTCDate()) + "/" + String(date.getUTCFullYear());
}

function mrRef(mr) {
  return `${mr.project_path || mr.references?.full?.split("!")[0] || "project"}!${mr.iid}`;
}

function projectPathFromWebUrl(webUrl) {
  const url = new URL(webUrl);
  const marker = "/-/merge_requests/";
  const markerIndex = url.pathname.indexOf(marker);
  if (markerIndex < 0) throw new Error("MR URL must contain /-/merge_requests/");
  const projectPath = decodeURIComponent(url.pathname.slice(1, markerIndex));
  const iid = url.pathname.slice(markerIndex + marker.length).split("/")[0];
  if (!projectPath || !iid) throw new Error("MR URL does not contain project path and IID");
  return { projectPath, iid };
}

function parseMrRef(value) {
  const text = String(value || "").trim();
  const match = text.match(/^(.+)!([0-9]+)$/);
  if (!match) throw new Error("mrRef must use project/path!iid format");
  return { projectPath: match[1], iid: match[2] };
}

async function fetchRecentMrs(config, { state = "all", maxRows = 10, updatedAfter = "" } = {}) {
  const { api, user } = await getGitLabClient(config);
  const perPage = Math.min(Math.max(Number(maxRows || 10), 1), MAX_LOOKBACK);
  const params = {
    scope: "all",
    state: state || "all",
    author_id: user.id,
    order_by: "updated_at",
    sort: "desc",
    per_page: perPage,
  };
  if (!isBlank(updatedAfter)) params.updated_after = updatedAfter;
  if (!isBlank(config.gitlabSourceBranch)) params.source_branch = config.gitlabSourceBranch;
  if (!isBlank(config.gitlabTargetBranch)) params.target_branch = config.gitlabTargetBranch;
  const endpoint = !isBlank(config.gitlabProjectPath)
    ? `/projects/${encodeURIComponent(config.gitlabProjectPath)}/merge_requests`
    : "/merge_requests";
  const rows = await api(endpoint, params);
  return (Array.isArray(rows) ? rows : []).map((mr) => ({
    ...mr,
    project_path: config.gitlabProjectPath || (mr.project_id ? mr.references?.full?.split("!")[0] : mr.project_path),
  }));
}

async function fetchMrByInput(config, args = {}) {
  const { api, user } = await getGitLabClient(config);
  let target;
  if (!isBlank(args.mrUrl)) target = projectPathFromWebUrl(args.mrUrl);
  else if (!isBlank(args.mrRef)) target = parseMrRef(args.mrRef);
  else if (args.latestUnlogged) {
    const unlogged = await findUnlogged(config, { maxRows: Number(args.maxRows || 20) });
    if (!unlogged.items.length) throw new Error("No unlogged MR found for token user");
    target = parseMrRef(unlogged.items[0].mrRef);
  } else {
    throw new Error("Provide mrUrl, mrRef, or latestUnlogged=true");
  }
  const projectPathEncoded = encodeURIComponent(target.projectPath);
  const mr = await api(`/projects/${projectPathEncoded}/merge_requests/${encodeURIComponent(target.iid)}`);
  if (!mr?.iid) throw new Error("GitLab MR was not found");
  if (String(mr.author?.id) !== String(user.id)) throw new Error("MR author does not match token user");
  return { ...mr, project_path: target.projectPath };
}

function summarizeMr(mr) {
  return {
    mrRef: mrRef(mr),
    title: compact(mr.title, 140),
    state: mr.state || "",
    createdDate: isoDate(mr.created_at),
    updatedDate: isoDate(mr.updated_at),
    sourceBranch: compact(mr.source_branch, 80),
    targetBranch: compact(mr.target_branch, 80),
  };
}

function existingMrKeys(values, header) {
  const keys = new Set();
  const start = header.rowIndex + 1;
  const urlPattern = /https?:\/\/[^\s]+\/-\/merge_requests\/\d+/g;
  const refPattern = /[A-Za-z0-9_.\-\/]+![0-9]+/g;
  for (let i = start; i < values.length; i += 1) {
    const rowText = String((values[i] || []).join(" "));
    for (const url of rowText.match(urlPattern) || []) {
      try {
        const { projectPath, iid } = projectPathFromWebUrl(url);
        keys.add(`${projectPath}!${iid}`);
      } catch {}
      keys.add(url);
    }
    for (const ref of rowText.match(refPattern) || []) keys.add(ref);
  }
  return keys;
}

async function findUnlogged(config, args = {}) {
  const recent = await fetchRecentMrs(config, { state: args.state || "all", maxRows: args.maxRows || 20, updatedAfter: args.updatedAfter || "" });
  const { values } = await getSheetContext(config, { includeValues: true });
  const header = detectHeader(values);
  const keys = existingMrKeys(values, header);
  const items = recent.filter((mr) => !keys.has(mr.web_url) && !keys.has(mrRef(mr))).map(summarizeMr);
  return { count: items.length, checked: recent.length, items };
}

function inferSortDirection(dateRows) {
  if (dateRows.length < 2) return "desc";
  let asc = 0;
  let desc = 0;
  for (let i = 1; i < dateRows.length; i += 1) {
    const prev = dateRows[i - 1].date.getTime();
    const cur = dateRows[i].date.getTime();
    if (cur > prev) asc += 1;
    if (cur < prev) desc += 1;
  }
  return asc > desc ? "asc" : "desc";
}

function nextNo(values, header, cols) {
  if (cols.no < 0) return "";
  let max = 0;
  for (let i = header.rowIndex + 1; i < values.length; i += 1) {
    const n = Number(String(values[i]?.[cols.no] || "").replace(/[^0-9.-]/g, ""));
    if (Number.isFinite(n) && n > max) max = n;
  }
  return max ? String(max + 1) : "1";
}

function buildRow(values, header, cols, mr, args = {}, config = {}) {
  const width = Math.max(header.row.length, ...Object.values(cols).filter((idx) => idx >= 0).map((idx) => idx + 1), 1);
  const row = Array(width).fill("");
  const assignments = {
    no: nextNo(values, header, cols),
    date: sheetDate(mr.created_at),
    project: mr.project_path || "",
    title: mr.title || "",
    url: mr.web_url || "",
    sourceBranch: mr.source_branch || "",
    targetBranch: mr.target_branch || "",
    state: mr.state || "",
    author: config.developerName || "Farhan",
    note: args.note || "",
  };
  for (const [key, value] of Object.entries(assignments)) {
    const idx = cols[key];
    if (idx >= 0) row[idx] = value;
  }
  return row;
}

function findInsertRowIndex(values, header, cols, mrDateText) {
  const dataStart = header.rowIndex + 1;
  const newDate = parseDate(mrDateText);
  if (!newDate || cols.date < 0) return values.length;
  const dateRows = [];
  for (let i = dataStart; i < values.length; i += 1) {
    const date = parseDate(values[i]?.[cols.date]);
    if (date) dateRows.push({ rowIndex: i, date });
  }
  if (!dateRows.length) return dataStart;
  const direction = inferSortDirection(dateRows);
  for (const row of dateRows) {
    if (direction === "desc" && row.date.getTime() < newDate.getTime()) return row.rowIndex;
    if (direction === "asc" && row.date.getTime() > newDate.getTime()) return row.rowIndex;
  }
  return Math.max(...dateRows.map((row) => row.rowIndex)) + 1;
}

async function logMr(config, args = {}) {
  const mr = await fetchMrByInput(config, args);
  const ctx = await getSheetContext(config, { includeValues: true });
  const header = detectHeader(ctx.values);
  const keys = existingMrKeys(ctx.values, header);
  if (keys.has(mr.web_url) || keys.has(mrRef(mr))) {
    return { alreadyLogged: true, mr: summarizeMr(mr), write: false };
  }
  const cols = mapColumns(header);
  const fullRow = buildRow(ctx.values, header, cols, mr, args, config);
  const writeColumnLimit = Math.max(1, Number(config.writeColumnLimit || fullRow.length));
  const row = fullRow.slice(0, writeColumnLimit);
  const insertRowIndex = findInsertRowIndex(ctx.values, header, cols, isoDate(mr.created_at));
  const a1Row = insertRowIndex + 1;
  const result = {
    alreadyLogged: false,
    dryRun: Boolean(args.dryRun),
    write: !args.dryRun,
    insertRow: a1Row,
    mappedColumns: Object.fromEntries(Object.entries(cols).filter(([, idx]) => idx >= 0 && idx < row.length).map(([key, idx]) => [key, columnLetter(idx)])),
    mr: summarizeMr(mr),
  };
  if (args.dryRun) return result;
  if (ctx.mode === "xlsx") {
    const backup = await writeXlsxRow(config, ctx, insertRowIndex, row, {
      skipBackup: args.skipBackup,
      operation: args.backupOperation || "before_single_write",
      reason: args.backupReason || "before MR log write",
      plannedCount: args.plannedCount || null,
    });
    if (backup) result.backup = backup;
    return result;
  }
  await sheetsApiRequest("POST", `/v4/spreadsheets/${encodeURIComponent(config.spreadsheetId)}:batchUpdate`, ctx.accessToken, {
    requests: [{
      insertDimension: {
        range: { sheetId: Number(config.sheetId), dimension: "ROWS", startIndex: insertRowIndex, endIndex: insertRowIndex + 1 },
        inheritFromBefore: insertRowIndex > header.rowIndex + 1,
      },
    }],
  });
  const range = `${sheetA1Name(ctx.title)}!A${a1Row}:${columnLetter(row.length - 1)}${a1Row}`;
  await sheetsApiRequest("PUT", `/v4/spreadsheets/${encodeURIComponent(config.spreadsheetId)}/values/${encodeURIComponent(range)}?valueInputOption=USER_ENTERED`, ctx.accessToken, {
    range,
    majorDimension: "ROWS",
    values: [row],
  });
  return result;
}


async function checkSetup(config) {
  const result = {
    gitlab: { ok: false },
    googleOauth: { ok: false },
    sheet: { ok: false, native: false },
    blockers: [],
  };
  try {
    await getGitLabClient(config);
    result.gitlab.ok = true;
  } catch (error) {
    result.gitlab.error = compact(error?.message || String(error), 180);
    result.blockers.push("GitLab token/API is not ready");
  }
  try {
    await getSheetsAccessToken(config.googleCredentialsPath);
    result.googleOauth.ok = true;
  } catch (error) {
    result.googleOauth.error = compact(error?.message || String(error), 180);
    result.blockers.push("Google service account OAuth is not ready");
  }
  try {
    const ctx = await getSheetContext(config, { includeValues: false });
    result.sheet.ok = true;
    result.sheet.native = ctx.mode === "sheets";
    result.sheet.xlsxInPlace = ctx.mode === "xlsx";
  } catch (error) {
    result.sheet.error = compact(error?.message || String(error), 220);
    if (String(error?.message || "").includes("Office/XLSX")) result.blockers.push("MR log file must be converted to native Google Sheet");
    else result.blockers.push("MR log sheet access is not ready");
  }
  result.ready = result.gitlab.ok && result.googleOauth.ok && result.sheet.ok;
  return result;
}

async function syncUnlogged(config, args = {}) {
  const dryRun = args.dryRun !== false;
  const unlogged = await findUnlogged(config, { maxRows: args.maxRows || 20, state: args.state || "all", updatedAfter: args.updatedAfter || "" });
  const limit = Math.min(Number(args.limit || unlogged.items.length || 0), unlogged.items.length);
  let backup = null;
  if (!dryRun && limit > 0) {
    const ctx = await getSheetContext(config, { includeValues: false });
    if (ctx.mode === "xlsx") {
      const buffer = await driveDownloadBuffer(config.spreadsheetId, ctx.accessToken);
      backup = await saveXlsxBackup(config, buffer, {
        operation: "before_sync",
        reason: args.backupReason || "before syncing unlogged MRs",
        plannedCount: limit,
      });
    }
  }
  const planned = [];
  for (const item of unlogged.items.slice(0, limit)) {
    planned.push(await logMr(config, {
      mrRef: item.mrRef,
      dryRun,
      note: args.note || "",
      skipBackup: Boolean(backup),
      backupOperation: "before_sync_item",
      backupReason: args.backupReason || "before syncing unlogged MRs",
      plannedCount: limit,
    }));
  }
  return { dryRun, checked: unlogged.checked, unloggedCount: unlogged.count, processed: planned.length, backup, items: planned };
}

const tools = [
  {
    name: "check_farhan_mr_log_setup",
    description: "Compact readiness check for GitLab token, Google service account, and MR log sheet type/access.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "get_farhan_recent_gitlab_mrs",
    description: "List recent GitLab merge requests authored by the configured token user. Compact output for MR logging.",
    inputSchema: {
      type: "object",
      properties: {
        state: { type: "string", description: "MR state: all, opened, closed, merged", default: "all" },
        maxRows: { type: "number", description: "Maximum MRs to return, capped at 100", default: 10 },
        updatedAfter: { type: "string", description: "Optional ISO datetime lower bound for updated_at" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "find_unlogged_farhan_mrs",
    description: "Compare Farhan/token-user GitLab MRs against the MR log sheet and return compact unlogged MR refs.",
    inputSchema: {
      type: "object",
      properties: {
        state: { type: "string", default: "all" },
        maxRows: { type: "number", default: 20 },
        updatedAfter: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "log_farhan_mr_to_sheet",
    description: "Insert one authored MR into the MR log sheet in date order. Use dryRun first unless the user explicitly asks to write.",
    inputSchema: {
      type: "object",
      properties: {
        mrUrl: { type: "string", description: "Optional GitLab MR URL" },
        mrRef: { type: "string", description: "Optional project/path!iid MR reference from this MCP" },
        latestUnlogged: { type: "boolean", description: "Log the latest unlogged MR for the token user" },
        dryRun: { type: "boolean", description: "Preview insertion without writing", default: true },
        maxRows: { type: "number", default: 20 },
        note: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "sync_farhan_unlogged_mrs_to_sheet",
    description: "Plan or write multiple unlogged authored MRs into the MR log sheet in date order. Defaults to dryRun=true.",
    inputSchema: {
      type: "object",
      properties: {
        dryRun: { type: "boolean", default: true },
        maxRows: { type: "number", default: 20 },
        limit: { type: "number" },
        state: { type: "string", default: "all" },
        updatedAfter: { type: "string" },
        note: { type: "string" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "create_mr_log_backup_checkpoint",
    description: "Create a fast rollback checkpoint of the current MR log XLSX before risky sheet changes.",
    inputSchema: {
      type: "object",
      properties: {
        reason: { type: "string", description: "Short rollback checkpoint reason" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "list_mr_log_backups",
    description: "List local MR log rollback checkpoints without downloading or scanning the sheet.",
    inputSchema: {
      type: "object",
      properties: {
        limit: { type: "number", default: 10 },
      },
      additionalProperties: false,
    },
  },
  {
    name: "restore_mr_log_backup",
    description: "Fast-restore the MR log XLSX from a rollback checkpoint. Defaults to dryRun=true; set dryRun=false to upload the backup.",
    inputSchema: {
      type: "object",
      properties: {
        backupId: { type: "string", description: "Optional backupId from list_mr_log_backups; latest is used when omitted" },
        dryRun: { type: "boolean", default: true },
      },
      additionalProperties: false,
    },
  },
];

async function callTool(name, args = {}) {
  const config = await loadConfig();
  if (name === "check_farhan_mr_log_setup") return checkSetup(config);
  if (name === "get_farhan_recent_gitlab_mrs") {
    const rows = await fetchRecentMrs(config, args);
    return { count: rows.length, items: rows.map(summarizeMr) };
  }
  if (name === "find_unlogged_farhan_mrs") return findUnlogged(config, args);
  if (name === "log_farhan_mr_to_sheet") return logMr(config, { dryRun: true, ...args });
  if (name === "sync_farhan_unlogged_mrs_to_sheet") return syncUnlogged(config, args);
  if (name === "create_mr_log_backup_checkpoint") return createMrLogBackup(config, args);
  if (name === "list_mr_log_backups") return listMrLogBackups(config, args);
  if (name === "restore_mr_log_backup") return restoreMrLogBackup(config, args);
  throw new Error(`Unknown tool: ${name}`);
}

async function handleMessage(message) {
  const { id, method, params } = message;
  if (method === "initialize") {
    jsonResponse(id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
    });
    return;
  }
  if (method === "notifications/initialized") return;
  if (method === "tools/list") {
    jsonResponse(id, { tools });
    return;
  }
  if (method === "tools/call") {
    try {
      const result = await callTool(params?.name, params?.arguments || {});
      jsonResponse(id, asTextResult(result));
    } catch (error) {
      jsonResponse(id, asTextResult({ error: compact(error?.message || String(error), 500) }));
    }
    return;
  }
  jsonError(id, -32601, `Method not found: ${method}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  try {
    await handleMessage(JSON.parse(line));
  } catch (error) {
    jsonError(null, -32700, compact(error?.message || String(error), 300));
  }
});
