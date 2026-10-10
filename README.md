# ML Food Waste Prediction and Blockchain Donation Traceability

## Project overview

A locally runnable web application that
1. predicts how much food a kitchen will waste (kg) and classifies the risk as Low / Medium / High,
2. recommends whether surplus food should be donated or disposed of,
3. records each donation on a private, tamper-evident blockchain, and
4. lets anyone verify the records, with a built-in tampering demo.

It is software only. No IoT devices or special hardware are needed.

## Architecture

```
Browser dashboard (HTML / Bootstrap / Chart.js)
            |
      Flask app (app.py)
   |           |              |
 ml.py      decision.py    blockchain.py
 (validate, (risk bands,   (Block, Blockchain,
  predict)   donate rules)  SHA-256, verify)
   |                           |
 model/model.pkl          database.db (SQLite)
 (sklearn Pipeline)       donations, ngos, blocks, predictions
```

Flow: user input -> validation -> ML prediction -> risk + donation decision -> donation form ->
SQLite record + blockchain block -> traceability page, QR code and verification.

```
food_waste_project/
  app.py            Flask routes
  ml.py             input validation + inference
  train_model.py    training / model comparison (run manually)
  generate_data.py  synthetic dataset generator
  decision.py       risk thresholds + donate/dispose rules (all configurable here)
  blockchain.py     Block and Blockchain classes
  config.py         paths, constants
  templates/        index, donate, donation, chain, verify, error
  static/           css/style.css, js/app.js
  tests/            pytest suite
  scripts/          build.js, py.js (npm wrappers), e2e_check.py (live HTTP check)
  data/waste_data.csv   model/model.pkl   database.db
```

## Features

- Random Forest and Linear Regression compared on MAE and R2, best one saved and used
- Risk bands: Low < 5 kg, Medium 5 to 15 kg, High > 15 kg (edit in `decision.py`)
- Donation rules: expired or nearly expired food is never donated, tiny quantities are disposed,
  High-risk quantities get a warning, NGO capacity limits the donated amount
- Automatic NGO matching (smallest NGO that fits), seeded demo NGOs
- Donation lifecycle Pending -> Picked Up -> Delivered, every change is a NEW block
- Verify page showing per-block hash and link checks
- **Demo Tamper Test** and **Restore original** buttons to demonstrate tamper detection
- QR code per donation linking to its traceability page (optional, app works without it)
- Charts: weekday waste profile, your prediction history, feature importance, donations by status
- `/status` JSON health endpoint

## Installation

Python 3.10 or newer is required.

```bash
python -m venv venv
```

Activate it:

```bash
# Windows (PowerShell)
venv\Scripts\Activate.ps1
# Windows (cmd)
venv\Scripts\activate.bat
# macOS / Linux
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Dataset

`data/waste_data.csv` is a **synthetic** dataset of 2000 kitchen-days created by `generate_data.py`.
Waste depends on over-preparation, previous waste, events, weekends, heat, humidity,
understaffing and Friday/Saturday crowds, with non-linear effects and random noise,
so it cannot be computed from a single column.

Columns: `meals_served, food_prepared_kg, kitchen_staff, day_of_week (0=Mon..6=Sun), temperature,
humidity, special_event (0/1 or yes/no), previous_waste_kg, waste_kg`.

To use a real dataset (for example from Kaggle), put a CSV with these columns at `data/waste_data.csv`
and retrain. If the file is missing, training generates the synthetic one automatically.

## Model training

```bash
python train_model.py
```

Prints MAE and R2 for both models and saves the best one to `model/model.pkl`.
The web app never trains at startup; it only loads this file. Retrain whenever the data changes.
(`generate_data.py` regenerates the synthetic CSV.)

## Running the application

```bash
python app.py
```

Open http://127.0.0.1:5000. Set `PORT`, `HOST` or `FLASK_DEBUG=1` environment variables if needed.
`database.db` is created and seeded automatically on first run.

Optional npm shortcuts (they only call the Python commands above): `npm run build`, `npm test`, `npm start`.

## Testing

```bash
pytest
```

The suite uses a throwaway database and covers the blockchain, decision engine, ML pipeline and every Flask route.
With the app running you can also run a live end-to-end check over HTTP (it resets the demo data):

```bash
python scripts/e2e_check.py
```

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Dashboard: cards, prediction form, charts, recent donations |
| `POST /predict` | JSON or form input -> predicted kg, risk, recommendation |
| `GET/POST /donate` | Donation form, validation, SQLite record + blockchain block |
| `GET /donation/<code>` | Donation details, traceability trail, QR code |
| `POST /donation/<code>/status` | Advance status (adds a new block) |
| `GET /chain` | Readable blockchain viewer |
| `GET /verify` | Integrity check page (`?format=json` for JSON) |
| `POST /tamper` | **Demo only**: edit a stored block so verification fails |
| `POST /restore` | Undo the demo tamper |
| `POST /reset` | Clear demo data and start a fresh chain |
| `GET /status` | JSON: model, database, donation count, blocks, chain validity |

## ML methodology

- **Preprocessing** (inside one scikit-learn `Pipeline`, so training and inference are identical):
  `day_of_week` is one-hot encoded, numeric columns are median-imputed and standardised,
  `special_event` is encoded 0/1.
- **Linear Regression** is the simple baseline: waste as a weighted sum of the inputs.
- **Random Forest** averages many decision trees and can capture non-linear effects and interactions
  (for example heat and humidity together, or large events on busy days).
- **Evaluation** on a held-out 20% test set. **MAE** is the average error in kg (lower is better).
  **R2** is the share of variance explained (closer to 1 is better). The lower-MAE model is saved.
- Predictions are clipped to the range 0 to the food prepared.

## Blockchain methodology

- A **block** holds `index`, `timestamp`, `data`, `previous_hash` and `hash`.
- The **hash** is SHA-256 over those fields (keys sorted so it is deterministic).
- Each block stores the hash of the block before it (**previous hash**), forming a chain.
- **Verification** recomputes every hash and checks every link. Editing any stored block changes its
  recomputed hash so it no longer matches the stored one, which is detected.
- Donor names are not stored on the chain, only a short one-way reference. Full records stay in SQLite.
- **Tamper demonstration:** `/tamper` edits a block in the database without recomputing its hash, exactly
  as an attacker would. `/verify` then reports "Blockchain Tampered" and the block number. `/restore` undoes it.

## Limitations

- The blockchain is an **educational simulation**: single node, no network, no consensus, no mining.
  It detects tampering but cannot prevent someone with database access from rewriting the whole chain.
- The dataset is **synthetic**, so the model demonstrates the method and is not suitable for real operations.
- Donation and food-safety rules are **simplified** and are not a real food-safety assessment.
- The prediction is a **demonstration model**; accuracy on a real kitchen would need real data.
- No authentication or multi-user roles. It is designed for local demonstration.
- Charts and styling load Bootstrap and Chart.js from a CDN, so the browser needs internet access.
