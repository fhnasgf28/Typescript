#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs/promises";
import https from "node:https";
import readline from "node:readline";

const SERVER_NAME = "hmx-bug-tracker-sheets";
const SERVER_VERSION = "1.0.0";
const DEFAULT_SPREADSHEET_ID = process.env.HMX_BUG_TRACKER_SPREADSHEET_ID || "";
const DEFAULT_SHEET_NAME = process.env.HMX_BUG_TRACKER_SHEET_NAME || "Bug Tracker All";
const DEFAULT_ASSIGNEE = process.env.HMX_BUG_TRACKER_ASSIGNEE || "Farhan";
const DEFAULT_CREDENTIALS_PATH = process.env.HMX_BUG_TRACKER_GOOGLE_CREDENTIALS || process.env.GOOGLE_APPLICATION_CREDENTIALS || "/home/adminftp/.config/openclaw-sheet-tasks/google-service-account.json";
const SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets";

function jsonResponse(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function jsonError(id, code, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n");
}

function normalize(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function compact(value, maxLength = 220) {
  const clean = String(value || "")
    .replace(/https?:\/\/\S+/g, "[link]")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length <= maxLength) return clean;
  return clean.slice(0, maxLength - 3).trimEnd() + "...";
}

function compactRaw(value, maxLength = 420) {
  const clean = String(value || "")
    .replace(/\s+/g, " ")
    .trim();
  if (clean.length <= maxLength) return clean;
  return clean.slice(0, maxLength - 3).trimEnd() + "...";
}

function isBlank(value) {
  return String(value || "").trim() === "";
}

function contains(value, needle) {
  return normalize(value).includes(normalize(needle));
}

function buildCsvUrl(spreadsheetId, sheetName) {
  const encodedSheet = encodeURIComponent(sheetName);
  return `https://docs.google.com/spreadsheets/d/${spreadsheetId}/gviz/tq?tqx=out:csv&sheet=${encodedSheet}`;
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
        if (status < 200 || status >= 300) {
          reject(new Error(`Spreadsheet export failed with HTTP ${status}`));
          return;
        }
        resolve(body);
      });
    });
    req.on("timeout", () => {
      req.destroy(new Error("Spreadsheet export timeout"));
    });
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
      let responseBody = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { responseBody += chunk; });
      res.on("end", () => {
        if (status < 200 || status >= 300) {
          reject(new Error(`Google API request failed with HTTP ${status}: ${compact(responseBody, 300)}`));
          return;
        }
        resolve(responseBody);
      });
    });
    req.on("timeout", () => {
      req.destroy(new Error("Google API request timeout"));
    });
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

