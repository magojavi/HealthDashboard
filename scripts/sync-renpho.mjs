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

function
