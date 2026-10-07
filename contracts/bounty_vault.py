# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json
import re

MIN_REWARD_WEI = 10**15
MIN_DEADLINE_MINUTES = 60
MAX_DEADLINE_MINUTES = 129600
MAX_PAGE_CHARS = 4000

PAYOUT_LINE_RE = re.compile(
    r"(?im)^\s*bounty\s*payout\s*:\s*(0x[a-fA-F0-9]{40})\s*$"
)
ISSUE_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)$"
)
PR_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)$"
)


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


def _fail(msg: str):
    if hasattr(gl, "vm") and hasattr(gl.vm, "UserError"):
        raise gl.vm.UserError(msg)
    assert False, msg


def _addr_str(a) -> str:
    if hasattr(a, "as_hex"):
        v = a.as_hex
        if callable(v):
            v = v()
        return str(v)
    return str(a)


def _who() -> str:
    return _addr_str(gl.message.sender_address)


def _same(a, b) -> bool:
    return str(a).lower() == str(b).lower()


def _now() -> str:
    try:
        return str(gl.message_raw["datetime"])
    except Exception:
        return ""


def _msg_value() -> int:
    if not hasattr(gl.message, "value"):
        _fail("gl.message.value is not available in this SDK build")
    return int(gl.message.value)


def _pay(to: str, amount: int) -> None:
    if amount <= 0:
        return
    _Recipient(Address(to)).emit_transfer(value=u256(amount))


