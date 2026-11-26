---
name: acceptance-review
description: Inspect adjacent versions of a Chinese academic manuscript and collect the author's decisions using local audit evidence.
---

Use the available `audit_texts` MCP tool with the original and revised text from the same revision round. Keep the original version intact. If the tool is unavailable, report that the independent check could not run; do not manufacture an audit report.

`review.flagged` identifies candidate changes to inspect, not confirmed errors. Explain observed changes separately from rule hits and model judgments, using the returned positions and evidence. Model judgments may be wrong and do not establish understanding of the full argument.

Use the author's confirmed revision request when available. Point to the text in both versions and ask the author to resolve any material change to the claim and its scope. All changes in a new local audit start pending. Collect the author's decision before accepting, rejecting, or changing text.

For reproducibility, preserve the tool/schema/lexicon versions, actual `run.mode`, document hashes, and the original report. Keep manuscript text and decisions in the user's chosen private location. The server does not send content to a rewriting model or save files.
