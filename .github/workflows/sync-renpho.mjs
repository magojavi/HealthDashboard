// scripts/sync-renpho.mjs
//
// Fetches body-composition measurements from Renpho's cloud API and merges
// any new ones into data/weight.json, matching the repo's existing schema:
//
//   [date, weight_kg, bmi, body_fat_pct, skeletal_muscle_pct, visceral_fat,
//    fat_free_weight_kg, subcutaneous_fat_pct, body_water_pct, muscle_mass_kg,
//    bone_mass_kg, protein_pct, bmr_kcal, metabolic_age, time]
//
// Auth/API logic adapted from the reverse-engineered Renpho Cloud API used by
// https://github.com/StartupBros-com/renpho-mcp-server (MIT licensed),
// itself credited to https://github.com/forkerer/RenphoGarminSync-CLI.
//
// Requires Node 18+ (built-in fetch). Run with:
//   RENPHO_EMAIL=... RENPHO_PASSWORD=... node scripts/sync-renpho.mjs

import fs from 'node:fs';
import crypto from 'node:crypto';

const API_BASE = 'https://cloud.renpho.com';
const ENCRYPTION_SECRET = 'ed*wijdi$h6fe3ew'; // fixed key used by Renpho's own apps
const WEIGHT_JSON_PATH = process.env.WEIGHT_JSON_PATH || 'data/weight.json';
const PAGE_SIZE = 200;

const EMAIL = process.env.RENPHO_EMAIL;
const PASSWORD = process.env.RENPHO_PASSWORD;

if (!EMAIL || !PASSWORD) {
  console.error('Missing RENPHO_EMAIL / RENPHO_PASSWORD environment variables.');
  process.exit(1);
}

function encryptAES(content) {
  const cipher = crypto.createCipheriv('aes-128-ecb', Buffer.from(ENCRYPTION_SECRET, 'utf8'), null);
  let encrypted = cipher.update(content, 'utf8', 'base64');
  encrypted += cipher.final('base64');
  return encrypted;
}

function encryptEmptyBytes() {
  const cipher = crypto.createCipheriv('aes-128-ecb', Buffer.from(ENCRYPTION_SECRET, 'utf8'), null);
  return Buffer.concat([cipher.update(Buffer.from([])), cipher.final()]).toString('base64');
}

function decryptAES(encryptedContent) {
  const decipher = crypto.createDecipheriv('aes-128-ecb', Buffer.from(ENCRYPTION_SECRET, 'utf8'), null);
  let decrypted = decipher.update(encryptedContent, 'base64', 'utf8');
  decrypted += decipher.final('utf8');
  return decrypted;
}

function extractIdsAsStrings(json, key) {
  const regex = new RegExp(`"${key}":(\\d+)`, 'g');
  return Array.from(json.matchAll(regex), (m) => m[1]);
}

function extractUserIdGroupsAsStrings(json) {
  const matches = json.matchAll(/"userIds":\[(\d+(?:,\d+)*)\]/g);
  return Array.from(matches, (m) => m[1].split(','));
}

async function postEncryptedRaw(session, path, requestBody, emptyBody = false) {
  const response = await fetch(`${API_BASE}/${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      token: session.token,
      userId: session.userId,
      appVersion: '7.0.0',
      platform: 'android'
    },
    body: JSON.stringify({
      encryptData: emptyBody ? encryptEmptyBytes() : encryptAES(JSON.stringify(requestBody ?? {}))
    })
  });

  const responseJson = await response.json();
  if (responseJson.code !== 101) {
    throw new Error(`API call failed for ${path}: code=${responseJson.code}, msg=${responseJson.msg}`);
  }
  if (!responseJson.data) {
    throw new Error(`API call failed for ${path}: no data in response`);
  }
  return decryptAES(responseJson.data);
}

async function authenticate() {
  const loginData = {
    questionnaire: {},
    login: {
      password: PASSWORD,
      areaCode: 'US',
      appRevision: '7.0.0',
      cellphoneType: 'MCP-Server',
      systemType: '11',
      email: EMAIL,
      platform: 'android'
    },
    bindingList: { deviceTypes: ['2'] }
  };

  const loginResponse = await fetch(`${API_BASE}/renpho-aggregation/user/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ encryptData: encryptAES(JSON.stringify(loginData)) })
  });

  const loginJson = await loginResponse.json();
  if (loginJson.code !== 101) {
    throw new Error(`Authentication failed: ${loginJson.msg}`);
  }

  const rawLoginData = decryptAES(loginJson.data);
  const userData = JSON.parse(rawLoginData);
  const login = userData.login;
  const userIdMatch = rawLoginData.match(/"id":(\d+)/);
  const userId = userIdMatch ? userIdMatch[1] : String(login.id);

  const tempSession = { token: login.token, userId };

  const rawDeviceData = await postEncryptedRaw(tempSession, 'renpho-aggregation/device/count', null, true);
  const deviceData = JSON.parse(rawDeviceData);
  const extractedUserIdGroups = extractUserIdGroupsAsStrings(rawDeviceData);

  if (!deviceData.scale || deviceData.scale.length === 0) {
    throw new Error('No scale devices found on this Renpho account.');
  }

  const scaleTables = deviceData.scale.map((scaleInfo, index) => ({
    table_name: scaleInfo.tableName,
    count: scaleInfo.count,
    user_ids: extractedUserIdGroups[index] || (scaleInfo.userIds || []).map(String)
  }));

  return { ...tempSession, scaleTables };
}