function base64url(value) {
  const buffer = Buffer.isBuffer(value) ? value : Buffer.from(value);
  return buffer.toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

async function getSheetsAccessToken(credentialsPath = DEFAULT_CREDENTIALS_PATH) {
  let raw;
  try {
    raw = await fs.readFile(credentialsPath, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      throw new Error(`Google Sheets write credential is not configured. Put a service account JSON at ${credentialsPath} or set HMX_BUG_TRACKER_GOOGLE_CREDENTIALS, then share the spreadsheet with that service account as Editor.`);
    }
    throw error;
  }

  let credential;
  try {
    credential = JSON.parse(raw);
  } catch {
    throw new Error(`Google Sheets credential file is not valid JSON: ${credentialsPath}`);
  }

  if (credential.web || credential.installed) {
    throw new Error(`Google Sheets credential file is an OAuth client secret, not a service account key. For unattended OpenClaw spreadsheet writes, use a service account JSON with client_email/private_key and share the spreadsheet to that service account as Editor.`);
  }

  if (!credential.client_email || !credential.private_key) {
    throw new Error(`Google Sheets credential file must contain client_email and private_key: ${credentialsPath}`);
  }

  const now = Math.floor(Date.now() / 1000);
  const assertionHeader = base64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const assertionClaims = base64url(JSON.stringify({
    iss: credential.client_email,
    scope: SHEETS_SCOPE,
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
  const text = await requestText("POST", credential.token_uri || "https://oauth2.googleapis.com/token", {
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "Content-Length": Buffer.byteLength(body),
    },
    body,
  });
  const parsed = JSON.parse(text);
  if (!parsed.access_token) throw new Error("Google OAuth response did not include access_token");
  return parsed.access_token;
}

async function sheetsApiRequest(method, apiPath, accessToken, payload) {
  const body = payload === undefined ? "" : JSON.stringify(payload);
  const text = await requestText(method, `https://sheets.googleapis.com${apiPath}`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      ...(body ? { "Content-Length": Buffer.byteLength(body) } : {}),
    },
    body,
  });
  return text ? JSON.parse(text) : {};
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

function columnIndex(header, names) {
  for (const name of names) {
    const index = header.map.get(normalize(name));
    if (index !== undefined) return index;
  }
  throw new Error(`Could not find column: ${names.join(" / ")}`);
}

function buildNoteAddition(args = {}) {
  const parts = [];
  if (!isBlank(args.note)) parts.push(String(args.note).trim());
  if (!isBlank(args.commit)) parts.push(`commit: ${String(args.commit).trim()}`);
  if (!isBlank(args.link)) parts.push(`link: ${String(args.link).trim()}`);
  return parts.join(" | ");
}

function mergeNotes(existing, addition, replaceNotes) {
  if (!addition) return String(existing || "");
  if (replaceNotes || isBlank(existing)) return addition;
  return `${String(existing).trim()}\n${addition}`;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === "\"") {
        if (text[i + 1] === "\"") {
          field += "\"";
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === "\"") {
      quoted = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  return rows;
}

function findHeader(rows) {
  for (let i = 0; i < Math.min(rows.length, 20); i += 1) {
    const normalized = rows[i].map(normalize);
    if (normalized.includes("dev") && normalized.includes("dev status")) {
      const map = new Map();
      rows[i].forEach((name, index) => {
        const key = normalize(name);
        if (key) map.set(key, index);
      });
      return { index: i, map, headers: rows[i] };
    }
  }
  throw new Error("Could not find header row with Dev and Dev Status columns");
}

function cell(row, map, names) {
  for (const name of names) {
    const index = map.get(normalize(name));
    if (index !== undefined) return row[index] || "";
  }
  return "";
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

async function loadBugTracker(args = {}) {
  const spreadsheetId = args.spreadsheetId || DEFAULT_SPREADSHEET_ID;
  const sheetName = args.sheetName || DEFAULT_SHEET_NAME;
  const text = await fetchText(buildCsvUrl(spreadsheetId, sheetName));
  const rows = parseCsv(text).filter((row) => row.some((value) => !isBlank(value)));
  const header = findHeader(rows);
  const dataRows = rows.slice(header.index + 1);
  return { rows, dataRows, header, sheetName, spreadsheetId };
}

async function getOpenDevTasks(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 12), 50));
  const { dataRows, header, sheetName } = await loadBugTracker(args);
  const matches = [];
  const allAssigneeRows = [];
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    const devStatus = cell(row, header.map, ["Dev Status"]);
    if (contains(dev, assignee)) {
      allAssigneeRows.push(row);
      if (isBlank(devStatus)) {
        matches.push(rowToTask(row, header.map, rowNumber));
      }
    }
  });
  return {
    sheet: sheetName,
    assignee,
    filter: "Dev contains assignee and Dev Status is blank",
    matched: matches.length,
    totalRowsForAssignee: allAssigneeRows.length,
    returned: Math.min(matches.length, maxRows),
    tasks: matches.slice(0, maxRows),
  };
}

async function summarize(args = {}) {
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const { dataRows, header, sheetName } = await loadBugTracker(args);
  const counts = new Map();
  let totalForAssignee = 0;
  let blank = 0;
  dataRows.forEach((row) => {
    const dev = cell(row, header.map, ["Dev"]);
    if (!contains(dev, assignee)) return;
    totalForAssignee += 1;
    const status = compact(cell(row, header.map, ["Dev Status"]), 80) || "(blank)";
    if (status === "(blank)") blank += 1;
    counts.set(status, (counts.get(status) || 0) + 1);
  });
  return {
    sheet: sheetName,
    assignee,
    totalRowsForAssignee: totalForAssignee,
    blankDevStatus: blank,
    byDevStatus: Object.fromEntries([...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]))),
  };
}

