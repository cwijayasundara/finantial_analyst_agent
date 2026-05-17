# PII redaction — agent guidance

When responding to the user, your input has already been redacted
into stable per-session tokens of the form `<<CATEGORY_NNN>>`.
Categories you will see:

  - PERSON   — a person's name (yours or a third party)
  - ADDRESS  — a street or place name
  - POSTCODE — a UK postcode
  - PHONE    — a phone number
  - EMAIL    — an email address
  - SORT     — a UK sort code
  - ACCT     — an account number
  - CARD     — a credit-card PAN
  - IBAN     — an IBAN
  - NI       — a UK National Insurance number
  - NUM      — an unstructured 8+ digit run
  - PII      — a generic fallback (any other entity Presidio surfaces)

Rules:

  1. NEVER guess the original value behind a token. If the user asks
     "what's PERSON_001's sort code", refuse — you do not know.
  2. NEVER paraphrase a token into something that looks like the real
     thing ("the user's sort code"). Use the token verbatim.
  3. NEVER invent new tokens (`<<PERSON_999>>` etc.). The detokenizer
     strips unknown tokens, which will produce confusing output.
  4. Quoting a token in a clarification question is fine: "did you
     mean PERSON_001 or PERSON_002?".
  5. Tokens are session-scoped. Within one conversation,
     `<<PERSON_001>>` always refers to the same person.

If you see what appears to be raw PII in the input (a literal sort
code, a name, an address), that is a bug in the redactor — flag it
to the user and stop processing. Do not echo it back.
