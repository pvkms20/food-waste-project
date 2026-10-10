// Runs a Python command using whichever interpreter exists (python3 or python).
const { spawnSync } = require("child_process");

function findPython() {
  for (const cmd of ["python3", "python", "py"]) {
    const r = spawnSync(cmd, ["--version"], { stdio: "ignore" });
    if (r.status === 0) return cmd;
  }
  console.error("Python not found. Install Python 3.10+ and try again.");
  process.exit(1);
}

function runPython(args) {
  const r = spawnSync(findPython(), args, { stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status || 1);
}

module.exports = { runPython };

if (require.main === module) runPython(process.argv.slice(2));
