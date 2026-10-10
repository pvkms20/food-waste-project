# TASK.md: Permanent specification and Definition of Done

Project: **Machine Learning-Based Food Waste Prediction and Blockchain-Based Donation Traceability System**

This file is the source of truth for future Claude Code sessions. When code and this file disagree, fix the code
(or, if the requirement is wrong, update this file deliberately). Do not weaken tests to make them pass.

## Operating mode (for autonomous sessions)

Work autonomously. Do not stop after writing a few files, hitting an error, a missing dependency, a failing test,
or starting the server. Loop: inspect -> implement -> run -> test -> read failures -> fix the cause -> retest,
until the Definition of Done is satisfied or a genuinely external blocker prevents it.
Make ordinary engineering decisions yourself. Prefer the simplest robust option. Do not over-engineer.
Never claim something works unless it was actually run. Do not delete or weaken tests to get green.
If the directory is a git repository: inspect state first, avoid destroying unrelated work, commit when stable.

## Project

A complete, locally runnable web application (academic/demo) combining:
1. ML food-waste prediction
2. Waste-risk classification
3. Donation / disposal decision logic
4. NGO donation management
5. Blockchain-based donation traceability
6. Blockchain integrity verification
7. Dashboard visualisation

Priorities: correctness, simplicity, reliability, understandable code, clean UI, demonstrable ML and blockchain.

## Technology

Python 3.10+, Flask, SQLite, HTML, CSS, Bootstrap (CDN), vanilla JavaScript, Chart.js (CDN) for charts.
Packages in `requirements.txt`: flask, pandas, numpy, scikit-learn, joblib, qrcode[pil], pytest.
Built-ins allowed: hashlib, json, datetime, sqlite3, os, pathlib, random, math.
Before installing packages inspect the environment, use a virtualenv where appropriate, verify imports, and
diagnose/fix installation failures instead of giving up.

## Dataset

`data/waste_data.csv`. Must work without any external download: generate a synthetic dataset if none exists.
Required columns:

```
meals_served, food_prepared_kg, kitchen_staff, day_of_week, temperature, humidity,
special_event, previous_waste_kg, waste_kg
```

Realistic, noisy, non-trivial relationships (waste correlates with meals served, food prepared, previous waste,
special events, and temperature/humidity to some degree). Waste must NOT be computable from a single column.

## Machine learning (`train_model.py`)

1. Load dataset. 2. Validate required columns. 3. Handle missing values. 4. Encode `day_of_week`.
5. Encode `special_event`. 6. Train/test split. 7. Train at least Random Forest Regressor and Linear Regression.
8. Compute MAE and R2. 9. Print metrics clearly. 10. Compare the models. 11. Save the best model to
`model/model.pkl`. 12. Save all preprocessing needed for inference.
Use a scikit-learn `Pipeline` (or equivalent) so inference uses exactly the training preprocessing.
The web app must NOT retrain on startup. Retraining is manual: `python train_model.py`.

## ML prediction

The user enters: meals served, food prepared (kg), kitchen staff, day of week, temperature, humidity,
special event, previous waste (kg). The backend returns predicted waste (kg) and waste risk.
Risk rules (configurable in `decision.py`, no magic numbers elsewhere):

```
< 5 kg = Low     5-15 kg = Medium     > 15 kg = High
```

## Decision engine (`decision.py`)

Decide donate vs dispose from predicted surplus, food quantity, expiry/safety and NGO capacity.
Minimum behaviour: suitable food with enough quantity and enough shelf life -> DONATE; unsafe/expired -> do not
donate; insufficient quantity -> dispose / not suitable; high-risk quantity -> warning rather than blind donation.
Conservative food-safety logic. Do not present it as a real food-safety decision. Return structured data:

```python
{"decision": "DONATE", "reason": "...", "risk": "LOW"}
```

## Blockchain (`blockchain.py`)

Educational simulation (NOT Ethereum/Bitcoin, no consensus or network). Each block has
`index, timestamp, data, previous_hash, hash` using SHA-256. Provide `Block`, `Blockchain`, `add_block()`,
`is_valid()`. Genesis block; deterministic hashing; each block linked to the previous; detect modified data and
broken previous-hash links. No sensitive information in block data.

## Database

SQLite `database.db`, auto-initialised if missing (including missing directories). Tables:
- `donations`: id, donor, food_type, quantity, expiry, ngo, status
- `ngos`: id, name, location, capacity
- `blocks`: index, timestamp, data, prev_hash, hash

Parameterised queries only (no string-built SQL). Seed several realistic demo NGOs.

## Donation flow

1. User predicts waste. 2. App determines risk. 3. App recommends donate/dispose. 4. If donating, user opens the
donation workflow. 5. User enters donor, food type, quantity, expiry, NGO. 6. Validate input. 7. Store the
donation in SQLite. 8. Create a blockchain block with the donation info. 9. Store the block in `blocks`.
10. Show confirmation. 11. Make the donation traceable from the blockchain page.
The donation record lives in SQLite; the chain stores a traceability representation (no sensitive data).

## QR code

Optional but preferred: a QR code per donation pointing to its traceability/verification route. If the package
causes environment problems, disable the feature gracefully; never let it break the application.

## Flask application (`app.py`) - required routes

