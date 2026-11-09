import pytest

from reminders_widget.icloud.cloudkit import CloudKit
from reminders_widget.icloud.errors import (
    LockConflict, OfflineError, RecordGone, SessionExpired, Transient, UnknownResponse,
)
from tests.fakes.fake_cloudkit import FakeCloudKit


def rec(name, **fields):
    return {"recordName": name, "recordType": "Reminder",
            "fields": {k: {"value": v} for k, v in fields.items()}}


def make(n=5):
    fake = FakeCloudKit([rec(f"r{i}", Completed=0) for i in range(n)])
    return fake, CloudKit(fake, "https://example.test/")


def update_op(record):
    return {"operationType": "update", "record": record}


def test_find_owner_returns_and_remembers():
    fake, ck = make()
    assert ck.find_owner() == fake.owner
    assert ck.find_owner() == fake.owner


def test_changes_none_returns_all_across_pages():
    fake, ck = make(5)
    records, token = ck.changes(None)
    assert sorted(r["recordName"] for r in records) == [f"r{i}" for i in range(5)]
    assert token.startswith("s")


def test_changes_token_returns_only_edited_record():
    fake, ck = make(5)
    _, token = ck.changes(None)
    fake.phone_edit("r2", Completed=1)
    records, token2 = ck.changes(token)
    assert [r["recordName"] for r in records] == ["r2"]
    assert records[0]["fields"]["Completed"]["value"] == 1
    assert token2 != token
    assert ck.changes(token2)[0] == []


def test_changes_pages_follow_more_coming():
    fake, ck = make(5)
    calls = []
    orig = fake.post
    fake.post = lambda url, body: (calls.append(url), orig(url, body))[1]
    ck.changes(None)
    assert len([u for u in calls if u.endswith("changes/zone")]) == 3


def test_phone_delete_sets_deleted():
    fake, ck = make(2)
    _, token = ck.changes(None)
    fake.phone_delete("r1")
    records, _ = ck.changes(token)
    assert records[0]["fields"]["Deleted"]["value"] == 1


def test_lookup_returns_record_and_missing_raises():
    fake, ck = make()
    assert ck.lookup("r1")["recordName"] == "r1"
    with pytest.raises(RecordGone):
        ck.lookup("nope")


def test_update_with_current_tag_merges_fields_and_issues_new_tag():
    fake, ck = make()
    cur = ck.lookup("r1")
    cur_fields = dict(cur["fields"])
    out = ck.modify(update_op({"recordName": "r1", "recordType": "Reminder",
                               "recordChangeTag": cur["recordChangeTag"],
                               "fields": {"Priority": {"value": 5}}}))
    assert out["recordChangeTag"] != cur["recordChangeTag"]
    after = ck.lookup("r1")
    assert after["fields"]["Priority"]["value"] == 5
    assert after["fields"]["Completed"] == cur_fields["Completed"]
    assert len(fake.writes) == 1


def test_pc_update_appears_in_later_changes():
    fake, ck = make()
    _, token = ck.changes(None)
    cur = ck.lookup("r1")
    ck.modify(update_op({"recordName": "r1", "recordChangeTag": cur["recordChangeTag"],
                         "fields": {"Priority": {"value": 1}}}))
    assert [r["recordName"] for r in ck.changes(token)[0]] == ["r1"]


def test_update_with_old_tag_raises_lock_conflict():
    fake, ck = make()
    old = ck.lookup("r1")["recordChangeTag"]
    fake.phone_edit("r1", Completed=1)
    with pytest.raises(LockConflict):
        ck.modify(update_op({"recordName": "r1", "recordChangeTag": old, "fields": {}}))


def test_create_then_duplicate_create_conflicts():
    fake, ck = make(0)
    op = {"operationType": "create", "record": rec("new", Completed=0)}
    out = ck.modify(op)
    assert out["recordChangeTag"]
    assert ck.lookup("new")["fields"]["Completed"]["value"] == 0
    with pytest.raises(LockConflict):
        ck.modify(op)


