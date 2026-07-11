#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";
import { spawn } from "node:child_process";

const SERVER_NAME = "hmx-hashmicro-chat";
const SERVER_VERSION = "0.3.0";
const CONFIG_PATH = process.env.HMX_HASHCHAT_CONFIG || "/home/adminftp/.config/openclaw-hashmicro-chat/config.json";
const DEFAULT_NODE = "/home/adminftp/.nvm/versions/node/v22.22.3/bin/node";
const DEFAULT_INTERNAL_STATUS_MCP = "/home/adminftp/farhan/openclaw-internal-status-mcp/server.mjs";
const DEFAULT_STATE_DIR = "/home/adminftp/.local/share/openclaw-hashmicro-chat";
const DEFAULT_CREDENTIAL_PATH = "/home/adminftp/.config/openclaw-hashmicro-chat/credential.env";
const LEGACY_CREDENTIAL_PATH = "/home/adminftp/farhan/openclaw-hashmicro-chat-mcp/credential.txt";

function jsonResponse(id, result) { process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n"); }
function jsonError(id, code, message) { process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n"); }
function nowIso() { return new Date().toISOString(); }
function compact(value, maxLength = 220) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  return clean.length <= maxLength ? clean : clean.slice(0, maxLength - 3).trimEnd() + "...";
}
function dateStamp(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}
async function pathExists(file) {
  try { await fs.access(file); return true; } catch { return false; }
}
async function readJson(file, fallback = null) {
  try { return JSON.parse(await fs.readFile(file, "utf8")); } catch { return fallback; }
}
async function writeJson(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(value, null, 2) + "\n", "utf8");
}
async function readTextIfExists(file) {
  try { return await fs.readFile(file, "utf8"); } catch { return ""; }
}
function cleanCredentialValue(value) {
  return String(value || "").trim().replace(/^['"]|['"]$/g, "");
}
function parseCredentialText(text) {
  const values = {};
  const rawLines = String(text || "").split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith("#"));
  for (const line of rawLines) {
    const eq = line.indexOf("=");
    const colon = line.indexOf(":");
    const splitAt = eq >= 0 ? eq : colon >= 0 ? colon : -1;
    if (splitAt < 0) continue;
    const key = line.slice(0, splitAt).trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_");
    const value = cleanCredentialValue(line.slice(splitAt + 1));
    if (["hashchat_username", "username", "user", "email", "login", "uname"].includes(key)) values.username = value;
    if (["hashchat_password", "password", "pass", "pwd"].includes(key)) values.password = value;
  }
  if ((!values.username || !values.password) && rawLines.length >= 2 && !rawLines[0].includes("=") && !rawLines[1].includes("=")) {
    values.username ||= cleanCredentialValue(rawLines[0]);
    values.password ||= cleanCredentialValue(rawLines[1]);
  }
  if (!values.username || !values.password) return null;
  return values;
}
async function readCredentials(cfg) {
  const sources = [
    { label: "config", file: cfg.credentialPath },
    { label: "legacy", file: cfg.legacyCredentialPath },
  ].filter((item) => item.file);
  for (const source of sources) {
    const text = await readTextIfExists(source.file);
    if (!text.trim()) continue;
    const parsed = parseCredentialText(text);
    if (parsed) return { ...parsed, source: source.label, file: source.file };
  }
  throw new Error("HashMicro Chat credential file is missing or cannot be parsed. Save username/password in the configured credential file with mode 600.");
}
async function removeCredentialFiles(cfg) {
  const deleted = [];
  const sources = [
    { label: "config", file: cfg.credentialPath },
    { label: "legacy", file: cfg.legacyCredentialPath },
  ];
  for (const source of sources) {
    if (!source.file) continue;
    try {
      await fs.rm(source.file, { force: true });
      deleted.push(source.label);
    } catch {}
  }
  return deleted;
}
async function locatorIsVisible(page, selector, timeout = 700) {
  try { return await page.locator(selector).first().isVisible({ timeout }); } catch { return false; }
}
async function fillFirstVisible(page, selectors, value) {
  for (const selector of selectors) {
    try {
      const locator = page.locator(selector).first();
      if (await locator.isVisible({ timeout: 1000 })) {
        await locator.fill(String(value), { timeout: 10000 });
        return selector;
      }
    } catch {}
  }
  return "";
}
async function clickFirstVisible(page, selectors) {
  for (const selector of selectors) {
    try {
      const locator = page.locator(selector).first();
      if (await locator.isVisible({ timeout: 1000 })) {
        await locator.click({ timeout: 10000 });
        return selector;
      }
    } catch {}
  }
  return "";
}
async function ensureRememberMe(page) {
  try {
    const checkbox = page.locator('input[type="checkbox"]').first();
    if (await checkbox.isVisible({ timeout: 1000 })) {
      if (!(await checkbox.isChecked().catch(() => false))) await checkbox.check({ force: true });
      return true;
    }
  } catch {}
  return false;
}
async function pageHasAuthToken(page) {
  try {
    return await page.evaluate(() => {
      const localToken = Boolean(localStorage.getItem("token"));
      const cookieToken = document.cookie.split(";").some((part) => part.trim().startsWith("token="));
      return Boolean(localToken || cookieToken);
    });
  } catch { return false; }
}
async function getPageAuthToken(page) {
  try {
    return await page.evaluate(() => {
      const localToken = localStorage.getItem("token") || "";
      if (localToken) return localToken;
      const tokenCookie = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("token="));
      return tokenCookie ? decodeURIComponent(tokenCookie.slice("token=".length)) : "";
    });
  } catch { return ""; }
}
const USERNAME_SELECTORS = [
  'input[type="email"]',
  'input[name*="email"]',
  'input[id*="email"]',
  'input[name*="login"]',
  'input[id*="login"]',
  'input[name*="user"]',
  'input[id*="user"]',
  'input[type="text"]',
];
const PASSWORD_SELECTORS = ['input[type="password"]', 'input[name*="password"]', 'input[id*="password"]'];
const NEXT_SELECTORS = ['button[type="submit"]', 'button:has-text("Next")', 'button:has-text("Continue")', 'button:has-text("Lanjut")'];
const SUBMIT_SELECTORS = [
  'button[type="submit"]',
  'input[type="submit"]',
  'button:has-text("Login")',
  'button:has-text("Log in")',
  'button:has-text("Sign in")',
  'button:has-text("Masuk")',
];
async function hasManualChallenge(page) {
  const selectors = [
    'input[name*="otp"]',
    'input[id*="otp"]',
    'input[name*="code"]',
    'input[id*="code"]',
    'iframe[src*="captcha"]',
    'text=/otp|verification|captcha|two-factor|2fa/i',
  ];
  for (const selector of selectors) {
    if (await locatorIsVisible(page, selector, 500)) return true;
  }
  return false;
}
async function hasChatSurface(page, cfg = {}) {
  const messageSurfaceVisible = await locatorIsVisible(page, 'textarea, [contenteditable="true"], [role="textbox"]', 1000);
  if (messageSurfaceVisible) return true;
  const groupName = String(cfg.groupName || "").trim();
  if (groupName) {
    try {
      if (await page.getByText(groupName, { exact: true }).first().isVisible({ timeout: 1000 })) return true;
    } catch {}
  }
  return false;
}
async function isLikelyLoggedIn(page, cfg = {}) {
  const passwordVisible = await locatorIsVisible(page, 'input[type="password"]', 800);
  if (passwordVisible) return false;
  const title = (await page.title().catch(() => "")).toLowerCase();
  const url = page.url().toLowerCase();
  const loginishPage = /login|signin|sign-in|auth/.test(title) || /login|signin|sign-in|auth/.test(url);
  if (loginishPage) return false;
  if (await pageHasAuthToken(page)) return true;
  return Boolean(await hasChatSurface(page, cfg));
}
async function bootstrapLogin(args = {}) {
  const cfg = await readConfig();
  if (cfg.browserRuntime !== "playwright") throw new Error("Only Playwright browser runtime is supported for HashMicro Chat login bootstrap.");
  const credentials = await readCredentials(cfg);
  const { chromium } = await import("playwright");
  await fs.mkdir(cfg.browserUserDataDir, { recursive: true });
  const headless = args.headless !== false;
  const keepLocalFile = Boolean(args.keepCredential || args.keepLocalFile);
  const timeout = Math.max(15000, Math.min(Number(args.timeoutMs || cfg.loginTimeoutMs || 60000), 180000));
  const context = await chromium.launchPersistentContext(cfg.browserUserDataDir, {
    headless,
    viewport: { width: 1366, height: 900 },
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  const page = context.pages()[0] || await context.newPage();
  let deletedCredentialFiles = [];
  try {
    await page.goto(cfg.chatUrl, { waitUntil: "domcontentloaded", timeout });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    if (!(await isLikelyLoggedIn(page, cfg))) {
      const usernameFilled = await fillFirstVisible(page, USERNAME_SELECTORS, credentials.username);
      let passwordFilled = await fillFirstVisible(page, PASSWORD_SELECTORS, credentials.password);
      if (usernameFilled && !passwordFilled) {
        const nextClicked = await clickFirstVisible(page, NEXT_SELECTORS);
        if (!nextClicked) await page.keyboard.press("Enter").catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
        passwordFilled = await fillFirstVisible(page, PASSWORD_SELECTORS, credentials.password);
      }
      if (passwordFilled) {
        await ensureRememberMe(page);
        const submitClicked = await clickFirstVisible(page, SUBMIT_SELECTORS);
        if (!submitClicked) await page.keyboard.press("Enter").catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
        await page.waitForTimeout(2500);
      }
    }
    const loggedIn = await isLikelyLoggedIn(page, cfg);
    const manualChallengeDetected = !loggedIn && await hasManualChallenge(page);
    if (loggedIn && !keepLocalFile) deletedCredentialFiles = await removeCredentialFiles(cfg);
    return {
      loginAttempted: true,
      loggedIn,
      sessionStored: loggedIn,
      browserProfileExists: await pathExists(cfg.browserUserDataDir),
      setupSource: credentials.source,
      setupFilesDeleted: deletedCredentialFiles.length > 0,
      deletedSetupFiles: deletedCredentialFiles,
      manualChallengeDetected,
        sendStillGuarded: !loggedIn,
      nextStep: loggedIn ? "Session saved. Real send can be enabled after target conversation is configured." : "Login did not complete automatically. Manual challenge or selector adjustment may be required.",
    };
  } finally {
    await context.close().catch(() => {});
  }
}
async function readConfig() {
  const fallback = {
    chatUrl: "https://chat.hashmicro.com/chat",
    groupName: "Farhan DevTeam - Support Team",
    sendEnabled: false,
    requireExplicitSend: true,
    nodePath: DEFAULT_NODE,
    internalStatusMcpPath: DEFAULT_INTERNAL_STATUS_MCP,
    stateDir: DEFAULT_STATE_DIR,
    browserUserDataDir: path.join(DEFAULT_STATE_DIR, "browser-profile"),
    browserRuntime: "playwright",
    selectorsConfigured: false,
    credentialPath: DEFAULT_CREDENTIAL_PATH,
    legacyCredentialPath: LEGACY_CREDENTIAL_PATH,
    loginTimeoutMs: 60000,
    conversationId: "",
  };
  const parsed = await readJson(CONFIG_PATH, {});
  return { ...fallback, ...parsed };
}
async function listDraftFiles(cfg) {
  const dir = path.join(cfg.stateDir, "drafts");
  try {
    const names = await fs.readdir(dir);
    return names.filter((n) => n.endsWith(".json")).map((n) => path.join(dir, n)).sort();
  } catch { return []; }
}
function callMcpTool({ nodePath, serverPath, name, args = {} }) {
  return new Promise((resolve, reject) => {
    const child = spawn(nodePath, [serverPath], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) { reject(new Error(stderr.trim() || `child MCP exited ${code}`)); return; }
      const lines = stdout.trim().split(/\n+/).filter(Boolean);
      for (const line of lines.reverse()) {
        try {
          const obj = JSON.parse(line);
          if (obj.id === 2) {
            if (obj.error) reject(new Error(obj.error.message || "MCP tool error"));
            else resolve(JSON.parse(obj.result?.content?.[0]?.text || "{}"));
            return;
          }
        } catch {}
      }
      reject(new Error("No tool response from internal-status MCP"));
    });
    const messages = [
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      { jsonrpc: "2.0", method: "notifications/initialized", params: {} },
      { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name, arguments: args } },
    ];
    child.stdin.end(messages.map((m) => JSON.stringify(m)).join("\n") + "\n");
  });
}
async function getStatus() {
  const cfg = await readConfig();
  const drafts = await listDraftFiles(cfg);
  const internalStatusMcpExists = await pathExists(cfg.internalStatusMcpPath);
  const browserProfileExists = await pathExists(cfg.browserUserDataDir);
  const credentialFileExists = await pathExists(cfg.credentialPath);
  const legacyCredentialFileExists = await pathExists(cfg.legacyCredentialPath);
  let playwrightAvailable = false;
  try { await import("playwright"); playwrightAvailable = true; } catch {}
  return {
    server: SERVER_NAME,
    chatUrlConfigured: Boolean(cfg.chatUrl),
    groupName: cfg.groupName,
    sendEnabled: Boolean(cfg.sendEnabled),
    selectorsConfigured: Boolean(cfg.selectorsConfigured),
    internalStatusMcpExists,
    browserRuntime: cfg.browserRuntime,
    playwrightAvailable,
    browserProfileExists,
    credentialFileExists,
    legacyCredentialFileExists,
    pendingDrafts: drafts.length,
    readyForDraft: internalStatusMcpExists,
    readyForLogin: Boolean(playwrightAvailable && (credentialFileExists || legacyCredentialFileExists)),
    readyForSend: Boolean(cfg.sendEnabled && cfg.selectorsConfigured && playwrightAvailable && browserProfileExists),
    sendGuard: cfg.sendEnabled ? "enabled_with_guards" : "disabled_until_explicit_setup",
  };
}
async function prepareDraft(args = {}) {
  const cfg = await readConfig();
  const period = args.period || "evening";
  const reportDate = args.reportDate || dateStamp();
  const draft = await callMcpTool({
    nodePath: cfg.nodePath,
    serverPath: cfg.internalStatusMcpPath,
    name: "generate_farhan_internal_status_draft",
    args: {
      period,
      reportDate,
      forceRefresh: Boolean(args.forceRefresh),
      forceMergeRequestRefresh: Boolean(args.forceMergeRequestRefresh),
      maxItems: args.maxItems,
      maxMergeRequests: args.maxMergeRequests,
      overrideSections: args.overrideSections,
    },
  });
  const draftId = `${reportDate}-${draft.period || period}-${Date.now()}`.replace(/[^a-zA-Z0-9_.-]+/g, "-");
  const file = path.join(cfg.stateDir, "drafts", `${draftId}.json`);
  const record = {
    draftId,
    createdAt: nowIso(),
    chatUrl: cfg.chatUrl,
    groupName: cfg.groupName,
    period: draft.period || period,
    reportDate: draft.reportDate || reportDate,
    counts: draft.counts || {},
    mergeRequestSource: draft.mergeRequestSource || "",
    message: draft.message || "",
    sentAt: null,
  };
  await writeJson(file, record);
  return {
    draftId,
    saved: true,
    draftFile: file,
    groupName: cfg.groupName,
    period: record.period,
    reportDate: record.reportDate,
    counts: record.counts,
    mergeRequestSource: record.mergeRequestSource,
    message: record.message,
  };
}
async function getDraftById(cfg, draftId) {
  if (!draftId) {
    const files = await listDraftFiles(cfg);
    if (!files.length) throw new Error("No draft exists. Run prepare_hashchat_internal_status_draft first.");
    return readJson(files[files.length - 1]);
  }
  const file = path.join(cfg.stateDir, "drafts", `${String(draftId).replace(/[^a-zA-Z0-9_.-]+/g, "-")}.json`);
  const draft = await readJson(file);
  if (!draft) throw new Error(`Draft not found: ${draftId}`);
  return draft;
}
async function persistDraftSent(cfg, draft) {
  if (!draft?.draftId || draft.draftId === "manual") return null;
  draft.sentAt = nowIso();
  draft.sentBy = SERVER_NAME;
  const file = path.join(cfg.stateDir, "drafts", `${draft.draftId}.json`);
  await writeJson(file, draft);
  return draft.sentAt;
}
async function openLoggedInHashChatPage(cfg, args = {}) {
  if (cfg.browserRuntime !== "playwright") throw new Error("Only Playwright browser runtime is supported for HashMicro Chat send.");
  const { chromium } = await import("playwright");
  await fs.mkdir(cfg.browserUserDataDir, { recursive: true });
  const headless = args.headless !== false;
  const timeout = Math.max(15000, Math.min(Number(args.timeoutMs || cfg.loginTimeoutMs || 60000), 180000));
  const context = await chromium.launchPersistentContext(cfg.browserUserDataDir, {
    headless,
    viewport: { width: 1366, height: 900 },
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  const page = context.pages()[0] || await context.newPage();
  try {
    await page.goto(cfg.chatUrl, { waitUntil: "domcontentloaded", timeout });
    await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
    if (await isLikelyLoggedIn(page, cfg)) return { context, page };
    let credentials = null;
    try { credentials = await readCredentials(cfg); } catch {}
    if (credentials) {
      await page.goto(new URL("/login", cfg.chatUrl).href, { waitUntil: "domcontentloaded", timeout });
      await fillFirstVisible(page, USERNAME_SELECTORS, credentials.username);
      const passwordFilled = await fillFirstVisible(page, PASSWORD_SELECTORS, credentials.password);
      if (passwordFilled) {
        await ensureRememberMe(page);
        const submitClicked = await clickFirstVisible(page, SUBMIT_SELECTORS);
        if (!submitClicked) await page.keyboard.press("Enter").catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
        await page.waitForTimeout(2500);
      }
    }
    if (!(await isLikelyLoggedIn(page, cfg))) throw new Error("HashMicro Chat browser session is not logged in. Run setup_hashchat_browser_session again.");
    return { context, page };
  } catch (error) {
    await context.close().catch(() => {});
    throw error;
  }
}
async function sendHashChatMessage(cfg, message, args = {}) {
  if (!cfg.conversationId) throw new Error("HashMicro Chat target conversation is not configured yet.");
  const { context, page } = await openLoggedInHashChatPage(cfg, args);
  try {
    const token = await getPageAuthToken(page);
    if (!token) throw new Error("HashMicro Chat auth token is missing from the browser session.");
    const result = await page.evaluate(async ({ conversationId, message, deleteAfterSend }) => {
      const tokenCookie = document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("token="));
      const token = localStorage.getItem("token") || (tokenCookie ? decodeURIComponent(tokenCookie.slice("token=".length)) : "");
      const form = new FormData();
      form.append("conversation_id", String(conversationId));
      form.append("message_type", "text");
      form.append("message", String(message));
      const response = await fetch("/api/send-message", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: form });
      let body = null;
      try { body = await response.json(); } catch {}
      const messageId = body?.message_id;
      const result = { ok: response.ok, status: response.status, hasMessageId: Boolean(messageId), success: response.ok && Boolean(messageId), deletedAfterSend: false, deleteStatus: 0 };
      if (result.success && deleteAfterSend) {
        const deleteResponse = await fetch("/api/delete-messages", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ message_id_list: String(messageId), delete_from_every_one: "true", conversation_id: String(conversationId) }),
        });
        result.deleteStatus = deleteResponse.status;
        let deleteBody = null;
        try { deleteBody = await deleteResponse.json(); } catch {}
        result.deletedAfterSend = deleteResponse.ok && deleteBody?.success !== false;
      }
      return result;
    }, { conversationId: cfg.conversationId, message, deleteAfterSend: Boolean(args.deleteAfterSend) });
    if (!result.success) throw new Error(`HashMicro Chat send failed with status ${result.status || "unknown"}.`);
    return { delivered: true, status: result.status, messageIdCreated: result.hasMessageId, deletedAfterSend: result.deletedAfterSend, deleteStatus: result.deleteStatus || undefined };
  } finally {
    await context.close().catch(() => {});
  }
}
async function sendDraft(args = {}) {
  const cfg = await readConfig();
  const dryRun = args.dryRun !== false;
  const draft = args.message
    ? { draftId: "manual", message: String(args.message), groupName: cfg.groupName, reportDate: args.reportDate || dateStamp(), period: args.period || "manual" }
    : await getDraftById(cfg, args.draftId);
  if (dryRun) {
    return { dryRun: true, wouldSend: true, groupName: cfg.groupName, chatUrlConfigured: Boolean(cfg.chatUrl), draftId: draft.draftId, message: draft.message };
  }
  if (!cfg.sendEnabled) throw new Error("Real HashMicro Chat send is disabled. Set sendEnabled=true only after browser login/session and target conversation are configured.");
  if (!cfg.selectorsConfigured) throw new Error("HashMicro Chat target conversation is not configured yet. Browser automation cannot safely send.");
  const delivery = await sendHashChatMessage(cfg, draft.message, args);
  const sentAt = await persistDraftSent(cfg, draft);
  return { dryRun: false, sent: true, groupName: cfg.groupName, draftId: draft.draftId, sentAt, transport: "hashmicro-chat-api", deliveryStatus: delivery.status, messageIdCreated: delivery.messageIdCreated, deletedAfterSend: delivery.deletedAfterSend || undefined };
}
async function listDrafts(args = {}) {
  const cfg = await readConfig();
  const limit = Math.max(1, Math.min(Number(args.limit || 10), 50));
  const files = (await listDraftFiles(cfg)).slice(-limit).reverse();
  const items = [];
  for (const file of files) {
    const d = await readJson(file);
    if (!d) continue;
    items.push({ draftId: d.draftId, createdAt: d.createdAt, period: d.period, reportDate: d.reportDate, groupName: d.groupName, sentAt: d.sentAt, preview: compact(d.message, 160) });
  }
  return { count: items.length, items };
}
async function markSent(args = {}) {
  const cfg = await readConfig();
  const draft = await getDraftById(cfg, args.draftId);
  draft.sentAt = args.sentAt || nowIso();
  draft.sentBy = "manual-confirmation";
  const file = path.join(cfg.stateDir, "drafts", `${draft.draftId}.json`);
  await writeJson(file, draft);
  return { markedSent: true, draftId: draft.draftId, sentAt: draft.sentAt };
}