async function fetchAllMeasurementsForTable(session, table) {
  const pageSize = PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(Math.max(table.count || 0, pageSize) / pageSize));
  const collected = [];

  for (let pageNum = 1; pageNum <= totalPages; pageNum++) {
    const rawResponse = await postEncryptedRaw(session, 'RenphoHealth/scale/queryAllMeasureDataList', {
      pageNum,
      pageSize,
      userIds: table.user_ids,
      tableName: table.table_name
    });

    const parsed = JSON.parse(rawResponse);
    if (parsed.length === 0) break;

    const ids = extractIdsAsStrings(rawResponse, 'id');
    const boundUserIds = extractIdsAsStrings(rawResponse, 'bUserId');

    parsed.forEach((entry, index) => {
      collected.push({
        ...entry,
        __idString: ids[index] || String(entry.id),
        __bUserIdString: boundUserIds[index] || (entry.bUserId != null ? String(entry.bUserId) : undefined)
      });
    });
  }

  return collected;
}

// Renpho measurements are recorded in local (Berlin) time in the existing
// dataset, so we must convert from UTC to Europe/Berlin here — using raw
// UTC (as an earlier version of this script did) causes every measurement
// to look "new" against the existing dataset and duplicates the whole
// history on every run.
const BERLIN_TZ = 'Europe/Berlin';

function berlinParts(timeStampSeconds) {
  const date = new Date(timeStampSeconds * 1000);
  const formatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: BERLIN_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23'
  });
  const parts = Object.fromEntries(formatter.formatToParts(date).map((p) => [p.type, p.value]));
  return parts; // { year, month, day, hour, minute, second }
}

function toISODate(timeStampSeconds) {
  const p = berlinParts(timeStampSeconds);
  return `${p.year}-${p.month}-${p.day}`;
}

function toTimeString(timeStampSeconds) {
  const p = berlinParts(timeStampSeconds);
  // hour without leading zero, minute/second zero-padded — matches the
  // existing dataset's convention (e.g. "8:32:08", "10:12:37")
  const hour = String(Number(p.hour));
  return `${hour}:${p.minute}:${p.second}`;
}

function mapToWeightJsonRow(m) {
  const ts = Number(m.timeStamp);
  return [
    toISODate(ts),
    m.weight ?? null,
    m.bmi ?? null,
    m.bodyfat ?? null,
    m.muscle ?? null,          // skeletal_muscle_pct — "Músculo esquelético(%)"
    m.visfat ?? null,          // visceral_fat
    m.fatFreeWeight ?? null,   // fat_free_weight_kg
    m.subfat ?? null,          // subcutaneous_fat_pct
    m.water ?? null,           // body_water_pct
    m.sinew ?? null,           // muscle_mass_kg — NEEDS VERIFICATION, see README note
    m.bone ?? null,            // bone_mass_kg
    m.protein ?? null,         // protein_pct
    m.bmr ?? null,             // bmr_kcal
    m.bodyage ?? null,         // metabolic_age
    toTimeString(ts)
  ];
}

async function main() {
  console.log('Authenticating with Renpho...');
  const session = await authenticate();
  console.log(`Authenticated as user ${session.userId}. Found ${session.scaleTables.length} scale table(s).`);

  let allRaw = [];
  for (const table of session.scaleTables) {
    const rows = await fetchAllMeasurementsForTable(session, table);
    console.log(`  ${table.table_name}: ${rows.length} rows fetched`);
    allRaw.push(...rows);
  }

  // Keep only rows bound to the primary account user (skip family members on shared scales)
  const ownRows = allRaw.filter((r) => r.__bUserIdString === session.userId);
  const rowsToUse = ownRows.length > 0 ? ownRows : allRaw;

  const newRows = rowsToUse.map(mapToWeightJsonRow);
  console.log(`Mapped ${newRows.length} measurements from Renpho.`);

  let existing = [];
  if (fs.existsSync(WEIGHT_JSON_PATH)) {
    existing = JSON.parse(fs.readFileSync(WEIGHT_JSON_PATH, 'utf8'));
  }

  // De-dupe on (date, weight, bmi, bodyfat) rounded to 1 decimal — robust to
  // small float/precision differences between sources, unlike matching on
  // the exact time string.
  function fingerprint(r) {
    const round1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
    return `${r[0]}|${round1(r[1])}|${round1(r[2])}|${round1(r[3])}`;
  }
  const existingKeys = new Set(existing.map(fingerprint));
  const toAdd = newRows.filter((r) => !existingKeys.has(fingerprint(r)));

  console.log(`${toAdd.length} new row(s) to add (out of ${newRows.length} fetched, ${existing.length} already on file).`);

  if (toAdd.length === 0) {
    console.log('Nothing new — weight.json is already up to date.');
    return;
  }

  const merged = [...existing, ...toAdd].sort((a, b) => {
    if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
    return (a[14] || '').localeCompare(b[14] || '');
  });

  fs.writeFileSync(WEIGHT_JSON_PATH, JSON.stringify(merged));
  console.log(`Wrote ${merged.length} total rows to ${WEIGHT_JSON_PATH}.`);
}

main().catch((err) => {
  console.error('Sync failed:', err);
  process.exit(1);
});