def test_update_missing_record_raises_record_gone():
    fake, ck = make(0)
    with pytest.raises(RecordGone):
        ck.modify(update_op({"recordName": "zz", "recordChangeTag": "t1", "fields": {}}))


def test_fail_next_consumed_one_per_modify():
    fake, ck = make()
    fake.fail_next = ["OP_LOCK_FAILURE"]
    cur = ck.lookup("r1")
    op = update_op({"recordName": "r1", "recordChangeTag": cur["recordChangeTag"],
                    "fields": {"Priority": {"value": 9}}})
    with pytest.raises(LockConflict):
        ck.modify(op)
    assert fake.fail_next == []
    ck.modify(op)
    assert ck.lookup("r1")["fields"]["Priority"]["value"] == 9


def test_session_invalid_raises_session_expired():
    fake, ck = make()
    fake.session_valid = False
    with pytest.raises(SessionExpired):
        ck.find_owner()


def test_offline_raises_offline_error():
    fake, ck = make()
    fake.offline = True
    with pytest.raises(OfflineError):
        ck.changes(None)


def test_http_404_raises_unknown_response():
    class Broken:
        def post(self, url, body):
            return 404, {}
    with pytest.raises(UnknownResponse):
        CloudKit(Broken(), "https://x").find_owner()


def test_unexpected_body_shape_raises_unknown_response():
    class Odd:
        def post(self, url, body):
            return 200, {"weird": 1}
    with pytest.raises(UnknownResponse):
        CloudKit(Odd(), "https://x").find_owner()


def test_unknown_server_error_code_raises_unknown_response():
    fake, ck = make()
    fake.fail_next = ["SOMETHING_ELSE"]
    with pytest.raises(UnknownResponse):
        ck.modify({"operationType": "create", "record": rec("n")})


def test_other_transport_exception_propagates():
    class Boom:
        def post(self, url, body):
            raise ValueError("boom")
    with pytest.raises(ValueError):
        CloudKit(Boom(), "https://x").find_owner()


def test_url_is_base_plus_container_path():
    seen = []
    class Spy:
        def post(self, url, body):
            seen.append(url)
            return 200, {"zones": [{"zoneID": {"zoneName": "Reminders", "ownerRecordName": "o"}}]}
    CloudKit(Spy(), "https://h.test/").find_owner()
    assert seen == ["https://h.test/database/1/com.apple.reminders/production/private/zones/list"]


# --- fix round 2 (task 10 review) -------------------------------------------

def test_lock_conflict_exposes_server_code():
    fake, ck = make()
    old = ck.lookup("r1")["recordChangeTag"]
    fake.phone_edit("r1", Completed=1)
    with pytest.raises(LockConflict) as stale:
        ck.modify(update_op({"recordName": "r1", "recordChangeTag": old, "fields": {}}))
    assert stale.value.code == "OP_LOCK_FAILURE"
    with pytest.raises(LockConflict) as dup:
        ck.modify({"operationType": "create", "record": rec("r1", Completed=0)})
    assert dup.value.code == "EXISTS"
    fake.duplicate_create_code = "CONFLICT"
    with pytest.raises(LockConflict) as dup:
        ck.modify({"operationType": "create", "record": rec("r1", Completed=0)})
    assert dup.value.code == "CONFLICT"


def _page(fake, **zone):
    fake._changes_zone = lambda body: {"zones": [{"zoneID": body["zones"][0]["zoneID"], **zone}]}


def test_changes_null_records_raises_unknown_response():
    fake, ck = make()
    _page(fake, records=None, syncToken="s1", moreComing=False)
    with pytest.raises(UnknownResponse):
        ck.changes(None)


def test_changes_more_coming_without_new_token_raises():
    fake, ck = make()
    _page(fake, records=[], syncToken="same", moreComing=True)
    with pytest.raises(UnknownResponse):
        ck.changes("same")
    with pytest.raises(UnknownResponse):
        ck.changes(None)  # first page answers "same", second page answers "same" again