const tools = [
  {
    name: "get_hashchat_connector_status",
    description: "Check HashMicro Chat connector readiness, draft count, and send guards. Does not read or send chat messages.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "prepare_hashchat_internal_status_draft",
    description: "Generate an internal status draft via hmx-internal-status and save it as a pending HashMicro Chat draft. Does not send chat messages.",
    inputSchema: { type: "object", properties: { period: { type: "string", enum: ["morning", "evening", "pagi", "sore"], default: "evening" }, reportDate: { type: "string", description: "YYYY-MM-DD. Defaults to today." }, forceRefresh: { type: "boolean", default: false }, forceMergeRequestRefresh: { type: "boolean", default: false }, maxItems: { type: "number", default: 10 }, maxMergeRequests: { type: "number", default: 5 }, overrideSections: { type: "object", description: "Optional section overrides passed to hmx-internal-status. Keys: doneYesterday, doneToday, planToday, planOverdue, onProgress, blocker." } }, additionalProperties: false },
  },
  {
    name: "setup_hashchat_browser_session",
    description: "Create a persistent HashMicro Chat browser profile from the local setup file. Does not expose saved values or chat content. Removes local setup files after success unless keepLocalFile=true.",
    inputSchema: { type: "object", properties: { headless: { type: "boolean", default: true }, keepLocalFile: { type: "boolean", default: false }, timeoutMs: { type: "number", default: 60000 } }, additionalProperties: false },
  },
  {
    name: "send_hashchat_draft",
    description: "Dry-run by default. Shows the pending draft that would be sent to HashMicro Chat. Real send is guarded until browser session/selectors are configured.",
    inputSchema: { type: "object", properties: { draftId: { type: "string" }, message: { type: "string" }, period: { type: "string" }, reportDate: { type: "string" }, dryRun: { type: "boolean", default: true } }, additionalProperties: false },
  },
  {
    name: "send_hashchat_message",
    description: "Send or dry-run a custom message to the configured HashMicro Chat group. Use only when Farhan explicitly asks to send. dryRun defaults true; real send stays guarded by connector config.",
    inputSchema: { type: "object", required: ["message"], properties: { message: { type: "string" }, period: { type: "string", default: "manual" }, reportDate: { type: "string" }, dryRun: { type: "boolean", default: true } }, additionalProperties: false },
  },
  {
    name: "list_hashchat_drafts",
    description: "List compact metadata for pending/saved HashMicro Chat drafts without exposing full chat history.",
    inputSchema: { type: "object", properties: { limit: { type: "number", default: 10 } }, additionalProperties: false },
  },
  {
    name: "mark_hashchat_draft_sent",
    description: "Mark a saved draft as manually sent after Farhan confirms it was posted. Does not send chat messages.",
    inputSchema: { type: "object", required: ["draftId"], properties: { draftId: { type: "string" }, sentAt: { type: "string" } }, additionalProperties: false },
  },
];
async function callTool(name, args = {}) {
  if (name === "get_hashchat_connector_status") return getStatus(args);
  if (name === "prepare_hashchat_internal_status_draft") return prepareDraft(args);
  if (name === "setup_hashchat_browser_session") return bootstrapLogin(args);
  if (name === "send_hashchat_draft") return sendDraft(args);
  if (name === "send_hashchat_message") return sendDraft({ ...args, draftId: undefined });
  if (name === "list_hashchat_drafts") return listDrafts(args);
  if (name === "mark_hashchat_draft_sent") return markSent(args);
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
