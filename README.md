# FoodTrace: ML Food Waste Prediction and Blockchain Donation Traceability

## Project overview

A locally runnable web application that
1. predicts how much food a kitchen will waste (kg) and classifies the risk as Low / Medium / High,
2. recommends whether surplus food should be donated or disposed of,
3. records each donation on a private, tamper-evident blockchain, and
4. lets anyone verify the records, with a built-in tampering demo.

It is software only. No IoT devices or special hardware are needed.

## Architecture

```
Browser app (HTML + CSS design system + vanilla JS, no CDN, works offline)
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
  tracking.py       donation lifecycle rules + delivery timeline builder
  features.py       derived model feature (prepared_per_meal), used inside the Pipeline
  icons.py          inline SVG icons
  templates/        base (sidebar), predict, overview, donations, donate, donation, track,
                    track_detail, verify, chain, model, status, error
  static/           css/style.css (design tokens), js/app.js (prediction), js/shell.js (sidebar)
  tests/            pytest suite (backend, tracking, access control, QR decoding, real browser)
  requirements-dev.txt   extra packages for the QR-decode and browser tests
  scripts/          build.js, py.js (npm wrappers), e2e_check.py (live HTTP check)
  data/waste_data.csv   model/model.pkl   database.db
```

## Features

- **Waste Prediction** (main screen): grouped inputs with inline validation, loading state, animated result
  with three distinct quantities: *predicted waste*, *estimated edible surplus* (your own "share still safe"
  judgement) and *suggested to offer* (limited by NGO capacity). Shows a 90% range from the model's past errors.
- **Overview**: totals, awaiting pickup, in transit, completed deliveries, items needing attention and the
  record-verification status. All numbers come from saved records; empty states are shown when there is no data.
- **Donations**: search and status filter; Track Donation action on every row.
- **Tracking timeline**: Registered -> Accepted by NGO -> Picked up -> In transit -> Delivered, plus
  Cancelled / Rejected / Delivery failed. Real timestamps, actor, notes, collected and delivered quantities.
  Invalid jumps are refused and a repeated click never adds a duplicate record.
- **QR codes** point to a public tracking page that shows no donor name and has no controls.
- **Verify Donation**: per-donation checks (own blocks, registration details, status history vs blockchain,
  allowed order, current status) plus whole-chain verification, with plain-language results.
- **Trust & Records**: how the record book works, what it does not do, records with "Advanced details",
  and demo tools (tamper test, restore, reset) that only work from the computer running the app.
- **Model information** (advanced): model comparison, baselines, feature importance, training ranges.
- **System status**: readable page for browsers; JSON for programs at `/status?format=json`.
- Risk bands: Low < 5 kg, Medium 5 to 15 kg, High > 15 kg (edit in `decision.py`).
- Donation rules: expired or nearly expired food is never donated, tiny quantities are disposed,
  High-risk quantities get a warning, NGO capacity limits the donated amount.

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

Compares three models, prints cross-validated and held-out test scores and saves the best one to
`model/model.pkl`.
The web app never trains at startup; it only loads this file. Retrain whenever the data changes.
(`generate_data.py` regenerates the synthetic CSV.)

## Running the application

```bash
python app.py
```

Open http://127.0.0.1:5000. Set `PORT`, `HOST` or `FLASK_DEBUG=1` environment variables if needed.

**Scanning the QR code with a phone.** By default the app listens on this computer only, so a QR code
that says `localhost` cannot open on a phone (the donation page warns you). To allow it, on the same Wi-Fi:

```bash
# Windows PowerShell
$env:HOST="0.0.0.0"; $env:PUBLIC_BASE_URL="http://YOUR-LAN-IP:5000"; python app.py
```

Other devices can then open the public tracking and verification pages, but creating donations, changing
status and the demo tools stay limited to the computer running the app (`FOOD_ALLOW_REMOTE_OPERATOR=1` lifts
that limit; there is no login, so only do it on a trusted network).
`database.db` is created and seeded automatically on first run.

Optional npm shortcuts (they only call the Python commands above): `npm run build`, `npm test`, `npm start`.

## Testing

```bash
pytest
```

