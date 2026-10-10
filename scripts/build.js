// Build = make sure a dataset exists, then train and save the model.
// An existing data/waste_data.csv (for example a real Kaggle dataset) is never overwritten.
const fs = require("fs");
const { runPython } = require("./py");

if (!fs.existsSync("data/waste_data.csv")) {
  console.log("No dataset found, generating synthetic data...");
  runPython(["generate_data.py"]);
}
runPython(["train_model.py"]);

if (!fs.existsSync("model/model.pkl")) {
  console.error("Build failed: model/model.pkl was not created.");
  process.exit(1);
}
console.log("Build OK: model/model.pkl is ready.");