def test_changes_stops_after_page_cap():
    from reminders_widget.icloud.cloudkit import MAX_CHANGE_PAGES
    fake, ck = make()
    calls = []

    def endless(body):
        calls.append(1)
        return {"zones": [{"zoneID": body["zones"][0]["zoneID"], "records": [],
                           "syncToken": f"s{len(calls)}", "moreComing": True}]}

    fake._changes_zone = endless
    with pytest.raises(UnknownResponse):
        ck.changes(None)
    assert len(calls) == MAX_CHANGE_PAGES == 1000


# --- final review fixes: throttling, transient and auth codes -----------------

class Answer:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def post(self, url, body):
        return self.status, self.body


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_http_429_and_5xx_are_transient(status):
    with pytest.raises(Transient) as err:
        CloudKit(Answer(status, {}), "https://x").find_owner()
    assert err.value.retry_after is None


def test_transient_status_with_non_object_body():
    with pytest.raises(Transient):
        CloudKit(Answer(503, None), "https://x").find_owner()


def test_http_transient_reads_retry_after_from_body():
    with pytest.raises(Transient) as err:
        CloudKit(Answer(503, {"serverErrorCode": "SERVICE_UNAVAILABLE", "retryAfter": 42}),
                 "https://x").find_owner()
    assert err.value.retry_after == 42.0


@pytest.mark.parametrize("code", ["THROTTLED", "TRY_AGAIN_LATER", "INTERNAL_ERROR",
                                  "SERVICE_UNAVAILABLE"])
def test_record_level_transient_codes(code):
    fake, ck = make()
    fake.fail_next = [code]
    with pytest.raises(Transient) as err:
        ck.modify({"operationType": "create", "record": rec("n")})
    assert err.value.retry_after is None


def test_record_level_transient_reads_retry_after():
    fake, ck = make()
    orig = fake._modify
    fake._modify = lambda body: {"records": [{"recordName": "n", "serverErrorCode": "THROTTLED",
                                              "retryAfter": 7.5}]}
    with pytest.raises(Transient) as err:
        ck.modify({"operationType": "create", "record": rec("n")})
    assert err.value.retry_after == 7.5
    fake._modify = orig


@pytest.mark.parametrize("value", ["soon", -1, True, float("nan"), float("inf"), None])
def test_odd_retry_after_is_ignored(value):
    with pytest.raises(Transient) as err:
        CloudKit(Answer(429, {"retryAfter": value}), "https://x").find_owner()
    assert err.value.retry_after is None


@pytest.mark.parametrize("code", ["THROTTLED", "TRY_AGAIN_LATER", "INTERNAL_ERROR",
                                  "SERVICE_UNAVAILABLE"])
def test_top_level_transient_code_on_ok_status(code):
    with pytest.raises(Transient):
        CloudKit(Answer(200, {"serverErrorCode": code}), "https://x").find_owner()


@pytest.mark.parametrize("code", ["AUTHENTICATION_REQUIRED", "AUTHENTICATION_FAILED"])
@pytest.mark.parametrize("status", [200, 400, 401, 403, 421, 500])
def test_authentication_codes_are_session_expired(code, status):
    with pytest.raises(SessionExpired):
        CloudKit(Answer(status, {"serverErrorCode": code}), "https://x").find_owner()


@pytest.mark.parametrize("code", ["AUTHENTICATION_REQUIRED", "AUTHENTICATION_FAILED"])
def test_record_level_authentication_codes_are_session_expired(code):
    fake, ck = make()
    fake.fail_next = [code]
    with pytest.raises(SessionExpired):
        ck.modify({"operationType": "create", "record": rec("n")})


def test_exists_on_create_is_lock_conflict_with_code():
    fake, ck = make()
    fake.fail_next = ["EXISTS"]
    with pytest.raises(LockConflict) as err:
        ck.modify({"operationType": "create", "record": rec("n")})
    assert err.value.code == "EXISTS"
