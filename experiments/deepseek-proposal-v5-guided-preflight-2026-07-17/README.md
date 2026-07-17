# DeepSeek Proposal v5 guided preflight

The first Proposal v5 call returned the same four hypotheses as v4 because the
generator version was recorded in the hash but did not affect the prompt. This
preflight records the corrected, zero-cost configuration: the frozen execution
loop guidance is now included in the system prompt and therefore changes the
prompt and request hashes.

No DeepSeek call was made for this preflight. A new explicit one-call
authorization is required before generating the guided candidates.