async function searchTasks(args = {}) {
  const query = String(args.query || "").trim();
  if (!query) throw new Error("query is required");
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const maxRows = Math.max(1, Math.min(Number(args.maxRows || 10), 50));
  const { dataRows, header, sheetName } = await loadBugTracker(args);
  const matches = [];
  dataRows.forEach((row, offset) => {
    const rowNumber = header.index + 2 + offset;
    const dev = cell(row, header.map, ["Dev"]);
    const haystack = row.join(" ");
    if (contains(dev, assignee) && contains(haystack, query)) {
      matches.push(rowToTask(row, header.map, rowNumber));
    }
  });
  return {
    sheet: sheetName,
    assignee,
    query,
    matched: matches.length,
    returned: Math.min(matches.length, maxRows),
    tasks: matches.slice(0, maxRows),
  };
}


async function updateTask(args = {}) {
  const rowNumber = Number(args.row);
  if (!Number.isInteger(rowNumber) || rowNumber < 1) throw new Error("row must be a positive spreadsheet row number from the read/search output");
  const assignee = args.assignee || DEFAULT_ASSIGNEE;
  const devStatus = args.devStatus === undefined ? "" : String(args.devStatus).trim();
  const noteAddition = buildNoteAddition(args);
  const attachment = args.attachment === undefined ? undefined : String(args.attachment).trim();
  if (!devStatus && !noteAddition && attachment === undefined) {
    throw new Error("Nothing to update. Provide devStatus, note, commit, link, or attachment.");
  }

  const { dataRows, header, sheetName, spreadsheetId } = await loadBugTracker(args);
  const offset = rowNumber - (header.index + 2);
  if (offset < 0 || offset >= dataRows.length) throw new Error(`row ${rowNumber} is outside the detected data table`);
  const row = dataRows[offset];
  const dev = cell(row, header.map, ["Dev"]);
  if (!contains(dev, assignee) && !args.allowOtherAssignee) {
    throw new Error(`row ${rowNumber} is not assigned to ${assignee}`);
  }

  const updates = [];
  const fields = [];
  if (devStatus) {
    updates.push({ index: columnIndex(header, ["Dev Status"]), value: devStatus });
    fields.push("Dev Status");
  }
  if (noteAddition) {
    updates.push({
      index: columnIndex(header, ["notes", "Notes"]),
      value: mergeNotes(cell(row, header.map, ["notes", "Notes"]), noteAddition, Boolean(args.replaceNotes)),
    });
    fields.push("notes");
  }
  if (attachment !== undefined) {
    updates.push({ index: columnIndex(header, ["Attachment (link)", "Attachment", "Link"]), value: attachment });
    fields.push("Attachment (link)");
  }

  const ranges = updates.map((update) => `${sheetA1Name(sheetName)}!${columnLetter(update.index)}${rowNumber}`);
  if (args.dryRun) {
    return {
      dryRun: true,
      sheet: sheetName,
      row: rowNumber,
      assignee,
      wouldUpdateFields: fields,
      ranges,
      currentTask: rowToTask(row, header.map, rowNumber),
    };
  }

  const accessToken = await getSheetsAccessToken(args.credentialsPath || DEFAULT_CREDENTIALS_PATH);
  const result = await sheetsApiRequest("POST", `/v4/spreadsheets/${encodeURIComponent(spreadsheetId)}/values:batchUpdate`, accessToken, {
    valueInputOption: "USER_ENTERED",
    data: updates.map((update, index) => ({ range: ranges[index], values: [[update.value]] })),
  });
  return {
    updated: true,
    sheet: sheetName,
    row: rowNumber,
    assignee,
    updatedFields: fields,
    updatedCells: result.totalUpdatedCells || updates.length,
  };
}

async function markTaskDone(args = {}) {
  return updateTask({ ...args, devStatus: "Done dev" });
}

