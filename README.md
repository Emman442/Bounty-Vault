# Bounty Vault

A merged-PR bounty escrow on GenLayer. A poster locks GEN against a public issue URL. Anyone can point the bounty at a candidate pull request. Validators independently confirm the PR was actually merged, read the solver's declared payout address from the PR itself, and judge whether the fix addresses the issue. Payment happens only on a FIXED verdict.

## How it works

A bounty is created against an issue URL with a reward and a deadline. Anyone can call `submit_fix` to point the bounty at a pull request they believe resolves it, this can be updated any time before resolution or the deadline.

`resolve` does the actual checking, in this order, and each step is deterministic until the last one.

1. Fetch the PR page. If it can't be fetched, the verdict is VOID.
2. Check the fetched text for a recognized merged marker. If none is found, the verdict is NOT_FIXED, no LLM call happens at all.
3. Look for one specific labeled line in the PR text, `Bounty payout: 0x...`. If it's missing or malformed, the verdict is VOID. This is a strict anchor, not a scan for anything hex-shaped, since a diff can contain unrelated hex strings.
4. Only once the PR is confirmed merged and carries a valid payout line does an LLM judge whether the merged changes actually address the issue, replying with one word, FIXED or NOT_FIXED.

Only the verdict and the extracted payout address go under validator consensus. Free-text reasoning is never compared or stored, since it can't reliably converge across independent fetches and isn't safety-critical, only the payout decision is.

NOT_FIXED and VOID leave the bounty open and retryable. FIXED is terminal and pays out in the same transaction.

## Rules

- Reward must be at least 0.001 GEN. Deadline must be between 1 hour and 90 days out.
- The payout address is read only from the PR page, never the issue page, since the poster controls the issue text and could otherwise plant a decoy address there.
- The poster can cancel and reclaim in full only while the bounty is still open and nobody has submitted a candidate PR yet. Once a solver has pointed at something, the bounty can no longer be pulled.
- Anyone can reclaim an expired bounty in full once its deadline has passed unresolved.
- A protocol fee (0 to 1000 bps, 0 to 10 percent) is taken from the reward only on a successful FIXED payout, never on a cancellation or an expiry refund.

## Contract

`contracts/bounty_vault.py`.

```text
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
```

### Writes

| Method | Who | What |
|---|---|---|
| `create_bounty(issue_url, deadline_minutes)` | payable, anyone | Lock a reward against an issue |
| `submit_fix(bounty_id, pr_url)` | anyone | Point the bounty at a candidate PR |
| `resolve(bounty_id)` | anyone | Check the current PR and settle or leave open |
| `reclaim_expired(bounty_id)` | anyone | Refund the poster once the deadline has passed |
| `cancel_bounty(bounty_id)` | creator only | Refund in full before any PR has been submitted |
| `set_protocol_fee` / `set_treasury` | admin | Fee must stay in 0 to 1000 bps |
| `debug_fetch(url, mode)` | anyone | Diagnostic, returns raw fetched page text |

### Views

`get_bounty`, `get_all_bounties`, `get_open_bounties`, `get_total_bounties`, `get_admin`, `get_treasury`, `get_protocol_fee_bps`, `get_contract_balance`.

### Status lifecycle

`open` stays open across any number of NOT_FIXED or VOID resolve attempts. It ends at `fixed` once a resolve returns FIXED with a valid payout address, or at `expired` if the deadline passes first, or at `cancelled` if the poster withdraws before any PR is submitted.

## Before deploying with real funds

Two things in this contract are explicitly unverified and flagged in the source.

`MERGE_MARKERS` is a best guess at the literal wording GitHub's PR page renders for a merged state. Call `debug_fetch` against a real, known-merged PR with `mode="text"`, read the output, and confirm one of the listed markers actually appears before trusting this with money. If none do, every bounty will sit stuck at NOT_FIXED even for genuinely merged fixes, which fails safe but is useless until fixed.

`get_contract_balance` relies on a `balance` property existing on the base contract class. If this SDK build doesn't expose it, deployment or schema load will raise a clear `AttributeError` naming it, at which point that one view needs swapping for whatever this build's actual equivalent is.

## Tests

Direct mode, with web and LLM mocked.

```bash
pytest tests/direct/test_bounty_vault.py -v
```