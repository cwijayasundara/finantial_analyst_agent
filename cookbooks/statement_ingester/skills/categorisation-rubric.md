# Merchant categorisation rubric

You are categorising a UK personal-finance transaction surface form into ONE
of the categories listed in the schema. Be terse and decisive. If you are
unsure, prefer `other` over guessing.

## Categories

- `groceries` — supermarkets, food shopping (Tesco, Sainsbury's, Aldi, Lidl, Asda, M&S Food, Waitrose, Co-op).
- `fuel` — petrol stations, EV charging.
- `dining` — restaurants, cafés, takeaways, food delivery (Uber Eats, Deliveroo, Just Eat).
- `subscription` — recurring digital services (Netflix, Spotify, gym, Amazon Prime).
- `income` — salary, refund, dividend, interest received (POSITIVE amounts only).
- `transfer` — between own accounts; standing order labels like "TFR", "TRANSFER", "PAYMENT TO/FROM".
- `utilities` — electricity, gas, water, broadband, mobile, council tax.
- `other` — everything else; use this rather than guessing.

## Heuristics

- "TESCO PETROL" / "BP" / "SHELL" / "ESSO" → fuel, NOT groceries.
- "TESCO STORES" / "TESCO METRO" → groceries.
- "AMAZON" by itself → other (could be subscription, gift, hardware).
- "AMAZON PRIME" → subscription.
- "PAYPAL *<merchant>" → categorise based on the merchant after the asterisk.

## Output discipline

- `merchant_canonical`: 1-3 words, Title Case (e.g. "Tesco", "Starbucks").
- `category`: one of the labels above, exact spelling.
- `confidence`: 0.0–1.0; below 0.6 means "use `other`".
- `reasoning_short`: ≤200 chars, ASCII + currency only.
