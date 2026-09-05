#!/usr/bin/env node
"use strict";

const mcs = require("node-mcstatus");

const host = process.argv[2];
const port = Number(process.argv[3] || 25565);

if (!host) {
  console.error("usage: node check-status.js <host> [port]");
  process.exit(1);
}

mcs
  .statusJava(host, port, { query: false })
  .then((result) => {
    const players = result.players || {};
    console.log(
      JSON.stringify({
        online: Boolean(result.online),
        host: result.host,
        port: result.port,
        players: players.online || 0,
        max: players.max || 0,
        motd: result.motd && result.motd.clean ? result.motd.clean : "",
      })
    );
    process.exit(result.online ? 0 : 2);
  })
  .catch((err) => {
    console.error(JSON.stringify({ online: false, error: err.message }));
    process.exit(1);
  });
