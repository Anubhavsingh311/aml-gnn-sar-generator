# AML Transaction Scorer

Upload a bank transactions CSV and get a ranked list of the transfers most likely to be part of money laundering.

**Live app:** https://anubhavsingh311.github.io/anti-money-laundering-scorer/

Trained and tested on the synthetic IBM HI-Small dataset, so it is a demonstration, not a compliance tool.

## How to use it

1. Open the live app. The status chip in the top right says **Waking** while the free server starts up. This can take about a minute after it has been idle. It switches to **Ready: xgb_graph** when the model is available. You can pick your file while you wait.
2. Drop a CSV onto the upload box, or click it to choose one. The browser checks the file before anything is sent and lists any problems.
3. Choose how many rows to return (100 to 2,000) and press **Score transactions**.
4. Read the results. The table is sorted by risk score, highest first.
   - **Flagged** rows scored above 0.776. This is the balanced setting.
   - **High-recall** rows scored above 0.031. This is a wider net that catches more illicit transfers and also more false alarms. Every flagged row is also high-recall, and a row can be neither.
   - Use the tabs to switch between Flagged, High-recall and All returned. Click a column header to sort, use the search box to find an account or bank, and use **Export CSV** to save the current view.

To try the page without the server, open it with `?demo=1` at the end of the URL. It shows made-up results under a "DEMO DATA" banner.

### Input file

The CSV must follow the IBM HI-Small layout, with a header row and these columns in this order: Timestamp (`YYYY/MM/DD HH:MM`), From Bank, Account, To Bank, Account, Amount Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format. An extra "Is Laundering" column is ignored.

- Maximum 150,000 rows per upload.
- Blank or non-numeric amounts are blocked before upload, because the server would otherwise score them silently wrong.
- Scores depend on the surrounding transactions in the file, and the model was trained on about 18 days of data. Files covering less than 24 hours still work but the page warns you the scores are less reliable.

You can get the data from Kaggle ("IBM Transactions for Anti Money Laundering"). HI-Small has about 287,000 transactions per day, so the 150,000-row limit covers roughly 12 hours.

## Results

Measured on a held-out test period: the last 20% of transactions by time (8 to 18 Sep 2022), which is 1,015,564 transactions of which 1,797 are illicit. The dataset has 5,078,345 transactions across 515,088 accounts, and 0.10% are illicit.

| Model | PR-AUC | Precision | Recall | F1 |
|---|---|---|---|---|
| Rule baseline (amount and count) | 0.002 | 0.001 | 0.072 | 0.003 |
| XGBoost, one stage | 0.331 | 0.416 | 0.338 | 0.373 |
| GCN | 0.643 | 0.757 | 0.565 | 0.647 |
| GraphSAGE | 0.644 | 0.771 | 0.558 | 0.647 |
| **Two-stage XGBoost (served)** | **0.717** | **0.780** | **0.637** | **0.701** |

Precision, recall and F1 are at each model's best-F1 threshold, chosen on the validation period. PR-AUC is the headline number because only 0.1% of transactions are illicit. ROC-AUC for the served model is 0.990.

The two thresholds the app uses:

| Setting | Threshold | Recall | Precision | Share of transactions alerted |
|---|---|---|---|---|
| Flagged | 0.776 | 63.7% | 78.0% | 0.14% |
| High-recall | 0.031 | 87.2% | 9.9% | 1.56% |

At the High-recall setting about 9 in 10 alerts are false positives. That is the cost of catching 87% of illicit transfers.

The data also labels laundering rings (fan-out, fan-in, cycle, scatter-gather and so on). The test period contains 143 of them. At the Flagged threshold the served model flags at least half of the transactions in 88.8% of rings and at least one transaction in 99.3% of them. At the High-recall threshold it flags at least half of the transactions in 99.3% of rings.

### What did not work

- At the Flagged threshold the model misses the 80% targets we set for precision (78.0%), recall (63.7%) and F1 (70.1%).
- The test period has a higher illicit rate (0.18%) than the training period (0.08%), which makes precision look better than it would on a quieter file.
- GraphSAGE and GCN did not beat the two-stage XGBoost, and they need the whole transaction graph at once, so they are saved in the notebook but not served.

## How it works

1. **Stage 1** scores each transfer on its own from 58 features: how many transfers and how much money the sender and receiver moved in the previous hour, day and full history, time since their last activity, account age, amount ratios, hour of day, payment format and currency.
2. **Stage 2** re-scores each transfer using 13 features built from the stage-1 scores of other transfers touching the same sender and receiver (within 1 hour, 24 hours and the whole file). This is what lets it spot rings, where each single transfer looks ordinary.

Stage 2 looks at transactions before and after the one being scored, so this is a batch review tool. It cannot score a single transaction as it happens.

## Limits

- Trained on synthetic data only. Do not use it for real compliance decisions.
- Treat the output as a queue for a human to review, not as accusations.
- Batch scoring only, and scores change with the history included in the file. 
