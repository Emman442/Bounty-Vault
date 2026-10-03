# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json
import re

MIN_REWARD_WEI = 10**15
MIN_DEADLINE_MINUTES = 60
MAX_DEADLINE_MINUTES = 129600
MAX_PAGE_CHARS = 4000

MERGE_MARKERS = (
    "successfully merged",
    "pull request was merged",
    "this pull request is merged",
    "state: merged",
    "status: merged",
)

PAYOUT_LINE_RE = re.compile(
    r"(?im)^\s*bounty\s*payout\s*:\s*(0x[a-fA-F0-9]{40})\s*$"
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


def _fetch(url: str):
    try:
        response = gl.nondet.web.get(url)
        text = response.body.decode("utf-8", errors="ignore").strip()
    except Exception:
        try:
            text = str(gl.nondet.web.render(url, mode="text")).strip()
        except Exception:
            return None
    if len(text) < 20:
        return None
    return text[:MAX_PAGE_CHARS]


def _judge(issue_url: str, pr_url: str) -> str:
    def leader() -> str:
        pr_text = _fetch(pr_url)
        if pr_text is None:
            return json.dumps({"payout_address": "", "verdict": "VOID"}, separators=(",", ":"))
        lowered = pr_text.lower()
        if not any(marker in lowered for marker in MERGE_MARKERS):
            return json.dumps({"payout_address": "", "verdict": "NOT_FIXED"}, separators=(",", ":"))
        match = PAYOUT_LINE_RE.search(pr_text)
        if not match:
            return json.dumps({"payout_address": "", "verdict": "VOID"}, separators=(",", ":"))
        payout_address = match.group(1)
        issue_text = _fetch(issue_url)
        prompt = (
            "You are judging whether a merged pull request fixes a reported issue. "
            "The merge is already confirmed. Reply with exactly one word: FIXED or NOT_FIXED.\n\n"
            "Issue page:\n" + (issue_text if issue_text else "(issue page could not be fetched)")
            + "\n\nPull request page:\n" + pr_text
        )
        raw = str(gl.nondet.exec_prompt(prompt)).strip().upper()
        verdict = "FIXED" if raw.startswith("FIXED") else "NOT_FIXED"
        return json.dumps(
            {"payout_address": payout_address, "verdict": verdict},
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
            "reward_amount": 0,
            "status": "open",
            "deadline_at": 0,
            "created_at": "",
            "submitted_pr_url": "",
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
        issue_url = issue_url.strip()
        if not issue_url.startswith("https://"):
            _fail("issue_url must be https")
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
        row["issue_url"] = issue_url
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
        pr_url = pr_url.strip()
        if not pr_url.startswith("https://"):
            _fail("pr_url must be https")
        row["submitted_pr_url"] = pr_url
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
        if row["submitted_pr_url"] == "":
            _fail("no candidate PR has been submitted yet")
        if _epoch(_now()) >= int(row["deadline_at"]):
            _fail("bounty deadline has passed, call reclaim_expired instead")

        packed = json.loads(_judge(row["issue_url"], row["submitted_pr_url"]))
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