def _days_from_civil(y: int, m: int, d: int) -> int:
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    mp = m - 3 if m > 2 else m + 9
    doy = (153 * mp + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _epoch(ts: str) -> int:
    s = str(ts).strip().replace(" ", "T")
    try:
        date_part, clock_part = s[:19].split("T")
        y, mo, d = [int(x) for x in date_part.split("-")]
        h, mi, se = [int(x) for x in clock_part.split(":")]
        return _days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + se
    except Exception:
        _fail("timestamp format not recognised: " + s[:40])


def _parse_issue(url: str):
    match = ISSUE_RE.match(url.strip())
    if not match:
        _fail("issue_url must be https://github.com/owner/repo/issues/123")
    return match.group(1), match.group(2), match.group(3)


def _parse_pr(url: str):
    match = PR_RE.match(url.strip())
    if not match:
        _fail("pr_url must be https://github.com/owner/repo/pull/456")
    return match.group(1), match.group(2), match.group(3)


def _fetch(url: str):
    try:
        response = gl.nondet.web.get(url)
        text = response.body.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None
    if len(text) < 20:
        return None
    return text[:MAX_PAGE_CHARS]


def _api_json(url: str):
    text = _fetch(url)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _binds_issue(pr: dict, owner: str, repo: str, issue_number: str) -> bool:
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    full_name = str(base_repo.get("full_name", "")).lower()
    if full_name != (owner + "/" + repo).lower():
        return False
    body = str(pr.get("body") or "")
    issue_url = "https://github.com/" + owner + "/" + repo + "/issues/" + issue_number
    if issue_url.lower() in body.lower():
        return True
    return re.search(r"(?i)(?:fix(?:es|ed)?|close(?:s|d)?|resolve(?:s|d)?)\s+#%s\b" % issue_number, body) is not None


def _judge(owner: str, repo: str, issue_number: str, pr_number: str) -> str:
    def leader() -> str:
        empty = json.dumps({"payout_address": "", "verdict": "VOID"}, separators=(",", ":"))
        issue = _api_json(
            "https://api.github.com/repos/" + owner + "/" + repo + "/issues/" + issue_number
        )
        pr = _api_json(
            "https://api.github.com/repos/" + owner + "/" + repo + "/pulls/" + pr_number
        )
        if issue is None or pr is None:
            return empty
        if str(issue.get("number", "")) != issue_number or str(pr.get("number", "")) != pr_number:
            return empty
        if not _binds_issue(pr, owner, repo, issue_number):
            return empty
        if pr.get("merged") is not True:
            return json.dumps({"payout_address": "", "verdict": "NOT_FIXED"}, separators=(",", ":"))
        body = str(pr.get("body") or "")
        match = PAYOUT_LINE_RE.search(body)
        if not match:
            return empty
        files = _fetch(
            "https://api.github.com/repos/" + owner + "/" + repo + "/pulls/" + pr_number + "/files"
        )
        prompt = (
            "You are judging whether a merged pull request fixes a reported issue. "
            "The merge is already confirmed from GitHub's pull request record. "
            "Reply with exactly one word: FIXED or NOT_FIXED.\n\n"
            "Issue record:\n" + json.dumps({
                "title": issue.get("title", ""),
                "body": str(issue.get("body") or "")[:MAX_PAGE_CHARS],
            })
            + "\n\nPull request record:\n" + json.dumps({
                "title": pr.get("title", ""),
                "body": body[:MAX_PAGE_CHARS],
            })
            + "\n\nChanged files:\n" + (files if files else "(files could not be fetched)")
        )
        raw = str(gl.nondet.exec_prompt(prompt)).strip().upper()
        verdict = "FIXED" if raw.startswith("FIXED") else "NOT_FIXED"
        return json.dumps(
            {"payout_address": match.group(1), "verdict": verdict},
            separators=(",", ":"),
        )

    return gl.eq_principle.strict_eq(leader)


class BountyVault(gl.Contract):
    admin: str
    treasury: str
    protocol_fee_bps: u256
    bounty_counter: u256
    bounties_json: str

    def __init__(self):
        self.admin = _who()
        self.treasury = _who()
        self.protocol_fee_bps = u256(200)
        self.bounty_counter = u256(0)
        self.bounties_json = "{}"

    def _only_admin(self) -> None:
        if not _same(_who(), str(self.admin)):
            _fail("Only admin")

    def _load(self) -> dict:
        data = json.loads(str(self.bounties_json) or "{}")
        if not isinstance(data, dict):
            _fail("corrupt bounty storage")
        return data

    def _save(self, data: dict) -> None:
        self.bounties_json = json.dumps(data, sort_keys=True)

    def _empty(self) -> dict:
        return {
            "bounty_id": "",
            "creator": "",
            "issue_url": "",
            "owner": "",
            "repo": "",
            "issue_number": "",
            "reward_amount": 0,
            "status": "open",
            "deadline_at": 0,
            "created_at": "",
            "submitted_pr_url": "",
            "pr_number": "",
            "submitted_by": "",
            "submitted_at": "",
            "last_verdict": "",
            "last_checked_at": "",
            "resolved_at": "",
            "payout_address": "",
            "fee_paid": 0,
            "found": False,
        }

    @gl.public.write.payable
    def create_bounty(self, issue_url: str, deadline_minutes: int) -> str:
        owner, repo, issue_number = _parse_issue(issue_url)
        if deadline_minutes < MIN_DEADLINE_MINUTES or deadline_minutes > MAX_DEADLINE_MINUTES:
            _fail("deadline_minutes must be between 60 and 129600")
        reward = _msg_value()
        if reward < MIN_REWARD_WEI:
            _fail("reward too small")

        created = _now()
        pid = int(self.bounty_counter) + 1
        self.bounty_counter = u256(pid)
        bounty_id = "bounty_" + str(pid)

        data = self._load()
        row = self._empty()
        row["bounty_id"] = bounty_id
        row["creator"] = _who()
        row["issue_url"] = "https://github.com/" + owner + "/" + repo + "/issues/" + issue_number
        row["owner"] = owner
        row["repo"] = repo
        row["issue_number"] = issue_number
        row["reward_amount"] = reward
        row["deadline_at"] = _epoch(created) + deadline_minutes * 60
        row["created_at"] = created
        row["found"] = True
        data[bounty_id] = row
        self._save(data)
        return bounty_id

    @gl.public.write
    def submit_fix(self, bounty_id: str, pr_url: str) -> None:
        data = self._load()
        row = data.get(bounty_id)
        if row is None:
            _fail("unknown bounty_id")
        if row["status"] != "open":
            _fail("bounty is not open")
        if _epoch(_now()) >= int(row["deadline_at"]):
            _fail("bounty deadline has passed, call reclaim_expired instead")
        owner, repo, pr_number = _parse_pr(pr_url)
        if not _same(owner, row["owner"]) or not _same(repo, row["repo"]):
            _fail("pull request must belong to the bounty repository")
        row["submitted_pr_url"] = "https://github.com/" + owner + "/" + repo + "/pull/" + pr_number
        row["pr_number"] = pr_number
        row["submitted_by"] = _who()
        row["submitted_at"] = _now()
        row["last_verdict"] = ""
        data[bounty_id] = row
        self._save(data)

    @gl.public.write
    def resolve(self, bounty_id: str) -> None:
        data = self._load()
        row = data.get(bounty_id)
        if row is None:
            _fail("unknown bounty_id")
        if row["status"] != "open":
            _fail("bounty is not open")
        if row["submitted_pr_url"] == "" or row["pr_number"] == "":
            _fail("no candidate PR has been submitted yet")
        if _epoch(_now()) >= int(row["deadline_at"]):
            _fail("bounty deadline has passed, call reclaim_expired instead")

        packed = json.loads(_judge(row["owner"], row["repo"], row["issue_number"], row["pr_number"]))
        verdict = str(packed.get("verdict", "VOID"))
        if verdict not in ("FIXED", "NOT_FIXED", "VOID"):
            verdict = "VOID"
        payout_address = str(packed.get("payout_address", ""))
        row["last_verdict"] = verdict
        row["last_checked_at"] = _now()

        if verdict == "FIXED" and payout_address:
            reward = int(row["reward_amount"])
            fee = (reward * int(self.protocol_fee_bps)) // 10000
            payout = reward - fee
            row["status"] = "fixed"
            row["resolved_at"] = _now()
            row["payout_address"] = payout_address
            row["fee_paid"] = fee
            data[bounty_id] = row
            self._save(data)
            _pay(payout_address, payout)
            _pay(str(self.treasury), fee)
            return

        data[bounty_id] = row
        self._save(data)

    @gl.public.write
    def reclaim_expired(self, bounty_id: str) -> None:
        data = self._load()
        row = data.get(bounty_id)
        if row is None:
            _fail("unknown bounty_id")
        if row["status"] != "open":
            _fail("bounty is not open")
        if _epoch(_now()) < int(row["deadline_at"]):
            _fail("deadline has not passed yet")
        reward = int(row["reward_amount"])
        creator = row["creator"]
        row["status"] = "expired"
        data[bounty_id] = row
        self._save(data)
        _pay(creator, reward)

    @gl.public.write
    def cancel_bounty(self, bounty_id: str) -> None:
        caller = _who()
        data = self._load()
        row = data.get(bounty_id)
        if row is None:
            _fail("unknown bounty_id")
        if not _same(caller, row["creator"]):
            _fail("only the creator can cancel")
        if row["status"] != "open":
            _fail("bounty is not open")
        if row["submitted_pr_url"] != "":
            _fail("a candidate fix has already been submitted, cannot cancel")
        reward = int(row["reward_amount"])
        row["status"] = "cancelled"
        data[bounty_id] = row
        self._save(data)
        _pay(caller, reward)

    @gl.public.write
    def set_protocol_fee(self, new_fee_bps: int) -> None:
        self._only_admin()
        if new_fee_bps < 0 or new_fee_bps > 1000:
            _fail("fee must be between 0 and 1000 bps")
        self.protocol_fee_bps = u256(new_fee_bps)

    @gl.public.write
    def set_treasury(self, new_treasury: str) -> None:
        self._only_admin()
        if len(new_treasury) == 0:
            _fail("invalid treasury address")
        self.treasury = _addr_str(Address(new_treasury))

    @gl.public.view
    def get_bounty(self, bounty_id: str) -> dict:
        row = self._load().get(bounty_id)
        if row is None:
            empty = self._empty()
            empty["bounty_id"] = bounty_id
            return empty
        row["found"] = True
        return row

    @gl.public.view
    def get_all_bounties(self) -> list:
        data = self._load()
        out = []
        for n in range(1, int(self.bounty_counter) + 1):
            bid = "bounty_" + str(n)
            if bid in data:
                out.append(data[bid])
        return out

    @gl.public.view
    def get_open_bounties(self) -> list:
        data = self._load()
        out = []
        for n in range(1, int(self.bounty_counter) + 1):
            bid = "bounty_" + str(n)
            if bid in data and data[bid]["status"] == "open":
                out.append(data[bid])
        return out

    @gl.public.view
    def get_admin(self) -> str:
        return str(self.admin)

    @gl.public.view
    def get_treasury(self) -> str:
        return str(self.treasury)

    @gl.public.view
    def get_protocol_fee_bps(self) -> int:
        return int(self.protocol_fee_bps)

    @gl.public.view
    def get_total_bounties(self) -> int:
        return int(self.bounty_counter)