The suite uses a throwaway database and covers the blockchain, decision engine, ML pipeline, lifecycle rules,
access control, QR decoding, every Flask route and a real-browser 15-step journey at desktop, tablet and phone
widths. The QR-decode and browser tests need the extras (they skip themselves if missing):

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
```

With the app running you can also run a live end-to-end check over HTTP (it resets the demo data, so use a
demo database):

```bash
python scripts/e2e_check.py
```

## Routes

| Route | Purpose |
|---|---|
| `GET /` | Waste Prediction (main screen) |
| `POST /predict` | JSON or form input -> predicted kg, range, edible surplus, risk, recommendation |
| `GET /overview` | Operational overview |
| `GET /donations` | Donation list (`?q=` search, `?status=` filter) |
| `GET/POST /donate` | New donation (this computer only) |
| `GET /donation/<code>` | Operator view: timeline, details, status actions, QR |
| `POST /donation/<code>/status` | Move to a status (`to=`), validated and applied once (this computer only) |
| `GET /track`, `GET /track/<code>` | Public tracking search and page (target of the QR code) |
| `GET /qr/<code>.png` | QR code for the public tracking URL |
| `GET /verify` | Verify a donation (`?code=DON-...`) and the whole chain (`?format=json`) |
| `GET /chain` | Trust & Records |
| `POST /tamper`, `/restore`, `/reset` | **Demo only** tools (this computer only) |
| `GET /model` | Advanced model information |
| `GET /status` | System status page; JSON with `?format=json` or without a browser Accept header |

## ML methodology

- **Preprocessing** (inside one scikit-learn `Pipeline`, so training and inference are identical):
  a derived `prepared_per_meal` feature, `day_of_week` one-hot encoded, numeric columns median-imputed and
  standardised, `special_event` encoded 0/1.
- **Candidates:** Linear Regression, Random Forest and Gradient Boosting (small grid search, run on training data only).
- **Honest evaluation:** 20% of rows are held out and never used for any choice. Models are compared by 5-fold
  cross-validated MAE on the other 80%, the winner is selected from that, and the held-out set is used once to
  report MAE, RMSE and R2 next to two naive baselines.
- **Uncertainty:** a 90% range is built from out-of-fold residuals, and its real coverage is measured on the
  held-out rows (about 90%).
- **Importance:** permutation importance on the held-out rows (how much the error grows when a feature is shuffled).
- **Guards:** implausible food-per-meal values are rejected; inputs outside the training range show a warning
  because tree models cannot extrapolate.
- **The default dataset is synthetic** (`generate_data.py`), so the scores show how well the model learns that
  pattern, not accuracy in a real kitchen.

## Blockchain methodology

- A **block** holds `index`, `timestamp`, `data`, `previous_hash` and `hash`.
- The **hash** is SHA-256 over those fields (keys sorted so it is deterministic).
- Each block stores the hash of the block before it (**previous hash**), forming a chain.
- **Verification** recomputes every hash and checks every link. Editing any stored block changes its
  recomputed hash so it no longer matches the stored one, which is detected.
- Donor names are not stored on the chain, only a short one-way reference. Full records stay in SQLite.
- **Tamper demonstration:** `/tamper` edits a block in the database without recomputing its hash, exactly
  as an attacker would. `/verify` then reports tampering and the block number. `/restore` undoes it.
- **Per-donation verification** also checks that the SQLite history and current status agree with the chain,
  so editing the database (not only a block) is caught.

## Limitations

- The blockchain is an **educational simulation**: single node, no network, no consensus, no mining.
  It detects tampering but cannot prevent someone with database access from rewriting the whole chain.
- The dataset is **synthetic**, so the model demonstrates the method and is not suitable for real operations.
- Donation and food-safety rules are **simplified** and are not a real food-safety assessment.
- The prediction is a **demonstration model**; accuracy on a real kitchen would need real data.
- No login or user roles. Write actions are limited to the computer running the app instead.
- Tracking statuses are entered by an operator; there is no NGO or driver app, so the "responsible actor"
  shown on the timeline is the role that normally performs the step, not an authenticated person.
- The "share still safe to donate" is the user's own judgement; the model does not assess food safety.
