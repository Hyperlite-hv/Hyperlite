"""A single-use sign-in value is taken by one statement (GHSA-xp4w-9555-wj48): of two requests presenting it at
the same instant, one only gets it."""

import threading

from app.repositories.sqlite.identity import SqliteIdentityStore


def _race(take, n=8):
    barrier, got = threading.Barrier(n), []

    def one():
        barrier.wait()
        got.append(take())

    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return [g for g in got if g]


def test_a_hand_off_code_state_or_challenge_is_taken_once_whatever_the_race(database):
    store = SqliteIdentityStore()
    for i in range(20):
        store.put_handoff(f"code{i}", "ana", "2099-01-01", "2000-01-01")
        assert len(_race(lambda i=i: store.take_handoff(f"code{i}"))) == 1
        store.put_sso_state(f"st{i}", "n", "2099-01-01", "b", "2000-01-01")
        assert len(_race(lambda i=i: store.take_sso_state(f"st{i}"))) == 1
        store.store_challenge("ana", "auth", f"ch{i}", "rp", "https://o", "2099-01-01", "2000-01-01")
        assert len(_race(lambda i=i: store.take_challenge("ana", "auth", f"ch{i}", "rp", "https://o"))) == 1
    assert store.take_handoff("unknown") is None and store.take_sso_state("unknown") is None
