#!/usr/bin/env node

import { fetchPage } from "./guard.js";

const url = process.argv[2];
if (!url) {
  console.error("Usage: content.js <url>");
  process.exit(2);
}

try {
  process.stdout.write(await fetchPage(url));
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exitCode = 1;
}