| Route | Behaviour |
|---|---|
| `/` | Dashboard: title, prediction form, result, risk indicator, recommendation, basic statistics, blockchain status, recent donations |
| `/predict` | Validate input, run ML, compute risk, run decision engine, return/display results |
| `/donate` | Donation form + submission. Validate donor, food type, quantity, expiry, NGO. Reject invalid/expired donations. Create DB record and blockchain block |
| `/chain` | Show blocks: index, timestamp, donation info, previous hash, current hash, in an understandable visual form |
| `/verify` | Verify integrity. Clearly show "Blockchain Valid" or "Blockchain Tampered" and explain why |
| `/tamper` | DEMONSTRATION ONLY tamper mechanism labelled **Demo Tamper Test** so `/verify` detects it. Not a production feature |
| `/status` | JSON: model available, database available, donation count, block count, blockchain validity |

Also implemented: `/restore` (undo the demo tamper), `/reset` (fresh demo state), `/donation/<code>`,
`/donation/<code>/status`, `/qr/<code>.png`.

## Frontend

Templates (at least): `templates/index.html`, `templates/donate.html`, `templates/chain.html`.
Static: `static/css/style.css`, `static/js/app.js`. Bootstrap CDN, vanilla JS, Chart.js CDN.
Looks like an academic software project, not raw HTML. Dashboard cards: Predicted Waste, Waste Risk,
Donation Recommendation, Total Donations, Blockchain Status. Clean prediction form. Meaningful charts (predicted
waste, historical/synthetic waste trends, donation statistics) and no decorative ones. Readable blockchain view
(cards/timeline/table). Responsive on laptop and reasonably on mobile widths.

## Project structure

```
food_waste_project/
  app.py  train_model.py  blockchain.py  decision.py  requirements.txt  README.md  database.db
  data/waste_data.csv
  model/model.pkl
  templates/index.html  donate.html  chain.html
  static/css/style.css  static/js/app.js
  tests/test_blockchain.py  test_decision.py  test_app.py
```

Extra files are allowed where useful (config.py, ml.py, generate_data.py, more templates and tests, scripts/).

## Testing requirements

Automated tests (pytest) must cover:
- **Blockchain:** genesis block, adding blocks, hash generation, valid chain, tampered chain detection.
- **Decision engine:** low / medium / high risk, expired food, insufficient quantity, valid donation.
- **Flask:** homepage, prediction route, donation submission, chain page, verification, status endpoint.
- **ML:** dataset loads, training succeeds, model file exists, inference works, prediction is numeric,
  preprocessing behaves as expected.

Validation loop: implement -> run tests -> fix failures -> rerun. Run `python train_model.py`, then `pytest`,
then start Flask and test the running app over real HTTP (`/`, `/predict`, `/donate`, `/chain`, `/verify`,
`/status`); use headless-browser testing if available.

## End-to-end flow (must be performed and verified)

1. Start from a clean/usable database. 2. Load the trained model. 3. Open the dashboard. 4. Enter realistic
prediction data. 5. Prediction appears. 6. Risk is calculated. 7. Donation recommendation appears.
8. Submit a donation. 9. Donation exists in SQLite. 10. A blockchain block is created. 11. Open the blockchain
viewer. 12. Verify the chain. 13. Run the tamper demonstration. 14. Chain becomes invalid.
15. Restore/reinitialise the demo state. 16. Chain becomes valid again.

## Error handling

Never crash on normal invalid input. Handle: missing fields, non-numeric values, negative values, invalid dates,
expired food, missing model, empty database, corrupted blockchain data, missing dataset, missing directories.
Show useful user-facing errors, never Python stack traces. Use logging where appropriate.

## Security and code quality

Validate input; parameterised SQL; no hardcoded secrets; no `eval`; never execute user input; escape rendered
user content; centralised configuration; clear function names; comments on important ML/blockchain logic;
avoid duplicated logic. No authentication unless required by the core app.

## README requirements

Sections: project overview, architecture, features, installation (venv + activation for Windows and Unix,
`pip install -r requirements.txt`), dataset, model training (`python train_model.py`), running
(`python app.py`), testing (`pytest`), routes, ML methodology (Random Forest, Linear Regression, preprocessing,
MAE, R2), blockchain methodology (block, hash, previous hash, SHA-256, verification, tamper demo), limitations
(educational blockchain, possibly synthetic data, simplified food safety, demonstration model, no IoT hardware).

## Definition of Done (all must be true)

**Backend:** Flask starts; required routes work; SQLite initialises; donation workflow works.
**ML:** dataset exists; training works; at least two models evaluated; MAE and R2 shown; best model saved;
web prediction works.
**Decision engine:** risk classification works; donate/dispose recommendation works; expiry considered.
**Blockchain:** genesis block exists; donations create blocks; SHA-256 hashes work; verification works;
tampering detected.
**Frontend:** dashboard loads; forms work; results clearly displayed; blockchain viewable; status understandable;
UI presentable.
**Testing:** automated tests pass; end-to-end flow tested; Flask actually started; no obvious runtime errors.
**Documentation:** README exists; installation and run instructions work; architecture documented.

## Final report format (for the end of an autonomous session)

1. What was built. 2. Files created/modified. 3. ML results (MAE, R2). 4. Number of tests executed.
5. Test result. 6. How to start the app. 7. Local URL. 8. Genuine remaining limitations.
Do not claim anything was tested unless it was.

## Current verified status

Last verified: 163 pytest tests passing; 21/21 live HTTP end-to-end checks; 16/16 headless-browser checks.
Random Forest (MAE 2.100, R2 0.937) beat Linear Regression (MAE 2.417, R2 0.932) and is the saved model.
Run the app: `python app.py` then open http://127.0.0.1:5000
