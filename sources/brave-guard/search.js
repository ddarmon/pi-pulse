#!/usr/bin/env node

import { braveSearch } from "./guard.js";

const args = process.argv.slice(2);
let count = 5;
let country = "US";
let freshness = null;
const queryParts = [];

for (let i = 0; i < args.length; i += 1) {
  if (args[i] === "-n" && args[i + 1]) {
    count = Number(args[++i]);
  } else if (args[i] === "--country" && args[i + 1]) {
    country = args[++i];
  } else if (args[i] === "--freshness" && args[i + 1]) {
    freshness = args[++i];
  } else {
    queryParts.push(args[i]);
  }
}

try {
  process.stdout.write(`${await braveSearch(queryParts.join(" "), { count, country, freshness })}\n`);
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
}