const tools = [
  {
    name: "get_farhan_open_dev_tasks",
    description: "Use this whenever Farhan asks whether there are incoming spreadsheet/bug tracker tasks. Reads Bug Tracker All and returns Farhan tasks where Dev Status is blank. Output is compact and includes attachment links.",
    inputSchema: {
      type: "object",
      properties: {
        assignee: { type: "string", default: "Farhan" },
        maxRows: { type: "number", default: 12 },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string", description: "Optional Google spreadsheet id. Defaults to configured task tracker." },
      },
      additionalProperties: false,
    },
  },
  {
    name: "summarize_farhan_bug_tracker",
    description: "Use this whenever Farhan asks for a status/count summary. Summarize Farhan task counts in Bug Tracker All grouped by Dev Status without returning full row content.",
    inputSchema: {
      type: "object",
      properties: {
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string", description: "Optional Google spreadsheet id. Defaults to configured task tracker." },
      },
      additionalProperties: false,
    },
  },
  {
    name: "search_farhan_bug_tracker",
    description: "Use this whenever Farhan gives a keyword/module/menu/task phrase. Search Farhan rows in Bug Tracker All and return compact matching tasks with attachment links included.",
    inputSchema: {
      type: "object",
      required: ["query"],
      properties: {
        query: { type: "string" },
        assignee: { type: "string", default: "Farhan" },
        maxRows: { type: "number", default: 10 },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string", description: "Optional Google spreadsheet id. Defaults to configured task tracker." },
      },
      additionalProperties: false,
    },
  },
  {
    name: "mark_farhan_task_done",
    description: "Fast path for Farhan: use this when Farhan says a spreadsheet task is done/selesai/done dev. Do not inspect dropdown/options first. It writes Dev Status exactly 'Done dev' and can append note, commit, or link using the row number from read/search output.",
    inputSchema: {
      type: "object",
      required: ["row"],
      properties: {
        row: { type: "number", description: "Spreadsheet row number from get/search output." },
        note: { type: "string", description: "Optional note text to append into notes." },
        commit: { type: "string", description: "Optional commit hash/number to append into notes." },
        link: { type: "string", description: "Optional reference/MR link to append into notes." },
        dryRun: { type: "boolean", default: false },
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string", description: "Optional Google spreadsheet id. Defaults to configured task tracker." },
      },
      additionalProperties: false,
    },
  },
  {
    name: "update_farhan_bug_tracker_task",
    description: "Use this whenever Farhan asks to mark a spreadsheet task done, update Dev Status, add notes, add commit/link text, or update attachment. Updates one Farhan row in Bug Tracker All using the row number from read/search output. Use dryRun first unless Farhan explicitly asks to update now.",
    inputSchema: {
      type: "object",
      required: ["row"],
      properties: {
        row: { type: "number", description: "Spreadsheet row number from get/search output." },
        devStatus: { type: "string", description: "Example: Done dev" },
        note: { type: "string", description: "Note text to append into notes." },
        commit: { type: "string", description: "Commit hash/number to append into notes." },
        link: { type: "string", description: "Reference link to append into notes." },
        attachment: { type: "string", description: "Optional replacement for Attachment (link)." },
        replaceNotes: { type: "boolean", default: false },
        dryRun: { type: "boolean", default: false },
        assignee: { type: "string", default: "Farhan" },
        sheetName: { type: "string", default: "Bug Tracker All" },
        spreadsheetId: { type: "string", description: "Optional Google spreadsheet id. Defaults to configured task tracker." },
      },
      additionalProperties: false,
    },
  },
];

async function callTool(name, args) {
  if (name === "get_farhan_open_dev_tasks") return getOpenDevTasks(args);
  if (name === "summarize_farhan_bug_tracker") return summarize(args);
  if (name === "search_farhan_bug_tracker") return searchTasks(args);
  if (name === "mark_farhan_task_done") return markTaskDone(args);
  if (name === "update_farhan_bug_tracker_task") return updateTask(args);
  throw new Error(`Unknown tool ${name}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    jsonError(null, -32700, "Parse error");
    return;
  }
  const id = request.id ?? null;
  try {
    if (request.method === "initialize") {
      jsonResponse(id, {
        protocolVersion: request.params?.protocolVersion || "2024-11-05",
        capabilities: { tools: {} },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
      });
      return;
    }
    if (request.method === "notifications/initialized") return;
    if (request.method === "ping") {
      jsonResponse(id, {});
      return;
    }
    if (request.method === "tools/list") {
      jsonResponse(id, { tools });
      return;
    }
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
