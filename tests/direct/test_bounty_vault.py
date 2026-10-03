"""Direct-mode tests for BountyVault."""

import json

import pytest

CONTRACT = "contracts/bounty_vault.py"
ISSUE = "https://github.com/example/repo/issues/1"
PR = "https://github.com/example/repo/pull/9"
PAYOUT = "0x1111111111111111111111111111111111111111"
REWARD = 10**16


def deploy(direct_vm, direct_deploy, admin):
    direct_vm.sender = admin
    direct_vm.value = 0
    return direct_deploy(CONTRACT)


def merged_page():
    return (
        "This pull request was merged.\n"
        f"bounty payout: {PAYOUT}\n"
        "The fix closes the reported bug.\n"
    )


def mock_pages(direct_vm, pr_body, llm="FIXED"):
    direct_vm.mock_web(r"example/repo/pull/9", {"status": 200, "body": pr_body})
    direct_vm.mock_web(r"example/repo/issues/1", {"status": 200, "body": "Bug: the parser drops the last field."})
    direct_vm.mock_llm(r"merged pull request fixes", llm)

def open_bounty(contract, direct_vm, minutes=120):
    direct_vm.value = REWARD
    return contract.create_bounty(ISSUE, minutes)


def test_create_bounty_and_views(direct_vm, direct_deploy, direct_owner):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)

    assert bounty_id == "bounty_1"
    row = contract.get_bounty(bounty_id)
    assert row["found"] is True
    assert row["status"] == "open"
    assert int(row["reward_amount"]) == REWARD
    assert row["issue_url"] == ISSUE
    assert contract.get_total_bounties() == 1
    assert len(contract.get_open_bounties()) == 1


def test_create_rejects_bad_inputs(direct_vm, direct_deploy, direct_owner):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.value = REWARD
    with direct_vm.expect_revert("issue_url must be https"):
        contract.create_bounty("http://github.com/example/repo/issues/1", 120)
    with direct_vm.expect_revert("deadline_minutes must be between"):
        contract.create_bounty(ISSUE, 10)
    direct_vm.value = 1
    with direct_vm.expect_revert("reward too small"):
        contract.create_bounty(ISSUE, 120)


def test_unmerged_pr_stays_open(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.submit_fix(bounty_id, PR)

    mock_pages(direct_vm, "Pull request is still open.\nbounty payout: " + PAYOUT)
    contract.resolve(bounty_id)

    row = contract.get_bounty(bounty_id)
    assert row["status"] == "open"
    assert row["last_verdict"] == "NOT_FIXED"
    assert row["payout_address"] == ""


def test_merged_without_payout_line_is_void(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.submit_fix(bounty_id, PR)

    mock_pages(direct_vm, "This pull request was merged.\nNo payout line here.")
    contract.resolve(bounty_id)

    row = contract.get_bounty(bounty_id)
    assert row["status"] == "open"
    assert row["last_verdict"] == "VOID"


def test_fixed_verdict_marks_paid(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.submit_fix(bounty_id, PR)

    mock_pages(direct_vm, merged_page(), "FIXED")
    contract.resolve(bounty_id)

    row = contract.get_bounty(bounty_id)
    assert row["status"] == "fixed"
    assert row["last_verdict"] == "FIXED"
    assert row["payout_address"] == PAYOUT
    assert int(row["fee_paid"]) == (REWARD * 200) // 10000
    assert contract.get_open_bounties() == []


def test_not_fixed_llm_keeps_bounty_open(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)
    direct_vm.sender = direct_alice
    direct_vm.value = 0
    contract.submit_fix(bounty_id, PR)

    mock_pages(direct_vm, merged_page(), "NOT_FIXED")
    contract.resolve(bounty_id)

    row = contract.get_bounty(bounty_id)
    assert row["status"] == "open"
    assert row["last_verdict"] == "NOT_FIXED"


def test_cancel_only_creator_before_submission(direct_vm, direct_deploy, direct_owner, direct_alice):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    with direct_vm.expect_revert("only the creator can cancel"):
        contract.cancel_bounty(bounty_id)

    direct_vm.sender = direct_owner
    contract.cancel_bounty(bounty_id)
    assert contract.get_bounty(bounty_id)["status"] == "cancelled"

    bounty_id = open_bounty(contract, direct_vm)
    direct_vm.sender = direct_alice
    contract.submit_fix(bounty_id, PR)
    direct_vm.sender = direct_owner
    with direct_vm.expect_revert("a candidate fix has already been submitted"):
        contract.cancel_bounty(bounty_id)


def test_resolve_requires_a_submission(direct_vm, direct_deploy, direct_owner):
    contract = deploy(direct_vm, direct_deploy, direct_owner)
    bounty_id = open_bounty(contract, direct_vm)
    with direct_vm.expect_revert("no candidate PR has been submitted yet"):
        contract.resolve(bounty_id)